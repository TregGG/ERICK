# Architecture

```
 Browser apps (vanilla ES modules, Leaflet + OSM tiles)
   /passenger/   /driver/   /admin/      shared/: api.js  realtime.js  geo.js  map.js  ui.js  auth.js
        |  REST (JSON, Bearer token)          |  WebSocket /ws (auth as first message)
        v                                     v
 FastAPI (backend/main.py) ── routers: auth, public, passenger, driver, admin, ws
        |  services: rides (state machine) · dispatch (offers) · locations · geo · routing · ops_settings · audit
        |  realtime.py: in-process connection manager + transactional outbox
        v
 PostgreSQL 16 + PostGIS 3.4 (docker compose)  ─ Alembic migrations (backend/migrations)
 OSRM HTTP API (public demo server by default) for road distance and geometry
```

## Ride state machine (`backend/states.py`)

```
REQUESTED ──dispatch──▶ OFFERED ──driver accepts──▶ ACCEPTED ──arrived──▶ ARRIVED ──start──▶ IN_PROGRESS ──complete──▶ COMPLETED
    ▲  │                   │  ▲                         │                    │
    │  │                   │  └── offer rejected/expired (back to REQUESTED, next driver)
    │  │                   │                            └─ driver releases ─▶ REQUESTED (reassignment)
    │  └─ search window closed / offer limit ─▶ NO_DRIVER_AVAILABLE
    └── passenger/admin cancel from REQUESTED, OFFERED, ACCEPTED, ARRIVED ─▶ CANCELLED
        driver no-show cancel from ARRIVED (after the configured wait) ─▶ CANCELLED
```

Every transition is a compare-and-set `UPDATE ... WHERE status = :current AND version = :version` plus a
`ride_events` row in the same transaction. Offers live in `ride_offers` (`PENDING → ACCEPTED | REJECTED |
EXPIRED | CANCELLED`).

## Integrity guarantees (enforced by the database, not just code)

| Rule | Mechanism |
| --- | --- |
| One active ride per passenger | partial unique index `uq_rides_one_active_per_passenger` |
| One assigned ride per driver (no overlapping trips) | partial unique index `uq_rides_one_active_per_driver` |
| One pending offer per ride / per driver | partial unique indexes on `ride_offers` |
| A driver is never re-offered a ride they declined or let expire | unique `(ride_id, driver_id)` on `ride_offers` |
| Duplicate booking taps | unique `(passenger_id, idempotency_key)`; client sends `Idempotency-Key` |
| Assigned rides always have a driver | check constraint `ck_rides_assigned_has_driver` |
| Money is exact | integer paise everywhere (`fare_paise`, `amount_paise`) |
| One payment per ride, one rating per rater per ride | unique constraints |

## Dispatch

1. Passenger request is validated (inside the OSM campus polygon + `GEOFENCE_BUFFER_METERS`, ≥100 m trip).
2. Road distance/duration from OSRM (or labelled straight-line estimate); flat fare from settings.
3. Candidates: online, approved, active account, vehicle registered, location fresher than
   `location_stale_s` and inside campus, no assigned ride, no pending offer, never offered this ride.
   PostGIS `ST_DWithin` on a generated `geography` column with a GiST index; ordered by distance.
4. The nearest candidate gets a time-limited offer (`offer_timeout_s`). Reject/expiry moves to the next.
5. Acceptance claims the offer and assigns the ride in one transaction.
6. The dispatch loop (1 s tick) expires offers, retries searching rides as drivers come online, and ends
   searches at `search_timeout_s` / `max_offers_per_ride` with `NO_DRIVER_AVAILABLE`.

Ranking uses straight-line distance to the pickup (cheap and adequate at campus scale). Ranking by
road ETA would need an OSRM *table* call per dispatch and a self-hosted OSRM.

## Real-time

* `/ws`: the first message must be `{"type":"auth","token":...}`; the server replies `ready` then a full
  `sync` snapshot. After any reconnect the client trusts the snapshot, so missed events are recovered.
* Events: `ride.updated` (full role-specific ride view with `version`; clients drop older versions),
  `offer.new`, `offer.closed`, `ride.released`, `driver.location`, `notification`, `account.updated`.
* Events are queued on the DB session and sent only after commit (`commit_and_publish`). Critical
  transitions always go through REST and the database; WebSocket messages never persist state on their own,
  except location reports, which are validated and stored like the REST fallback.
* The server re-validates the session every 30 s and closes with 4401/4403 when it is revoked, expired
  or the account is suspended. Message floods are closed with 4408.
* REST polling (5 s) is used **only** while the socket is disconnected.

**Scaling beyond one instance:** the connection manager, rate limiter and dispatch lock are in-process.
Before running more than one backend instance, add Redis (or Postgres LISTEN/NOTIFY) pub/sub for event
fan-out, move rate limiting to Redis, and run the dispatch loop as a single leader (advisory lock).

## Location privacy

* Drivers' browsers send positions only while on duty or on a trip; going off duty deletes the stored
  position. Only the latest position is kept (no history table).
* Passengers see their assigned driver's exact position only during their trip; while booking they see
  nearby availability rounded to ~100 m.
* Drivers see a passenger's first name, pickup and drop-off in an offer; full name and phone only after
  accepting, and contact details disappear from both sides once the trip ends.

## Known simplifications

* Single-seat dispatch (no pooling). The previous pooling code was not carried forward (see `AUDIT.md`).
* Cash payments only, recorded by the driver; no payment gateway.
* Travel time = road distance ÷ `ERICKSHAW_AVG_SPEED_KMH` (OSRM's car profile durations are not
  representative of e-rickshaws).
