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
2. Road distance/duration from OSRM (or labelled straight-line estimate); fare = (base fare per passenger + optional boost) × passenger count; the base comes from settings.
3. Candidates: online, approved, active account, vehicle registered with seats for the whole party, location fresher than
   `location_stale_s` and inside campus, no assigned ride, no pending offer, never offered this ride.
   PostGIS `ST_DWithin` on a generated `geography` column with a GiST index; ordered by distance.
4. The nearest candidate gets a time-limited offer (`offer_timeout_s`). Reject/expiry moves to the next.
5. Acceptance claims the offer and assigns the ride in one transaction.
6. The dispatch loop (1 s tick) expires offers, retries searching rides as drivers come online, and ends
   searches at `search_timeout_s` / `max_offers_per_ride` with `NO_DRIVER_AVAILABLE`.

A searching passenger can raise the per-passenger boost (`max_boost_paise`, at most `max_boosts_per_ride`
times). A boost restarts the search window and makes drivers who declined or let the offer expire eligible again.

## Cancellation fee

While a driver is assigned (ACCEPTED/ARRIVED), every location report stores the driver's distance to the
pickup in `ride_approach_samples` (distance only, no coordinates; pruned after 2 min, deleted when the
driver stops heading to the pickup). A passenger cancellation is free unless the driver got more than
`approach_min_m` closer over the last `cancel_window_s`; a stationary driver, one moving away, or one that
stopped reporting can't be shown to be approaching. The fee is `cancel_fee_percent` of the fare, at least
`cancel_fee_min_paise`, capped at the fare. The client must send the fee it showed (`accepted_fee_paise`);
a higher fee is refused with `fee_changed`, never charged silently. The fee is a PENDING
`payments` row (`kind = 'cancellation_fee'`). The passenger app shows unpaid fees as a reminder but booking
is not blocked; an admin marks each fee PAID (credited to that driver's earnings) or WAIVED. Admin
cancellations are always free.

## Start PIN

Each ride gets a random four-digit `start_pin`. Only the passenger's ride view includes it, and only while
the driver is on the way or waiting. Starting the trip from ARRIVED requires the PIN (constant-time compare,
5 attempts per ride per 5 min), so the driver picks up the right passenger.

## Demand map

`GET /api/driver/demand` counts pickups of rides requested in the last `demand_window_min` minutes in a
~150 m grid, with how many are still waiting and how many found no driver. Drivers who are on duty with no
offer or trip see it as shaded circles. Only cell counts are returned, never individual passengers.

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
