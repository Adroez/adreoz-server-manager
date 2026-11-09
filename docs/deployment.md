# Deployment

Phase 1 runs as one unprivileged process with SQLite. This guide covers a
long-lived Linux install behind a TLS reverse proxy. For a quick local trial,
`./scripts/setup.sh && ./scripts/run.sh` is enough.

## Prerequisites

- Linux with systemd (the example unit) or another process supervisor.
- Python 3.11 or newer, including `venv` and development headers if your
  distribution splits them.
- `sqlite3` CLI for backups (optional but recommended).
- A reverse proxy for TLS if the panel will leave loopback.
- A dedicated unprivileged user. Do **not** run the panel as root.

## 1. Create the service user and directories

```bash
sudo useradd --system --create-home --home-dir /opt/adreoz-server-manager \
  --shell /usr/sbin/nologin adreoz
sudo -u adreoz git clone <your-repository> /opt/adreoz-server-manager
sudo -u adreoz mkdir -p /opt/adreoz-server-manager/data/{servers,backups,logs}
sudo chmod 700 /opt/adreoz-server-manager/data \
  /opt/adreoz-server-manager/data/{servers,backups,logs}
```

Avoid spaces in the deployment path; the example systemd unit assumes
`/opt/adreoz-server-manager`.

## 2. Install dependencies

```bash
cd /opt/adreoz-server-manager
sudo -u adreoz ./scripts/setup.sh
```

`setup.sh` creates `.venv`, installs pinned runtime and test dependencies, and
applies Alembic migrations to `data/asm.db`. It also enforces `0700` on the
runtime directories and `0600` on the SQLite file. If `uv` is installed it is
used automatically; otherwise the script falls back to `venv` + `pip`.

## 3. Configure the panel

```bash
sudo -u adreoz cp .env.example .env
sudo -u adreoz chmod 600 .env
sudo -u adreoz "${EDITOR:-nano}" .env
```

Recommended production values:

```dotenv
ASM_HOST=127.0.0.1
ASM_PORT=8080
ASM_ENVIRONMENT=production
ASM_COOKIE_SECURE=true
ASM_TRUST_PROXY_HEADERS=true
ASM_TRUSTED_HOSTS=panel.example.com,127.0.0.1,localhost
ASM_SESSION_LIFETIME_HOURS=24
ASM_LOGIN_ATTEMPT_LIMIT=10
ASM_LOGIN_ATTEMPT_WINDOW_SECONDS=300
```

Only enable `ASM_TRUST_PROXY_HEADERS` when the proxy overwrites forwarded
headers. Keep `ASM_ALLOW_ROOT=false`. Add every hostname users type into the
browser to `ASM_TRUSTED_HOSTS`; requests with any other `Host` header return
`400`. Keep the loopback entries so local health checks still work.

Start once in the foreground to confirm migrations and setup work:

```bash
sudo -u adreoz ./scripts/run.sh
```

Then visit the panel through the proxy, or use an SSH tunnel for the first
setup:

```bash
ssh -L 8080:127.0.0.1:8080 you@host
```

Open <http://127.0.0.1:8080> and create the administrator. Stop the foreground
process with `Ctrl+C`.

## 4. Install the systemd service

```bash
sudo cp deploy/adreoz-server-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adreoz-server-manager
sudo systemctl status adreoz-server-manager
```

Check logs and health:

```bash
journalctl -u adreoz-server-manager -f
curl -fsS http://127.0.0.1:8080/api/health
```

The unit uses `ProtectSystem=strict` and allows writes only under
`/opt/adreoz-server-manager/data`. If you relocate `ASM_DATA_DIR`, update
`ReadWritePaths` accordingly. Future server process management will require
reviewing the hardening profile and child-process permissions.

## 5. Server software installation

The panel does **not** download or install Minecraft server software. Before
starting a server, place a valid `server.jar` in the server's directory under
`data/servers/<name>/`. The panel writes `server.properties` and `eula.txt`
automatically.

