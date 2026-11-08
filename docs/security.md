# Security

Adreoz Server Manager is designed for a **trusted single host**: the panel,
SQLite database, and future Minecraft servers live on the same machine, and the
panel binds to loopback by default. The model below describes what Phase 1
actually enforces, plus the limits you must account for before network
exposure.

## Threat model

### In scope

- Remote attackers reaching the panel over HTTP/HTTPS.
- Malicious or compromised **non-admin** accounts attempting privilege
  escalation or cross-account actions.
- Credential stuffing and brute-force login attempts.
- Cross-site request forgery against a signed-in browser.
- Session theft through database disclosure, XSS, or cookie access.
- Accidental data exposure through errors, headers, cache, or logs.
- A local unprivileged process trying to reach the panel if it is exposed
  beyond loopback.

### Out of scope for Phase 1

- Host root compromise, kernel exploits, or physical access.
- Multi-tenant isolation on a shared host.
- Malicious Minecraft plugins or server jars (later phases must add sandboxing
  and resource limits).
- Denial of service at the network layer. The in-process limiter is not a
  substitute for a firewall or reverse-proxy rate limit.
- Email delivery, password reset by email, and multi-factor authentication.

## Authentication

| Control | Implementation |
| --- | --- |
| Password hashing | `hashlib.scrypt`, N=32768, r=8, p=1, 16-byte random salt, 32-byte output |
| Hash format | `scrypt$1$N$r$p$salt$digest`, self-describing for future upgrades |
| Comparison | `hmac.compare_digest` |
| Password policy | 10–128 characters, at least one letter and one number |
| Unknown-user timing | A fixed dummy scrypt hash is verified for unknown usernames |
| Setup locking | Transactional `setup_complete` marker; later calls return `409` |
| Session token | 32 random bytes, URL-safe; only SHA-256 digest stored |
| CSRF token | 32 random bytes per session, returned to the client |
| Cookie | `asm_session`, `HttpOnly`, `SameSite=Lax`, path `/`, `Secure` when configured |
| Session expiry | Database `expires_at`, checked on every request |
| Idle activity | `last_seen_at` refreshed at most once per minute |
| Password change | Revokes all sessions, then issues a new session and CSRF token |
| Login rotation | Revokes any session token already presented, then issues a fresh token |
| Other sessions | Explicit "sign out other sessions" action |
| Disabled accounts | Existing sessions deleted immediately on deactivation |

### Session rotation

Logging in revokes any session token already presented with the request before
issuing the new one, so a pre-authentication token can never survive a login.

Changing your own password deletes every session for that user, then creates a
fresh session for the current browser. Admin-initiated password resets revoke
the target user's sessions. Role changes do not force rotation, but the next
request reads the current role from the database, so a demotion takes effect
immediately.

The frontend only clears its local state after a successful logout or a `401`
from the logout endpoint. If the request fails at the network layer or returns
`403`/`5xx`, the server session may still be valid, so the UI stays signed in
and reports the failure instead of implying the user is safe.

### Why not JWT?

Opaque server-side sessions allow immediate revocation, keep authorization
state authoritative, and avoid signing-key management. The cost is a database
lookup per request, which is negligible for a local panel.

## Authorization

Two roles exist:

- `admin` — full panel access: users, settings, audit summaries, and all
  account actions.
- `user` — dashboard and personal account only.

Authorization is enforced by FastAPI dependencies (`require_admin`,
`require_admin_write`) on the server. Frontend route guards improve usability
but are never trusted for access decisions.

Built-in safeguards:

- An administrator cannot change their own role or deactivate themselves.
- The last active administrator cannot be demoted, disabled, or deleted.
- An administrator cannot reset their own password through the admin reset
  endpoint; they must use the account page, which requires the current
  password.
- Deleting a user cascades sessions and nulls audit actor references while
  preserving the username snapshot.
- Reserved usernames (`root`, `system`, `adreoz`, `asm`, `support`) are
  rejected.

## CSRF

