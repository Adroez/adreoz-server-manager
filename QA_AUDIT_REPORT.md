# Adreoz Server Manager — Final QA Audit (Phases 1–5)

**Date:** 2026-11-09  
**Scope:** Pre-Phase-6 refinement and quality audit  
**Branch:** `master`  
**HEAD:** `7b10400` (`Phase 5.2: Final Reliability Fixes`)  
**Application:** `http://127.0.0.1:8080`

---

## 1. Scope and Constraints

This audit covered the existing Phases 1–5 implementation only. Phase 6 (Java
runtime/version management) was intentionally not started.

The audit followed these constraints:

- Fix only reproduced or source-confirmed defects.
- Preserve the existing architecture, UI, CSRF, RBAC, and backup-safety model.
- No real Minecraft server was launched.
- No real backup was restored.
- No database reset, account/password/role change, or permission change was performed.
- Browser QA used the existing authenticated session and real system Chromium.
  Logout was tested last; no credentials were assumed or re-entered.

## 2. Environment and Tooling

| Item | Value |
| --- | --- |
| OS | Ryoku Linux (Arch-based) |
| Browser QA | System Chromium via Playwright MCP (desktop/mobile sweeps) |
| Chrome DevTools | System Chromium (`/usr/bin/chromium` → `/usr/lib/chromium/chromium` 156.0.8078.79) via `chrome-devtools-mcp` |
| Backend | FastAPI + SQLite, local Uvicorn |
| Backend tests | `../.venv/bin/python -m pytest` from `backend/` |
| Frontend tests | `./scripts/test.sh` |
| Dev server | PID 115820, log `/tmp/opencode/asm-dev.log` |
| Fixture | Server `test-server` (id 1), empty directory, no `server.jar` |

## 3. Confirmed Defects Fixed

| Area | Defect | Fix |
| --- | --- | --- |
| Server lifecycle | Launched processes were not monitored; DB status did not update on exit; start/stop/restart did not await lifecycle work | Added `_monitor_process()`; made lifecycle services and routes async; monitor updates status and clears `process_id` |
| Server config | `propertiesContainer` accessed before initialization, producing a blank page | Moved state initialization before `render()`; validation now loads before properties |
| Server console | `statusNode` TDZ error; status dot in wrong element; double-escaped log text; stale header actions; WebSocket reconnected after navigation | Rebuilt status badge per render; removed double escaping; per-render header actions; disposal and reconnect guards; route cleanup hook |
| Server detail | `changeStatus()` referenced undefined `data`; status-dot class was overwritten | Use the outer `server` object; compose the class string correctly |
| File manager | File links passed the wrong arguments; upload used `FormData` against a raw-body endpoint, omitted CSRF, and targeted the directory instead of the file; hidden input was not in the DOM | Navigate by server/path; send raw file bytes with CSRF and correct target path; append the hidden input; support `?path=` |
| File editor | Read used `path` instead of the backend's `fp`; back link returned to the file itself | Use `fp`; return to the containing folder |
| Backups | Server ID was parsed from the wrong hash segment, breaking the page | Use `parts[2]` |
| Servers list | Status-dot class was overwritten by a duplicate `class` attribute | Compose the class string correctly |
| Config service | Duplicate `hardcore` key in `ALLOWED_VALUES` | Removed the duplicate |
| Icons | Invalid unquoted `edit-2` object key | Quoted the key |
| Router | Query strings broke route matching; no cleanup mechanism for view resources | Strip the query before matching; added `ctx.onCleanup` |
| Config UI | Conflict toast concatenated two strings without a space | Added the missing separator |

## 4. Backend Verification

- **121 backend tests passed** (10 third-party deprecation warnings).
- New permanent lifecycle regression tests in `backend/tests/test_servers.py`:
  - Start rejects a server without `server.jar` and leaves it stopped.
  - Start registers a fake process, stop terminates it, and the monitor records `stopped` with `process_id = NULL`.
  - Restart replaces the registered process with a new one.
- Existing backup tests cover restore success, archive preservation, stopped-server
  enforcement, missing/corrupt archives, path traversal, symlink rejection, and
  rollback paths.

## 5. Frontend Verification

- **46 frontend tests passed, 0 failed.**
- All JavaScript files pass `node --check`.
- Unused imports and dead variables were removed from touched views.
- `git diff --check` reports no whitespace errors.

## 6. Browser QA Coverage (Playwright)

Desktop sweep at 1440×900 and mobile sweep at 390×844 covered:

- Overview
- Servers list
- Create server
- Server detail
- File manager
- Server configuration
- Backups
- Server console
- Users
- Settings
- Account
- Not-found route

Both sweeps produced **no JavaScript page errors, no console errors, and no horizontal overflow**.

## 7. Chrome DevTools MCP Verification (System Chromium)

### Authentication & Dashboard
- **Login:** Successful with documented local admin account (`Adreoz1` / `AdreozPass123`) without credential changes.
- **Dashboard (`#/overview`):** Rendered correctly with panel status, host metrics, account profile, recent activity, and platform summary.
- **Console messages:** None (clean).
- **Network:** 41 requests — all `200`/`304`.