Supported software types: vanilla, paper, fabric, forge.

Server directories are created with `0700` permissions; `server.properties`
is written with `0600`.

## 6. WebSocket console deployment

The live console uses WebSockets (`WS /api/servers/{id}/console`). When
deploying behind a reverse proxy, the proxy must support WebSocket upgrades
and forward the `Connection` and `Upgrade` headers.

### Caddy with WebSocket support

```caddyfile
panel.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8080 {
        header_up X-Real-IP {remote_host}
        header_up Connection {>Connection}
        header_up Upgrade {>Upgrade}
    }
}
```

### nginx with WebSocket support

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

The session cookie is sent automatically by the browser over the WebSocket
connection. Ensure `ASM_COOKIE_SECURE=true` when using WSS (secure WebSocket)
behind a TLS reverse proxy.

## 7. File manager deployment

The file manager allows users to browse, create, edit, and delete files within
each server's directory under `data/servers/<name>/`. File uploads are limited
to 100 MB per file; file reads are limited to 16 MB.

Uploaded files are stored as-is and are not executed automatically. The panel
does not scan uploaded files for malware; administrators should exercise caution
when allowing users to upload executable files.

## 9. Reverse proxy and TLS

The panel must be reached over HTTPS before you set `ASM_COOKIE_SECURE=true`.
Both examples proxy to loopback and forward the real client IP.

### Caddy

```caddyfile
panel.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8080 {
        header_up X-Real-IP {remote_host}
    }
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
}
```

Caddy provisions TLS automatically. `X-Forwarded-For` is set by default.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name panel.example.com;

    ssl_certificate     /etc/letsencrypt/live/panel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.example.com/privkey.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Add a request-rate limit at the proxy as defence in depth. The application
limiter is per process and complements, but does not replace, proxy limits.

Both proxies must forward the original `Host` header (the nginx example uses
`proxy_set_header Host $host`) and that hostname must appear in
`ASM_TRUSTED_HOSTS`. A mismatch produces `400 Invalid host header` before the
request reaches the panel.

> WebSocket support is not required in Phase 1. Future console streaming will
> need `proxy_set_header Upgrade $http_upgrade` and
> `proxy_set_header Connection "upgrade"` (or Caddy's default upgrade
> handling).

## 10. Backups

The entire runtime state is in `data/`:

- `data/asm.db` — users, sessions, audit trail, backup metadata, settings.
- `data/servers/` — live Minecraft server directories (worlds, configs, JARs).
- `data/backups/` — managed `tar.gz` server backups created through the panel.
- `data/logs/` — reserved for managed server logs.

The directories are created `0700`; the database and backup archives are
`0600`.

### Managed server backups

The panel creates and restores per-server archives from **Backups** on a server
page:

- Backups are created only while the server is stopped, written to a temporary
  `.part` file, then atomically renamed.
- Restores validate every archive entry and replace the server directory through
  a staging swap; the source archive is kept.
- Automatic backups are configured per server (1, 6, 12, 24, 72, or 168 hours)
  and run in-process while the panel is running. Set
  `ASM_BACKUP_SCHEDULER_ENABLED=false` to disable the scheduler, and adjust
  `ASM_BACKUP_SCHEDULER_INTERVAL_SECONDS` (minimum 30) to change how often it
  checks.
- Retention keeps the newest 1–100 backups per server and removes older ones.
  Backups are not encrypted at rest, so protect `data/backups/` and the host.

Back up `data/backups/` itself if you need copies off-host. Managed backups are
not a substitute for the database backup below: restoring a server does not
restore users, sessions, or settings.

### Online SQLite backup

```bash
sudo -u adreoz sqlite3 /opt/adreoz-server-manager/data/asm.db \
  ".backup '/opt/adreoz-server-manager/data/backups/asm-$(date +%F-%H%M).db'"
```

SQLite's `.backup` command is safe while the panel is running.

### Cold backup

