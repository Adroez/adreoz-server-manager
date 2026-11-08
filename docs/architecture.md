# Architecture

Adreoz Server Manager is a single-process, local-first web application. A
FastAPI backend serves both the JSON API and the static frontend, and stores
state in SQLite. No external services, message brokers, or build pipelines are
required for Phase 1.

## Goals and boundaries

Phase 1 provides the **control plane foundation**: identity, authorization,
host visibility, and administration. Phase 2 adds **server lifecycle**:
create, configure, start, and stop Minecraft servers with per-user ownership.

- Phase 1 never renders fake server counts.
- Phase 2 provisions real server directories and manages real processes;
  status is never simulated.
- The dashboard reports only values it can actually read (host metrics,
  database counts, audit rows, session metadata, server status).

## Component overview

```text
┌─────────────────────────────────────────────────────────────┐
│ Browser                                                     │
│  index.html → ES modules → hash router → views/components   │
│  fetch("/api/…") with session cookie + X-CSRF-Token         │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP (loopback by default)
┌──────────────────────────────▼──────────────────────────────┐
│ FastAPI process                                             │
│                                                             │
│  SecurityHeadersMiddleware  → CSP, cache, framing policy    │
│  TrustedHostMiddleware      → Host-header allowlist         │
│  Routers                    → health/setup/auth/users/servers/… │
│  Dependencies               → DB, auth, CSRF, RBAC, limits  │
│  Services                   → auth, users, dashboard, servers │
│  SQLAlchemy sessions        → SQLite                        │
│  StaticFiles                → frontend/ at "/"              │
│  ProcessManager             → async server subprocesses     │
└─────────────────────────────────────────────────────────────┘
```

### Backend modules

| Path | Responsibility |
| --- | --- |
| `app/main.py` | App factory, lifespan, exception handlers, router and static mounts |
| `app/core/config.py` | Pydantic settings, paths, database URI, trusted hosts, safe defaults |
| `app/core/process.py` | Async server subprocess management (no `shell=True`) |
| `app/core/security.py` | scrypt hashing, token generation, password policy |
| `app/core/rate_limit.py` | Thread-safe in-memory sliding-window limiter |
| `app/core/clock.py` | Naive UTC storage helpers and UTC JSON serialization |
| `app/core/roles.py` | `admin` / `user` role constants |
| `app/database/` | Declarative base, engine, session factory, reachability check |
| `app/models/` | `User`, `AuthSession`, `AuditLog`, `AppSetting` |
| `app/schemas/` | Pydantic request/response contracts |
| `app/api/deps.py` | `get_db`, session authentication, CSRF, admin guards, IP rate limits |
| `app/api/routes/` | HTTP endpoints grouped by concern |
| `app/api/routes/servers.py` | Server CRUD, lifecycle, logs |
| `app/services/` | Transactional business logic and audit recording |
| `app/services/servers.py` | Server CRUD, ownership, lifecycle, process management |
| `app/middleware/security.py` | Response headers and cache policy |

### Frontend modules

| Path | Responsibility |
| --- | --- |
| `frontend/js/main.js` | Boot, setup/login routing, session lifecycle, hash router |
| `frontend/js/core/api.js` | JSON client, CSRF header injection, 401 handling |
| `frontend/js/core/store.js` | In-memory session state (no tokens in `localStorage`) |
| `frontend/js/core/dom.js` | Small DOM builder and event helper |
| `frontend/js/components/` | Layout, auth layout, forms, modal, toast, icons, brand |
| `frontend/js/views/servers.js` | Server list page |
| `frontend/js/views/server-create.js` | Create server form |
| `frontend/js/views/server-detail.js` | Server details and controls |
| `frontend/js/views/` | Setup, login, dashboard, users, settings, account, servers, 404 |
| `frontend/css/` | Design tokens, base styles, components, layout, view styles |

The frontend is a hash-routed SPA. It reuses one application shell per signed-in
session and recreates the shell when the session changes. Permissions are
mirrored in navigation for usability, but **every** protected operation is
authorized again by the backend.

## Data model