Every state-changing endpoint (`POST`, `PATCH`, `DELETE`) requires the
`X-CSRF-Token` header to equal the session's CSRF token using a constant-time
comparison. `GET` and `HEAD` are read-only.

The cookie is `SameSite=Lax`, which blocks cross-site `POST` cookies in modern
browsers. The token check is the authoritative defence and does not rely on
`SameSite` support.

Login and setup are intentionally exempt from CSRF because no session exists
yet. Both are rate-limited, and setup is only available once.

## Request validation

Request bodies use Pydantic models with `extra="forbid"` (via `StrictRequest`),
so unknown fields such as an injected `role` or `is_active` are rejected with
`422` instead of being silently ignored. Password, username, email, role, and
session-lifetime values are validated in the schema and again in the service
layer where a database invariant matters.

All database access goes through the SQLAlchemy ORM with bound parameters; no
request value is interpolated into SQL text.

## Multi-server security

- Every server record carries an `owner_id` foreign key to `users.id`.
- `GET /api/servers` returns only the caller's servers unless the caller is
  an administrator, who sees all servers.
- `GET/PATCH/DELETE/start/stop/restart /api/servers/{id}` require the caller
  to be the server owner or an administrator; otherwise `403`.
- Server directories are created under `data/servers/` with `0700`
  permissions; `server.properties` is written with `0600`.
- Server names are validated against a safe pattern and checked for
  uniqueness (case-insensitive) to prevent path and identifier collisions.
- Server ports are validated (1024–65535) and checked for uniqueness across
  all servers.
- No `shell=True` is ever used; the Java executable and arguments are passed
  as a list to `asyncio.create_subprocess_exec`.
- Arbitrary executable commands are never accepted from API parameters.
- Server software JAR files are not downloaded or installed by the panel;
  the operator must place `server.jar` in the server directory before
  starting.

### Live console security

- WebSocket connections are authenticated via the session cookie; unauthenticated
  connections are rejected with `403`.
- Server ownership is enforced on every WebSocket connection; non-owners and
  non-administrators cannot connect to another user's server console.
- Server IDs are validated against the database before accepting a connection;
  frontend-supplied IDs are never trusted without authorization checks.
- Client commands are written directly to the server process stdin; no
  `shell=True` is ever used, preventing arbitrary host shell execution.
- Empty commands are ignored; commands to a stopped server return a system
  message.
- Log broadcasting is bounded per server (500-line buffer) to prevent
  unbounded memory growth.
- WebSocket disconnects are handled gracefully; the server cleans up client
  subscriptions on disconnect.

### File manager security

- All file operations are authenticated via the session cookie; unauthenticated
  connections are rejected with `401`.
- Server ownership is enforced on every file endpoint; non-owners and
  non-administrators cannot access another user's files.
- Server IDs are validated against the database before accepting any file
  operation; frontend-supplied IDs are never trusted without authorization
  checks.
- All file paths are resolved and validated before every operation. The resolved
  path must live inside the server's directory under `data/servers/`.
- Directory traversal (`..`) is rejected at the path validation layer.
- Symlinks are followed by `Path.resolve()`; if the target escapes the server
  directory, the request is rejected.
- File and folder names are validated against a safe pattern (letters, numbers,
  dots, underscores, hyphens only; no reserved names like `root`, `system`, etc.).
- File reads are limited to 16 MB; uploads are limited to 100 MB.
- Uploaded files are stored as-is and are not executed automatically.
- All file operations (create, read, write, rename, delete, upload) are audited
  with actor, action, and detail.

## Rate limiting

Login and setup share a thread-safe, in-memory sliding window keyed by
`bucket:client_ip`:

- Default: 10 attempts per 300 seconds.
- Rejected attempts are not recorded, so the window drains naturally.
- A successful login resets that IP's bucket.
- `429` responses include `Retry-After`.

**Limitation:** state is per process. Running multiple uvicorn workers or
multiple panel instances multiplies the effective limit. Use a reverse-proxy
rate limit as well if the panel is exposed.

