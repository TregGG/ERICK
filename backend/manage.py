"""Operational commands. Run from the repository root:

    python -m backend.manage migrate            apply database migrations
    python -m backend.manage load-campus        load IIT Roorkee boundary + landmarks (safe to repeat)
    python -m backend.manage seed-demo          create labelled demo accounts (refused in production)
    python -m backend.manage create-admin --phone 98xxxxxxxx --name "Transport Office"
    python -m backend.manage bootstrap-dev      migrate + load-campus + seed-demo
"""
import argparse
import asyncio
import json
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from .config import BACKEND_DIR, settings
from .db import SessionLocal, engine
from .models import CampusZone, DriverProfile, Landmark, User, Vehicle
from .security import normalize_phone
from .services.geo import CAMPUS_FIXTURE

DEMO_DRIVERS = ["Aarav Singh", "Ramesh Kumar", "Imran Ali", "Vikram Das", "Sanjay Patel",
                "Karan Mehta", "Rohit Verma", "Nitin Rao", "Deepak Shah", "Manoj Yadav"]
DEMO_PASSENGERS = ["Aditi Sharma", "Neha Gupta", "Kabir Khan", "Priya Nair", "Arjun Kapoor", "Meera Joshi",
                   "Ishaan Roy", "Sana Malik", "Riya Das", "Dev Singh", "Tanya Jain", "Aman Sethi",
                   "Kavya Iyer", "Rohan Bose", "Nisha Paul"]
DEMO_ADMIN_PHONE = "+917000000001"


def migrate() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")


async def load_campus() -> None:
    fixture = json.loads(CAMPUS_FIXTURE.read_text(encoding="utf-8"))
    async with SessionLocal() as db:
        zone_name = "IIT Roorkee campus boundary"
        if (await db.execute(select(CampusZone).where(CampusZone.name == zone_name))).scalar_one_or_none() is None:
            db.add(CampusZone(name=zone_name, kind="boundary",
                              geojson=json.dumps({"type": "Polygon", "coordinates": fixture["boundary"]["coordinates"]}),
                              source=f"{fixture['source']} ({fixture['boundary']['osm_ref']})"))
        existing = set((await db.execute(select(Landmark.name))).scalars())
        added = 0
        for item in fixture["landmarks"]:
            if item["name"] in existing:
                continue  # never overwrite administrator edits
            db.add(Landmark(name=item["name"], category=item["category"], lat=item["lat"], lng=item["lng"],
                            is_pickup_point=item["is_pickup_point"], osm_ref=item["osm_ref"],
                            source="OpenStreetMap (ODbL); centroid, not a surveyed stop"))
            added += 1
        await db.commit()
    print(f"Campus data loaded: boundary + {added} new landmark(s).")


async def seed_demo() -> None:
    if settings.is_production:
        sys.exit("Refusing to seed demo accounts when APP_ENV=production.")
    async with SessionLocal() as db:
        if (await db.execute(select(User).where(User.phone == "+919000000001"))).scalar_one_or_none():
            print("Demo data already present.")
            return
        for index, name in enumerate(DEMO_DRIVERS, 1):
            driver = User(phone=f"+9190000000{index:02d}", name=f"{name} (Demo)", role="driver")
            db.add(driver)
            await db.flush()
            # Demo drivers are approved but offline and have no location until a real
            # browser (or the labelled simulator) reports one.
            db.add(DriverProfile(user_id=driver.id, verification_status="approved", verification_note="Demo account"))
            db.add(Vehicle(driver_id=driver.id, registration_number=f"UK 08 ER {1200 + index}"))
        for index, name in enumerate(DEMO_PASSENGERS, 1):
            db.add(User(phone=f"+9180000000{index:02d}", name=f"{name} (Demo)", role="passenger"))
        if (await db.execute(select(User).where(User.phone == DEMO_ADMIN_PHONE))).scalar_one_or_none() is None:
            db.add(User(phone=DEMO_ADMIN_PHONE, name="Demo Transport Office", role="admin"))
        await db.commit()
    print("Demo accounts created: drivers 9000000001-9000000010, passengers 8000000001-8000000015, admin 7000000001.")


async def create_admin(phone: str, name: str) -> None:
    phone = normalize_phone(phone)
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
        if user is not None and user.role != "admin":
            sys.exit(f"{phone} already belongs to a {user.role} account.")
        if user is None:
            db.add(User(phone=phone, name=name, role="admin"))
            await db.commit()
    print(f"Administrator ready: {phone}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("migrate", "load-campus", "seed-demo", "bootstrap-dev"):
        sub.add_parser(name)
    admin = sub.add_parser("create-admin")
    admin.add_argument("--phone", required=True)
    admin.add_argument("--name", default="Transport Office")
    args = parser.parse_args()

    async def run_async(coro):
        try:
            await coro
        finally:
            await engine.dispose()

    if args.cmd in ("migrate", "bootstrap-dev"):
        migrate()
    if args.cmd in ("load-campus", "bootstrap-dev"):
        asyncio.run(run_async(load_campus()))
    if args.cmd in ("seed-demo", "bootstrap-dev"):
        asyncio.run(run_async(seed_demo()))
    if args.cmd == "create-admin":
        asyncio.run(run_async(create_admin(args.phone, args.name)))


if __name__ == "__main__":
    main()
