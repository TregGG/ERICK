# Repository audit (2026-10-25, before the rewrite)

A snapshot of the pre-change working tree is kept in `work/pre-migration-snapshot/`
(`working-tree.tar`, `uncommitted-changes.patch`, `BASE_COMMIT` = `3a9c43b`).

## What was actually running

| URL | Served by | Notes |
| --- | --- | --- |
| `http://127.0.0.1:8091/passenger/`, `/driver/` | `server.mjs` (Node, in-memory) | Started by `start-erick-node.cmd` / `start-erick-local.cmd` |
| port 8000 | FastAPI `backend/main.py` | Did not start on this machine through the bundled Codex Python runtime |
| — | `backend/dev_server.py` (stdlib HTTP + SQLite) | A third, partial implementation |

There were **three competing ride backends** with different state machines and no shared source of truth.
The browser apps talked to whichever one served them; on 8091 that was the Node server, so **all rides,
users and driver availability lived in Node process memory and vanished on restart.**
Real-time updates were 3-second polling; the apps' WebSocket code connected to `/ws/{token}`, which the
Node server did not implement.

## Critical findings

1. **Path traversal / secret exposure (Node).** `GET /passenger/../backend/.env` returned HTTP 200. Any
   repository file, including `.env` and the SQLite databases, could be downloaded.
2. **No role checks (Node).** A passenger token could call `/api/driver/accept-passenger/{id}` on its own
   ride and become the "driver"; pickup/drop-off/settings endpoints had no ownership checks.
3. **Role escalation (FastAPI).** `verify-otp` with `role=driver` silently changed an existing passenger
   into a driver; `request-otp` auto-created accounts with a client-chosen role.
4. **OTP weaknesses.** Codes kept in plaintext process memory, no attempt limit, printed to the log
   (`DEBUG: Sent OTP ...`), 1-year JWT expiry in the default config, no logout/revocation, default JWT secret.
5. **Wrong campus.** All coordinates (`28.6139, 77.2090`) are central New Delhi, ~150 km from IIT Roorkee.
   The geofence was a circle around that point.
6. **Fake data presented as real.** Fixed "4.7"/"4.8" driver ratings, hard-coded ETA of 3 minutes,
   `distance_to_pickup_meters: 300`, a "Pay via QR" button that just showed "Payment successful!",
   and drivers placed at a fake campus-centre position when GPS was denied.
7. **Race conditions.** Pooling seat counts were read-modify-write without locks; acceptance in Node had no
   atomicity; no idempotency for ride requests; cancellation could leave seat counts negative.
8. **Location privacy.** Every online driver could list every open request with passenger names; the
   WebSocket token was in the URL (logged by proxies).
9. **Environment.** `.venv`/`.venv313` were empty shells; `.packages` was a cp312 target-install for a
   different runtime; no `.gitignore`; `backend/.env` tracked by git. Docker was installed but not running.
   System Python 3.13 handles asyncio sockets correctly — the "Windows socket issue" was specific to the
   bundled runtime, not to FastAPI.
10. **No tests** of any kind.

## Feature status before the rewrite

| Feature | Status |
| --- | --- |
| OTP login (dev code on screen) | Worked (Node), insecure |
| Landmark chips, tap-to-place pins | Worked, wrong city |
| Ride request → driver accept → pickup → drop-off | Worked in Node memory only |
| Live driver tracking | Not working on 8091 (no WebSocket); driver marker came from polling |
| Pooling / detour matching | FastAPI only, not reachable from the running app |
| Ride history | Worked in Node memory; seeded fake history |
| Persistence across restart | **Failed** |
| Admin interface | Did not exist |

## Decisions taken

* FastAPI is the single backend; PostgreSQL + PostGIS is the primary store (SQLite fallback kept for quick runs).
* Pooling was **not** carried forward: it had no atomic seat accounting and was unreachable from the
  running app. One active ride per driver is enforced by the database. Pooling can be reintroduced as an
  explicit model later (see `docs/ARCHITECTURE.md`).
* Campus geography comes from OpenStreetMap (ODbL), with each landmark traceable to its OSM element.
