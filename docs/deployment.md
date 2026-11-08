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
sudo chmod 750 /opt/adreoz-server-manager/data
```

Avoid spaces in the deployment path; the example systemd unit assumes
`/opt/adreoz-server-manager`.

## 2. Install dependencies

```bash
cd /opt/adreoz-server-manager
sudo -u adreoz ./scripts/setup.sh
```

`setup.sh` creates `.venv`, installs pinned runtime and test dependencies, and
applies Alembic migrations to `data/asm.db`. If `uv` is installed it is used
automatically; otherwise the script falls back to `venv` + `pip`.

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
ASM_SESSION_LIFETIME_HOURS=24
ASM_LOGIN_ATTEMPT_LIMIT=10
ASM_LOGIN_ATTEMPT_WINDOW_SECONDS=300
```

Only enable `ASM_TRUST_PROXY_HEADERS` when the proxy overwrites forwarded
headers. Keep `ASM_ALLOW_ROOT=false`.

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

## 5. Reverse proxy and TLS

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

> WebSocket support is not required in Phase 1. Future console streaming will
> need `proxy_set_header Upgrade $http_upgrade` and
> `proxy_set_header Connection "upgrade"` (or Caddy's default upgrade
> handling).

## 6. Backups

The entire runtime state is in `data/`:

- `data/asm.db` — users, sessions, audit trail, settings.
- `data/servers/` — reserved for Phase 2 server files.
- `data/backups/` — reserved for Phase 4 backup artifacts.
- `data/logs/` — reserved for managed server logs.

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

## 7. Upgrades

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

## 8. Troubleshooting

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

## 9. Operational checklist

- [ ] Panel runs as a dedicated non-root user.
- [ ] `ASM_HOST=127.0.0.1`; only the proxy is reachable externally.
- [ ] TLS terminates at the proxy; `ASM_COOKIE_SECURE=true`.
- [ ] `ASM_ENVIRONMENT=production` (API docs disabled).
- [ ] `.env` is `0600` and not committed.
- [ ] `data/` is `0750` and backed up.
- [ ] Proxy rate limiting and, if possible, an IP allowlist/VPN are enabled.
- [ ] `journalctl` and failed-login audit entries are reviewed.
- [ ] Dependencies and the host are patched on a schedule.
