# Campus Rick — IIT Roorkee e-rickshaw booking, dispatch & live tracking

Passenger, driver and administrator web apps backed by FastAPI, PostgreSQL/PostGIS and WebSockets.

```bat
start-erick.cmd
```

Then open <http://127.0.0.1:8091/passenger/>, <http://127.0.0.1:8091/driver/> and
<http://127.0.0.1:8091/admin/>. In development the login code is shown on screen.

* [docs/OPERATIONS.md](docs/OPERATIONS.md) — setup, demo accounts, tests, environment, rollback, limitations
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — state machine, dispatch, real-time protocol, integrity rules
* [docs/AUDIT.md](docs/AUDIT.md) — what the repository looked like before this version, and why things changed
* [docs/STATUS.md](docs/STATUS.md) — implementation checklist

Map data © OpenStreetMap contributors (ODbL). Campus places are OSM features, not official stops. 