```bash
sudo systemctl stop adreoz-server-manager
sudo tar -C /opt/adreoz-server-manager -czf /secure/asm-backup-$(date +%F).tar.gz \
  data .env
sudo systemctl start adreoz-server-manager
```

Store backups outside the application directory, restrict permissions, and
test restores periodically.

### Restore

```bash
sudo systemctl stop adreoz-server-manager
sudo -u adreoz cp /secure/asm-backup-YYYY-MM-DD.db data/asm.db
sudo chown adreoz:adreoz data/asm.db
sudo systemctl start adreoz-server-manager
```

Restoring an old database also restores old users, sessions, and settings.
Revoke sessions after a suspected compromise by changing passwords or clearing
`auth_sessions`.

## 11. Upgrades

```bash
sudo systemctl stop adreoz-server-manager
sudo -u adreoz git -C /opt/adreoz-server-manager pull --ff-only
sudo -u adreoz /opt/adreoz-server-manager/scripts/setup.sh
sudo systemctl start adreoz-server-manager
```

`setup.sh` is idempotent and runs `alembic upgrade head`. Back up the database
before every upgrade.

Never run `alembic downgrade` on a production database without a verified
backup and a specific reason; Phase 1 ships one initial revision.

## 12. Troubleshooting

| Symptom | Check |
| --- | --- |
| Service exits immediately | `journalctl -u adreoz-server-manager -n 100`; often a missing `.env` variable or unwritable `data/` |
| `Refusing to run as root` | Run as the `adreoz` user, not root |
| `Database schema is missing` | Run `ROOT=/opt/adreoz-server-manager; cd "$ROOT/backend" && "$ROOT/.venv/bin/python" -m alembic upgrade head` |
| `502` from proxy | Panel not listening on the configured host/port; check `curl 127.0.0.1:8080/api/health` |
| Login says invalid credentials | Confirm caps lock and the 10+ character policy; inspect failed-login audit rows |
| `403 Invalid or missing CSRF token` | Stale page or expired session; reload and sign in again |
| `429 Too many attempts` | Wait for `Retry-After`; adjust `ASM_LOGIN_ATTEMPT_*` deliberately |
| Cookies not sent over HTTPS | Set `ASM_COOKIE_SECURE=true` and confirm the proxy forwards HTTPS |
| Login page loads but API fails | CSP blocks only external origins; verify `connect-src 'self'` was not altered and that the proxy does not rewrite paths |
| Wrong client IP in audit | Configure proxy headers and set `ASM_TRUST_PROXY_HEADERS=true` only for a trusted proxy |
| `400 Invalid host header` | Add the public hostname to `ASM_TRUSTED_HOSTS` and confirm the proxy forwards `Host` |
| `data/asm.db` is world-readable | Run `setup.sh` or restart the panel; both enforce `0600` |
| Server start fails with 400 | Confirm `server.jar` exists in the server directory under `data/servers/<name>/` |
| Server process exits immediately | Check server logs via `GET /api/servers/{id}/logs`; validate `server.properties` values |

## 13. Operational checklist

- [ ] Panel runs as a dedicated non-root user.
- [ ] `ASM_HOST=127.0.0.1`; only the proxy is reachable externally.
- [ ] TLS terminates at the proxy; `ASM_COOKIE_SECURE=true`.
- [ ] `ASM_ENVIRONMENT=production` (API docs disabled).
- [ ] `.env` is `0600` and not committed.
- [ ] `data/` and its subdirectories are `0700`; `data/asm.db` is `0600`.
- [ ] `ASM_TRUSTED_HOSTS` includes the public hostname only.
- [ ] `data/` is backed up.
- [ ] Server software JARs are placed manually in `data/servers/<name>/`; the panel does not download them.
- [ ] Proxy rate limiting and, if possible, an IP allowlist/VPN are enabled.
- [ ] `journalctl` and failed-login audit entries are reviewed.
- [ ] Dependencies and the host are patched on a schedule.