```text
users
  id                 integer PK
  username           text (display form)
  username_normalized text UNIQUE (case-insensitive key)
  email              text UNIQUE NULL
  password_hash      text (versioned scrypt string)
  role               text (admin | user)
  is_active          boolean
  created_at         datetime
  updated_at         datetime
  last_login_at      datetime NULL

auth_sessions
  id            integer PK
  user_id       FK users.id ON DELETE CASCADE
  token_hash    text UNIQUE (SHA-256 of the cookie value)
  csrf_token    text
  created_at    datetime
  expires_at    datetime (indexed)
  last_seen_at  datetime
  ip_address    text NULL
  user_agent    text NULL

audit_logs
  id              integer PK
  actor_user_id   FK users.id ON DELETE SET NULL
  actor_username  text (snapshot, survives deletion)
  action          text (indexed)
  detail          text NULL
  ip_address      text NULL
  user_agent      text NULL
  created_at      datetime (indexed)

app_settings
  key         text PK
  value       text
  updated_at  datetime

servers
  id            integer PK
  owner_id      integer FK users.id ON DELETE CASCADE
  unique_id     text UNIQUE (UUID v4)
  name          text UNIQUE (display form)
  software_type text (vanilla | paper | fabric | forge)
  version       text
  directory     text (absolute path, inside data/servers/)
  port          integer
  max_players   integer
  online_mode   boolean
  difficulty    text (peaceful | easy | normal | hard)
  gamemode      text (survival | creative | adventure | spectator)
  view_distance integer
  motd          text
  status        text (stopped | starting | running | stopping | error)
  process_id    integer nullable
  created_at    datetime
  updated_at    datetime
  started_at    datetime nullable
  stopped_at    datetime nullable

server_logs
  id        integer PK
  server_id integer FK servers.id ON DELETE CASCADE
  stream    text (stdout | stderr)
  line      text
  created_at datetime
```

### Why these shapes

- **Normalized username plus display username.** SQLite comparisons can be
  case-sensitive depending on collation. A stored lowercase key guarantees
  consistent uniqueness and lookup.
- **Token digest, not token.** A database disclosure exposes hashes that cannot
  be replayed as cookies.
- **Audit snapshot.** Deleting a user nulls the foreign key but preserves the
  username so historical entries stay readable.
- **Setup marker in `app_settings`.** `setup_complete` is written in the same
  transaction as the first administrator. Deleting users later does not reopen
  public setup.
- **Naive UTC in SQLite.** SQLite does not round-trip timezone information;
  storage uses naive UTC and API serialization adds `Z`.

## Request lifecycle

### First run

1. Browser loads `/` from static files and calls `GET /api/setup/status`.
2. With `setup_required=true`, the setup wizard is shown.
3. `POST /api/setup` is rate-limited, validated, hashes the password, creates
   the administrator, writes `setup_complete`, creates a session, and sets the
   cookie.
4. Later setup attempts return `409 Conflict`.

### Authentication

1. `POST /api/auth/login` is rate-limited per client IP.
2. Unknown usernames still perform a dummy scrypt verification to reduce
   timing differences.
3. On success, any session already presented with the request is revoked, so a
   pre-authentication token cannot survive login.
4. A random 32-byte token is generated. Only its SHA-256 digest is stored, along
   with a random CSRF token and expiry.
5. The cookie is `HttpOnly`, `SameSite=Lax`, path `/`, and `Secure` only when
   configured.
6. Every protected request resolves the cookie to a session, checks expiry and
   account state, and refreshes `last_seen_at` at most once per minute.

### Server lifecycle

1. `POST /api/servers` creates a server record, initializes its directory
   under `data/servers/<name>/`, and writes `server.properties` + `eula.txt`.
   Ownership is recorded as `owner_id = actor.id`.
2. `POST /api/servers/{id}/start` launches `java -jar server.jar nogui` as an
   asyncio subprocess. Output is captured line-by-line into `server_logs`.
   Only `java` and its arguments are passed — never `shell=True`.
3. `POST /api/servers/{id}/stop` sends SIGTERM, then SIGKILL after a timeout.
4. `POST /api/servers/{id}/restart` stops then starts.
5. `DELETE /api/servers/{id}` removes the server record and its directory
   (only when stopped).
6. `GET /api/servers/{id}/logs` returns captured output lines.
7. Every endpoint enforces ownership: normal users see only their own servers;
   admins see all.

### Lifecycle state machine

```
stopped ──start──> starting ──java running──> running
  ▲                  │                          │
  │                  │ process exited (0)       │ process exited (!=0)
  │                  ▼                          ▼
  └───────────────── stopped ◄───────────────── error
                         ▲
                         │ stop (SIGTERM→SIGKILL)
                         └──────────────────── stopping
```

Ownership and admin rules:

- `POST /api/servers` — any authenticated user can create; owner is set to caller.
- `GET /api/servers` — users see their own; admins see all.
- `GET/PATCH/DELETE/start/stop/restart /api/servers/{id}` — owner or admin only.
- Configuration changes (`PATCH`) require `status = stopped`.
### Live console

`WS /api/servers/{id}/console` provides real-time console access:

