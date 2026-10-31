# Implementation checklist

Legend: [x] done and verified by a test or run · [~] done, partially verified · [ ] not done

## Phase 1 — audit & stabilisation
- [x] Repository audit (`docs/AUDIT.md`), baseline run of the Node server, security probes
- [x] Snapshot of pre-change tree for rollback (`work/pre-migration-snapshot/`)
- [x] Working Python 3.13 environment on Windows; FastAPI starts and serves on 8091
- [x] Path traversal and missing role checks closed (FastAPI static mounts; legacy Node server patched)

## Phase 2 — data & backend foundation
- [x] PostgreSQL 16 + PostGIS 3.4 via docker compose; Alembic migration `0001`
- [x] Schema: users, driver profiles, vehicles, sessions, OTP challenges, rides, offers, ride events,
      latest driver locations, landmarks, campus zones, settings, payments, ratings, notifications,
      saved places, audit logs
- [x] Single ride state machine; compare-and-set transitions; DB-level uniqueness guards
- [x] Persistence across backend restart (`tests/test_persistence.py`)

## Phase 3 — real-time ride lifecycle
- [x] Authenticated WebSocket (auth-first-message, sync snapshot, revalidation, flood limit)
- [x] Offers pushed to drivers, ride updates and driver locations pushed to passengers
- [x] Atomic accept, reject → next driver, timeout → next driver, driver release → reassignment
- [x] Idempotent ride requests; concurrent accept / duplicate request tests
- [x] Outbox: events sent only after commit; REST polling only while disconnected

## Phase 4 — geospatial
- [x] IIT Roorkee boundary + 39 landmarks from OpenStreetMap (ODbL), traceable OSM ids
- [x] Geofence validation with GPS-tolerance buffer; out-of-area rejection
- [x] PostGIS radius search with GiST index (Python fallback on SQLite)
- [x] OSRM road routes with cache/throttle; labelled straight-line fallback
- [x] Stale / inaccurate / future / out-of-order location rejection

## Phase 5 — product
- [x] Passenger: OTP login, landmark search, saved places, tap/drag pins, my-location, route preview,
      nearby availability, request, live tracking with freshness + simulated label, cancel with reason,
      rating, history with timeline
- [x] Driver: profile & vehicle, verification status, duty toggle, GPS sharing with explicit states,
      offers with countdown, accept/decline, arrive/start/complete, release, no-show, navigation link,
      cash collection, earnings from completed rides, history, labelled test simulator
- [x] Admin: overview, rides (detail, timeline, offers, cancel), driver verification/suspension, account
      suspension, landmarks (add/disable/pickup flag), fares & dispatch settings, reports, audit log
- [~] Real SMS: Twilio Verify (recommended) and Twilio Messaging implemented and tested against a simulated Twilio API; needs a live test with real credentials
- [ ] Payment gateway (cash only by design for now)
- [ ] Push notifications when the app is closed

## Phase 6 — quality & deployment
- [x] Backend tests: 60 on PostgreSQL, 59 + 1 skipped on SQLite
- [x] Browser end-to-end scenario (`e2e/test_e2e.py`): two Chromium sessions, browser-GPS fixtures,
      full lifecycle, cancellation, competing acceptance, admin console, console/HTTP error check — passing
- [x] Restart persistence verified on the dev database (ride counts identical before/after restart)
- [x] Security headers + CSP, production config guard, rate limits, audit logging
- [ ] Multi-instance deployment (Redis pub/sub, leader-elected dispatch)
- [ ] Automated OTP/record retention jobs
