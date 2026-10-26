# Campus Rick backend (FastAPI)

The single backend for the IIT Roorkee campus e-rickshaw platform: REST API, authenticated WebSocket
channel, dispatch loop, and static hosting of the passenger, driver and admin apps.

* Start, test, configure and roll back: [`../docs/OPERATIONS.md`](../docs/OPERATIONS.md)
* Design, state machine, integrity rules: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)

| Module | Purpose |
| --- | --- |
| `config.py` | Settings from env / `.env`; refuses unsafe production configuration |
| `models.py`, `migrations/` | Schema (SQLAlchemy 2) and Alembic migrations |
| `states.py` | Ride state machine |
| `security.py` | Phone normalisation, OTP hashing, JWT + server-side sessions, role guards, rate limiting |
| `services/rides.py` | Transitions, role-specific views, notifications |
| `services/dispatch.py` | Candidate search (PostGIS), offers, expiry, search timeout |
| `services/locations.py` | Location validation and storage |
| `services/geo.py`, `data/iitr_campus.json` | Campus geofence and OSM-derived landmarks |
| `services/routing.py` | OSRM client with cache, throttle and labelled fallback |
| `realtime.py`, `api/ws.py` | WebSocket fan-out and protocol |
| `manage.py` | `migrate`, `load-campus`, `seed-demo`, `create-admin`, `bootstrap-dev` |