### Server Lifecycle
- **Servers list (`#/servers`):** Fixture server `test-server` displayed with correct status "Stopped", status-dot class rendered properly.
- **Server detail (`#/servers/1`):** All configuration fields populated; "Start server" button present.
- **Start without `server.jar`:** Clicked "Start server" → HTTP 400 returned, toast displayed: *"Server software (server.jar) is not installed in /home/suriya/Adreoz Server Manager/data/servers/test-server. Import or install software before starting."* The 400 is a handled network response, not an uncaught JS error. Server remains stopped.

### WebSocket Console
- **Console page (`#/servers/1/console`):** Loaded successfully. WebSocket connected → status badge shows "Connected", toast *"Connected to console for test-server."* Command input enabled, auto-scroll button present.
- **Navigation away (`#/overview`):** WebSocket disconnected cleanly. **No** reconnect toast, no reconnect attempts, no new console errors. This confirms the `disposed` guard and `ctx.onCleanup` fix works.

### Logout
- **Sign out:** Clicked sidebar "Sign out" → POST `/api/auth/logout` 200 → redirected to `/` → sign-in screen renders with *"You have been signed out."* message.
- **Console messages:** Still only the pre-existing accessibility issue (see below).
- **Network:** All 200/304 except the expected 400 from the start attempt.

### Console Messages Observed
| Type | Count | Message | Assessment |
| --- | --- | --- | --- |
| `[issue]` | 2 (cumulative) | *"A form field element should have an id or name attribute"* | Pre-existing minor accessibility best-practice issue (search/login inputs). Not a regression. |
| `[error]` | 1 | *"Failed to load resource: the server responded with a status of 400 (Bad Request)"* | Expected — from the intentional "start without server.jar" test. Handled by UI toast. |

**No JavaScript errors, no uncaught exceptions, no page rendering failures.**

## 8. Lifecycle Verification

- Starting/restarting `test-server` without `server.jar` returns the expected
  HTTP 400 and displays the correct error toast; the server remains stopped.
- The expected HTTP 400 network response was observed in Chromium; it was a
  handled application response, not an uncaught JavaScript error.
- Backend tests use a fake subprocess to verify launch, monitor, stop, and
  restart behavior without running Java or a real Minecraft server.
- Status-dot classes were verified on the list, detail, and console pages.

## 9. File, Config, and Backup Workflows

Browser-verified:

- Create a new text file, open it in the editor, save, and return to the folder.
- Upload a raw file, open it, and verify its contents.
- Delete test files and confirm the directory returns to empty.
- Open the configuration page, save MOTD/difficulty changes, reload, and restore
  the original values.
- Create a backup, open its details, download it, and delete it.
- Confirm the original backup remains and the test server directory is empty.

Not browser-executed:

- Backup restore. This was intentionally skipped to avoid restoring over real
  fixture data; backend restore tests cover the logic and safety cases.

## 10. Responsive and Error Sweep

- Mobile widths showed no horizontal scrolling or clipped layout on any audited
  route.
- The final route sweep loaded every audited page successfully.
- The console WebSocket connected while on the console page and did not reconnect
  after navigating away.
- Logout was tested last: the login screen rendered, the authenticated shell was
  gone, and no console errors occurred.

## 11. Fixture and State Restoration

- `data/servers/test-server` is empty.
- The test configuration file was removed.
- The original backup count remains **1**; its archive is intact.
- No account, password, role, or permission changes were made during final QA.
- QA browser artifacts were removed from `.playwright-mcp/`.
- The dev server remains running for inspection; it can be stopped with
  `kill 115820`.

## 12. Remaining Issues and Limitations

1. **No real server runtime test.** Phase 6 is not started, and no real
   `server.jar` was available. Process lifecycle is covered with a fake
   subprocess and the jar-missing error path.
2. **No live console log stream from a real server.** WebSocket connection,
   authentication, rendering, and cleanup were verified; real Minecraft log
   traffic was not.
3. **Backup restore was not browser-executed.** It is covered by backend tests,
   but not by an end-to-end UI restore against a disposable fixture.
4. **Single-process operation coordination.** The in-memory operation locks are
   documented as suitable for one Uvicorn worker only; multi-worker deployment
   would need external coordination.
5. **Browser QA is not committed as CI.** The 46 frontend tests are unit tests;
   the Playwright sweep remains a manual audit artifact.
6. **Third-party warnings.** The backend suite reports 10 deprecation warnings
   from Starlette/FastAPI/httpx, not from application code.
7. **Accessibility issue.** A pre-existing minor issue: search/login form fields
   lack `id` or `name` attributes (reported as `[issue]` by Chrome DevTools).
   Not a regression, but could be addressed in a future cleanup.
8. **Sidebar console link.** The sidebar "Server console" link uses a static
   hash `#/servers/console` without the server ID. The correct route is
   `#/servers/1/console`. This is a pre-existing UX gap, not a regression.
9. **Untracked audit file.** `QA_AUDIT_REPORT.md` is intentionally untracked until
   the commit decision.

## 13. Git Status and Approval Request

Working tree:

- 14 modified files under `backend/` and `frontend/`
- 1 untracked file: `QA_AUDIT_REPORT.md`
- Nothing staged
- Branch `master` is up to date with `origin/master`

No commit, push, amend, reset, or discard has been performed. Approval is
requested before committing or pushing these changes.