1. The client connects via WebSocket with the session cookie (sent automatically
   by browsers). The cookie is validated to recover the authenticated user.
2. Server ownership is enforced: the caller must own the server or be an
   administrator; otherwise the connection is rejected with `403`.
3. On successful connection, the server sends the recent log history (bounded
   buffer, 500 lines) so the client sees immediate context.
4. Live stdout/stderr lines from the server process are broadcast to all
   connected WebSocket clients for that server.
5. Client text messages are written directly to the server process stdin —
   never executed through a shell (`shell=True` is never used).
6. Empty commands are ignored. Commands sent to a stopped server receive a
   system message.
7. Client disconnects are handled gracefully; the server attempts automatic
   reconnection with exponential backoff.
8. Log buffering is bounded per server to prevent unbounded memory growth.


### File Manager

`GET/POST/PATCH/DELETE /api/servers/{id}/files` provides file management for
each server's directory:

1. All file operations are authenticated via the session cookie and enforce
   server ownership (admin access all servers).
2. Server directory paths are resolved and validated before every operation.
   The resolved path must live inside the server's directory under
   `data/servers/`. Directory traversal (`..`) and symlink escapes are
   rejected.
3. File and folder names are validated against a safe pattern (letters,
   numbers, dots, underscores, hyphens only; no reserved names).
4. File reads are limited to 16 MB; uploads are limited to 100 MB.
5. Uploaded files are stored as-is and are not executed automatically.
6. All file operations are audited with actor, action, and detail.

### Configuration Editor

`GET/PATCH /api/servers/{id}/config/properties` provides editing of
`server.properties`:

1. Configuration is authenticated and ownership-enforced like all server
   operations.
2. `server.properties` is parsed line-by-line, preserving comments, blank
   lines, and unknown properties not included in the update.
3. All values are validated before writing: booleans (true/false), enums
   (difficulty, gamemode, etc.), and integers with ranges (port, max-players,
   view-distance, etc.).
4. Changes require a server restart to take effect; the UI warns users
   accordingly.
5. All configuration changes are audited.


- Deletion (`DELETE`) requires `status = stopped`.

### Errors

- Expected domain failures use a `DomainError` with a safe message and status.
- Validation failures return `422` with only location/message/type fields.
- Unexpected exceptions are logged server-side and return a generic `500`.
- API responses carry `Cache-Control: no-store`.

## Startup and migrations

`python -m app` performs these steps:

1. Load settings and create data directories.
2. Refuse to run as root unless `ASM_ALLOW_ROOT=true`.
3. Run `alembic upgrade head` against the configured database.
4. Start uvicorn with `server_header=False` and `date_header=False`.

On application startup, the lifespan handler primes psutil, records the panel
start time, verifies database reachability and schema presence, enforces
owner-only data/database permissions, and purges expired sessions. It does not
create tables implicitly; migrations remain the single schema authority.

Requests whose `Host` header is outside `ASM_TRUSTED_HOSTS` are rejected by
Starlette's `TrustedHostMiddleware` before routing.

## Testing strategy

- Tests use a temporary SQLite file and a real `TestClient`, not mocks of the
  ORM.
- Migrations are exercised on a fresh database to catch drift between model
  metadata and Alembic revisions.
- Security tests assert headers, CSRF rejection, RBAC denial, setup locking,
  rate limiting, password/session rotation, login-time token replacement,
  trusted-host rejection, strict request bodies, owner-only permissions, and
  secure-cookie/HSTS behavior.
- Server tests assert CRUD, ownership enforcement, unauthorized access, invalid
  configuration, lifecycle state transitions, and process failure handling.
- File manager tests assert directory traversal rejection, symlink escape
  prevention, cross-user denial, file size limits, and invalid filenames.
- Config editor tests assert property parsing, unknown property preservation,
  and invalid value rejection.
- Dashboard tests assert response shape and plausible live values; they do not
  assert exact CPU or memory numbers.
- Frontend format helpers are unit-tested with Node's built-in test runner.

## Known trade-offs

- **Single process.** The rate limiter and session cleanup assumptions target
  one uvicorn worker. Multi-worker deployments would need a shared limiter.
- **SQLite.** Ideal for a personal or small-team host. Concurrent writes and
  multi-host access are out of scope.
- **No build step.** ES modules and plain CSS keep deployment simple, at the
  cost of no bundling or TypeScript.
- **Loopback default.** Network exposure requires an explicit configuration
  change and a TLS proxy.
- **Phase boundary.** Server management and console streaming belong to
  Phase 2 and Phase 3 respectively and are now implemented. File operations
  (Phase 4) are also implemented.