Client IP resolution only trusts `X-Forwarded-For` / `X-Real-IP` when
`ASM_TRUST_PROXY_HEADERS=true`. Enable it **only** behind a proxy that
overwrites those headers; otherwise clients can spoof their address.

## HTTP hardening

Every response receives:

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none';
  base-uri 'self'; form-action 'self'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
```

Additional behaviour:

- Requests with a `Host` header outside `ASM_TRUSTED_HOSTS` are rejected with
  `400 Invalid host header` before reaching the application routes.
- `Strict-Transport-Security` is added when `ASM_COOKIE_SECURE=true`.
- `/api/*` responses use `Cache-Control: no-store`.
- HTML, JS, and CSS use `Cache-Control: no-cache`.
- The frontend uses **no inline scripts or styles**, so the strict CSP needs no
  nonce or `unsafe-inline`.
- Uvicorn's `Server` and `Date` headers are disabled.
- API documentation (`/docs`, `/openapi.json`) is disabled unless
  `ASM_ENVIRONMENT=development`.

## Error handling and logging

- Domain errors return a safe message and an appropriate status.
- Validation errors return only `loc`, `msg`, and `type`; request input is not
  echoed, because it may contain passwords.
- Unexpected exceptions are logged with a traceback and return a generic
  `500` message.
- API error responses are `no-store`.
- The audit trail records security-relevant events with actor, IP, user agent,
  action, detail, and UTC timestamp.

Audited actions include setup completion, successful and failed logins,
logout, password changes, other-session revocation, user creation/update/
deletion, admin password resets, and settings updates.

Audit rows are not exposed through a public endpoint. Admins see the eight most
recent entries on the dashboard; the full table remains in SQLite for local
review.

## Process and deployment

- The panel refuses to run as **root** unless `ASM_ALLOW_ROOT=true`.
- The default bind address is `127.0.0.1`.
- Runtime data directories are created and corrected to `0700`; a file-backed
  SQLite database is restricted to `0600` at startup and by `scripts/setup.sh`.
- `ASM_ENVIRONMENT=production` with `ASM_COOKIE_SECURE=false` logs a warning;
  the default remains HTTP-friendly for a local install, but a public
  deployment must enable secure cookies.
- Database access uses SQLAlchemy parameter binding; there is no raw SQL string
  interpolation and no `shell=True`.
- No Minecraft process is spawned in Phase 1, so command-injection surface is
  limited to request validation and ORM operations.
- The example systemd unit uses `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, `ProtectHome`, and a single writable data directory.
- Dependencies are pinned for reproducible installs. Review and update them
  deliberately.

## Before exposing the panel

1. Put it behind a TLS reverse proxy (Caddy, nginx, or similar).
2. Set `ASM_ENVIRONMENT=production`, `ASM_COOKIE_SECURE=true`, and
   `ASM_HOST=127.0.0.1`.
3. Set `ASM_TRUST_PROXY_HEADERS=true` only if the proxy sanitizes forwarded
   headers, and add the public hostname to `ASM_TRUSTED_HOSTS`.
4. Add a proxy-level rate limit and, ideally, an IP allowlist or VPN.
5. Back up the database and `.env` securely; keep `data/` at `0700`, the
   database at `0600`, and restrict ownership to the service user.
6. Monitor failed-login audit rows and panel logs.
7. Keep the host, Python runtime, and dependencies patched.

## Known limitations

- No multi-factor authentication or WebAuthn.
- No email-based password recovery. An administrator must reset passwords
  locally.
- No persistent account lockout; protection is rate limiting plus audit.
- In-memory limiter assumes a single process.
- No encrypted-at-rest database. Use full-disk encryption if required.
- No fine-grained per-server permissions yet; that belongs to a later phase.
- The frontend ships no third-party scripts, but browser extensions remain
  outside the panel's control.

## Reporting a vulnerability

This is a local project without a public security contact yet. If you publish
it, add a `SECURITY.md` with a private reporting address before accepting
external deployments.
