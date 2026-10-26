# Operations runbook (Windows)

## Prerequisites

* Python 3.13 (`py -3.13`), Docker Desktop (for PostGIS). Node is no longer required.

## First start

```bat
start-erick.cmd
```

This creates `.venv313` if needed, starts PostGIS (`docker compose up -d --wait database`, port 55432),
runs `python -m backend.manage bootstrap-dev` (migrations, IIT Roorkee campus data, demo accounts)
and serves everything on `http://127.0.0.1:8091`:

| App | URL | Demo login (dev OTP shown on screen) |
| --- | --- | --- |
| Passenger | `/passenger/` | 8000000001 … 8000000015 |
| Driver | `/driver/` | 9000000001 … 9000000010 (approved, offline, no position until a browser shares one) |
| Admin | `/admin/` | 7000000001 |
| API docs | `/docs` | disabled when `APP_ENV=production` |

Manual equivalent:

```bat
py -3.13 -m venv .venv313
.venv313\Scripts\python -m pip install -r backend\requirements-dev.txt
docker compose up -d --wait database
.venv313\Scripts\python -m backend.manage bootstrap-dev
.venv313\Scripts\python run-local.py
```

Without Docker: set `DATABASE_URL=sqlite+aiosqlite:///backend/erick_dev.db` in `backend\.env`
(radius search then runs in Python instead of PostGIS).

Health check: `GET /api/health` reports database reachability, engine, dispatch loop and socket count.

## Phones on the same Wi-Fi

Browsers only allow GPS on `https://` or `localhost`. To test on a phone, put the server behind HTTPS
(e.g. a reverse proxy or a tunnel with a valid certificate) and set `HOST=0.0.0.0`.
Plain `http://<lan-ip>:8091` will load the apps but location will be refused, and the driver app says so.

## Tests

```bat
cd backend
..\.venv313\Scripts\python -m pytest -q                      :: PostgreSQL/PostGIS (default, needs docker db)
set TEST_DATABASE_URL=sqlite+aiosqlite:///C:/Temp/erick_test.db
..\.venv313\Scripts\python -m pytest -q                      :: SQLite
cd ..
.venv313\Scripts\python -m playwright install chromium
.venv313\Scripts\python -m pytest e2e -s                     :: browser scenario; server must be running
```

The backend suite creates and drops its own `erickshaw_test` database; it never touches `erickshaw`.

## Environment variables

See `backend/.env.example` (placeholders only). Production requires: `APP_ENV=production`, a random
`JWT_SECRET` (≥32 chars), `OTP_DEV_MODE=false`, `SIMULATION_ENABLED=false`, a real `SMS_PROVIDER`
(`twilio` + `TWILIO_*` credentials), explicit `CORS_ORIGINS` if the apps are served from another origin,
PostgreSQL `DATABASE_URL`, and preferably a self-hosted `ROUTING_BASE_URL`. The server refuses to start
when these are unsafe (`Settings.validate_for_runtime`).

Production setup:

```bat
python -m backend.manage migrate
python -m backend.manage load-campus
python -m backend.manage create-admin --phone 98xxxxxxxx --name "Transport Office"
```

`seed-demo` refuses to run when `APP_ENV=production`.

## Data retention

* `driver_locations`: latest position only, deleted when the driver goes off duty.
* `otp_challenges`: hashed codes; safe to purge rows older than 24 h (not yet automated).
* `rides`, `ride_events`, `payments`, `ratings`, `audit_logs`: retained (operational records).

## Rollback

The previous app (Node in-memory server + old frontends) is preserved:

* `work/pre-migration-snapshot/BASE_COMMIT` (`3a9c43b`) plus `uncommitted-changes.patch` reproduce the
  exact pre-change working tree; `working-tree.tar` contains it including untracked files.
* To roll back: stop `start-erick.cmd`, extract `working-tree.tar` into a separate folder and run its
  `start-erick-node.cmd`. Data in the PostGIS volume is untouched by a rollback (the old app keeps state in memory).
* Database: `alembic -c backend/alembic.ini downgrade base` removes the new schema (destroys its data).

## Known limitations / production blockers

* SMS delivery (Twilio adapter) is implemented but has **not** been tested with real credentials.
* OSRM public demo server: rate-limited (~1 req/s), no SLA, car profile. Self-host OSRM with campus roads.
* Single backend instance only (in-process WebSocket fan-out, rate limiting, dispatch lock).
* Background location: mobile browsers throttle or stop GPS when the driver app is in the background or
  the screen is off. The app requests a screen wake lock and asks drivers to keep it open; a native
  wrapper would be needed for reliable background tracking.
* Campus landmarks are OpenStreetMap centroids, not surveyed pickup points; the transport office should
  confirm pickup points in the admin console before launch.
* No payment gateway (cash only); no push notifications when the app is closed.
* Tokens are stored in `localStorage` (XSS would expose them; the CSP forbids inline/eval scripts to reduce that risk).
