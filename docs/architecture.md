# Architecture

Adreoz Server Manager is a single-process, local-first web application. A
FastAPI backend serves both the JSON API and the static frontend, and stores
state in SQLite. No external services, message brokers, or build pipelines are
required for Phase 1.

## Goals and boundaries

Phase 1 provides the **control plane foundation**: identity, authorization,
host visibility, and administration. It does **not** create, start, or stop
Minecraft servers. This boundary is deliberate:

- No server records exist yet, so the UI never renders fake server counts.
- The dashboard reports only values it can actually read (host metrics,
  database counts, audit rows, session metadata).
- Future sections appear as a written roadmap, not as disabled mock controls.

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
│  Routers                    → health/setup/auth/users/…     │
│  Dependencies               → DB, auth, CSRF, RBAC, limits  │
│  Services                   → auth, users, dashboard, audit │
│  SQLAlchemy sessions        → SQLite                        │
│  StaticFiles                → frontend/ at "/"              │
└─────────────────────────────────────────────────────────────┘
```

### Backend modules

| Path | Responsibility |
| --- | --- |
| `app/main.py` | App factory, lifespan, exception handlers, router and static mounts |
| `app/core/config.py` | Pydantic settings, paths, database URI, trusted hosts, safe defaults |
| `app/core/security.py` | scrypt hashing, token generation, password policy |
| `app/core/rate_limit.py` | Thread-safe in-memory sliding-window limiter |
| `app/core/clock.py` | Naive UTC storage helpers and UTC JSON serialization |
| `app/core/roles.py` | `admin` / `user` role constants |
| `app/database/` | Declarative base, engine, session factory, reachability check |
| `app/models/` | `User`, `AuthSession`, `AuditLog`, `AppSetting` |
| `app/schemas/` | Pydantic request/response contracts |
| `app/api/deps.py` | `get_db`, session authentication, CSRF, admin guards, IP rate limits |
| `app/api/routes/` | HTTP endpoints grouped by concern |
| `app/services/` | Transactional business logic and audit recording |
| `app/middleware/security.py` | Response headers and cache policy |

### Frontend modules

| Path | Responsibility |
| --- | --- |
| `frontend/js/main.js` | Boot, setup/login routing, session lifecycle, hash router |
| `frontend/js/core/api.js` | JSON client, CSRF header injection, 401 handling |
| `frontend/js/core/store.js` | In-memory session state (no tokens in `localStorage`) |
| `frontend/js/core/dom.js` | Small DOM builder and event helper |
| `frontend/js/components/` | Layout, auth layout, forms, modal, toast, icons, brand |
| `frontend/js/views/` | Setup, login, dashboard, users, settings, account, 404 |
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

### Mutations

Every `POST`, `PATCH`, and `DELETE` under `/api` requires `X-CSRF-Token` to
match the session's token. Admin routes additionally require `role=admin`.
Failed authorization is audited or rejected before any business change.

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
- **Phase boundary.** Server management, file operations, backups, and console
  streaming belong to later phases and are intentionally absent.
