"""Test harness.

Tests run against a dedicated database (TEST_DATABASE_URL, default: the
`erickshaw_test` database on the docker-compose PostGIS container) that is built
with the real Alembic migrations. Set TEST_DATABASE_URL=sqlite+aiosqlite:///... to
run the same suite on SQLite. Routing is disabled and the dispatch loop is off so
results are deterministic; tests drive dispatch explicitly.
"""
import asyncio
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:55432/erickshaw_test")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": TEST_DB,
    "JWT_SECRET": "test-secret-" + "x" * 40,
    "OTP_DEV_MODE": "true",
    "SMS_PROVIDER": "console",
    "SIMULATION_ENABLED": "true",
    "DISPATCH_ENABLED": "false",
    "ROUTING_BASE_URL": "",
    "LOCATION_MIN_INTERVAL_SECONDS": "0",
})
sys.path.insert(0, str(REPO))


def _ensure_database() -> None:
    if not TEST_DB.startswith("postgresql"):
        db_file = Path(TEST_DB.split(":///", 1)[1])
        if db_file.exists():
            db_file.unlink()
        return
    import asyncpg

    async def create():
        base, name = TEST_DB.replace("+asyncpg", "").rsplit("/", 1)
        conn = await asyncpg.connect(base + "/postgres")
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()
    asyncio.run(create())


_ensure_database()

from backend import manage  # noqa: E402  (must import after the environment is configured)

manage.migrate()

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.db import SessionLocal  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models import DriverProfile, User  # noqa: E402
from backend.security import rate_limiter  # noqa: E402
from backend.services import ops_settings  # noqa: E402
from backend.services.geo import load_campus  # noqa: E402

# Verified OSM landmark coordinates (see backend/data/iitr_campus.json).
LIBRARY = (29.865311, 77.89477)
RAJENDRA_BHAWAN = (29.87101, 77.894076)
HOSPITAL = (29.861718, 77.892923)
CENTURY_GATE = (29.868686, 77.890187)
KASTURBA_BHAWAN = (29.867222, 77.900693)
OUTSIDE_CAMPUS = (29.9000, 77.9500)

KEEP_TABLES = {"landmarks", "campus_zones", "alembic_version", "spatial_ref_sys"}


@pytest.fixture(scope="session", autouse=True)
async def campus_loaded():
    await manage.load_campus()
    async with SessionLocal() as db:
        await load_campus(db)


@pytest.fixture(autouse=True)
async def clean_db():
    async with SessionLocal() as db:
        tables = [t for t in ("ratings", "payments", "notifications", "ride_approach_samples", "ride_events", "ride_offers", "rides",
                              "driver_locations", "saved_places", "vehicles", "driver_profiles", "auth_sessions",
                              "otp_challenges", "audit_logs", "system_settings", "users") if t not in KEEP_TABLES]
        if TEST_DB.startswith("postgresql"):
            await db.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))
        else:
            for table in tables:
                await db.execute(text(f"DELETE FROM {table}"))
        await db.commit()
    ops_settings.invalidate()
    rate_limiter.reset()
    yield


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def now_iso(offset_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat()


class Api:
    """Small helper around the HTTP API used by every test."""

    def __init__(self, client: httpx.AsyncClient):
        self.c = client
        self._phone = 9100000000

    def next_phone(self) -> str:
        self._phone += 1
        return str(self._phone)

    async def login(self, app_role: str, phone: str = None, name: str = None) -> dict:
        phone = phone or self.next_phone()
        r = await self.c.post("/api/auth/otp/request", json={"phone": phone, "app": app_role})
        assert r.status_code == 200, r.text
        r = await self.c.post("/api/auth/otp/verify",
                              json={"phone": phone, "app": app_role, "code": r.json()["dev_otp"], "name": name})
        assert r.status_code == 200, r.text
        data = r.json()
        return {"token": data["access_token"], "user": data["user"], "phone": phone,
                "h": {"Authorization": f"Bearer {data['access_token']}"}}

    async def passenger(self, name="Test Passenger") -> dict:
        return await self.login("passenger", name=name)

    async def admin(self) -> dict:
        phone = self.next_phone()
        async with SessionLocal() as db:
            db.add(User(phone="+91" + phone, name="Test Admin", role="admin"))
            await db.commit()
        return await self.login("admin", phone=phone)

    async def driver(self, at=LIBRARY, approved=True, online=True, vehicle=None, name="Test Driver") -> dict:
        d = await self.login("driver", name=name)
        vehicle = vehicle or f"UK 08 ER {self._phone % 10000:04d}"
        r = await self.c.put("/api/driver/profile", headers=d["h"], json={"name": name, "vehicle_number": vehicle})
        assert r.status_code == 200, r.text
        if approved:
            async with SessionLocal() as db:
                profile = await db.get(DriverProfile, __import__("uuid").UUID(d["user"]["id"]))
                profile.verification_status = "approved"
                await db.commit()
        if online:
            r = await self.c.post("/api/driver/online", headers=d["h"], json={"online": True})
            assert r.status_code == 200, r.text
            if at is not None:
                await self.locate(d, at)
        return d

    async def locate(self, d: dict, at, accuracy=8.0, offset_s=0.0, source="gps", expect=200):
        r = await self.c.post("/api/driver/location", headers=d["h"], json={
            "lat": at[0], "lng": at[1], "accuracy_m": accuracy, "timestamp": now_iso(offset_s), "source": source})
        assert r.status_code == expect, r.text
        return r.json()

    async def request_ride(self, p: dict, pickup=LIBRARY, dropoff=RAJENDRA_BHAWAN, key=None, expect=201):
        headers = dict(p["h"])
        if key:
            headers["Idempotency-Key"] = key
        r = await self.c.post("/api/passenger/rides", headers=headers, json={
            "pickup": {"lat": pickup[0], "lng": pickup[1], "name": "Pickup"},
            "dropoff": {"lat": dropoff[0], "lng": dropoff[1], "name": "Drop"}})
        assert r.status_code == expect, r.text
        return r.json()

    async def driver_state(self, d: dict) -> dict:
        r = await self.c.get("/api/driver/state", headers=d["h"])
        assert r.status_code == 200, r.text
        return r.json()

    async def act(self, d: dict, ride_id: str, action: str, body=None, expect=200):
        if action == "start" and body is None:
            body = {"pin": await ride_pin(ride_id)}  # what the passenger would read out at pickup
        r = await self.c.post(f"/api/driver/rides/{ride_id}/{action}", headers=d["h"], json=body or {})
        assert r.status_code == expect, r.text
        return r.json()


async def ride_pin(ride_id: str):
    from backend.models import Ride
    async with SessionLocal() as db:
        return (await db.get(Ride, __import__("uuid").UUID(ride_id))).start_pin


@pytest.fixture
def api(client):
    return Api(client)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
