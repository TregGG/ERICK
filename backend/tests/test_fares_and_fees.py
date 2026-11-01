"""Passenger count, per-passenger boost and the approach-based cancellation fee."""
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select

from backend.db import SessionLocal, utcnow
from backend.models import AuditLog, RideApproachSample
from backend.services import dispatch

from .conftest import HOSPITAL, LIBRARY, RAJENDRA_BHAWAN


async def request(api, p, passengers=1, boost=0, expect=201):
    r = await api.c.post("/api/passenger/rides", headers=p["h"], json={
        "pickup": {"lat": LIBRARY[0], "lng": LIBRARY[1], "name": "Pickup"},
        "dropoff": {"lat": RAJENDRA_BHAWAN[0], "lng": RAJENDRA_BHAWAN[1], "name": "Drop"},
        "passenger_count": passengers, "boost_paise": boost})
    assert r.status_code == expect, r.text
    return r.json()


async def assigned_ride(api, passengers=1, boost=0):
    driver = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await request(api, p, passengers, boost)
    await api.act(driver, ride["id"], "accept")
    return driver, p, ride


async def set_samples(ride_id: str, samples):
    """samples: [(seconds_ago, distance_m)] replacing whatever the driver reported."""
    now = utcnow()
    async with SessionLocal() as db:
        await db.execute(delete(RideApproachSample).where(RideApproachSample.ride_id == uuid.UUID(ride_id)))
        for ago, distance_m in samples:
            db.add(RideApproachSample(ride_id=uuid.UUID(ride_id), distance_m=distance_m, accuracy_m=8,
                                      recorded_at=now - timedelta(seconds=ago)))
        await db.commit()


async def quote(api, p, ride_id):
    r = await api.c.get(f"/api/passenger/rides/{ride_id}/cancel-quote", headers=p["h"])
    assert r.status_code == 200, r.text
    return r.json()


async def cancel(api, p, ride_id, accepted_fee=0, expect=200):
    r = await api.c.post(f"/api/passenger/rides/{ride_id}/cancel", headers=p["h"],
                         json={"reason": "Plans changed", "accepted_fee_paise": accepted_fee})
    assert r.status_code == expect, r.text
    return r.json()


# --------------------------------------------------------------------------- count and boost

async def test_fare_is_base_plus_boost_per_passenger(api):
    p = await api.passenger()
    ride = await request(api, p, passengers=3, boost=500)
    assert ride["fare"]["paise"] == (1000 + 500) * 3
    assert ride["passenger_count"] == 3
    assert ride["fare_breakdown"]["base_per_passenger"]["paise"] == 1000
    assert ride["fare_breakdown"]["boost_per_passenger"]["paise"] == 500


async def test_passenger_count_and_boost_are_validated(api):
    p = await api.passenger()
    await request(api, p, passengers=7, expect=422)
    await request(api, p, passengers=0, expect=422)
    r = await request(api, p, boost=999999, expect=422)
    assert r["code"] == "invalid_boost"


async def test_driver_sees_party_size_before_accepting(api):
    driver = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await request(api, p, passengers=3, boost=500)
    offer = (await api.driver_state(driver))["offer"]
    assert offer["ride_id"] == ride["id"]
    assert offer["passenger_count"] == 3
    assert offer["fare"]["paise"] == 4500
    assert offer["fare_breakdown"]["boost_per_passenger"]["paise"] == 500


async def test_large_party_is_only_offered_to_a_rickshaw_with_enough_seats(api):
    driver = await api.driver(at=HOSPITAL)  # default 4 seats
    p = await api.passenger()
    ride = await request(api, p, passengers=5)
    assert ride["status"] == "REQUESTED"
    assert (await api.driver_state(driver))["offer"] is None
    r = await api.c.put("/api/driver/profile", headers=driver["h"],
                        json={"name": "Test Driver", "vehicle_number": "UK 08 ER 7777", "capacity": 6})
    assert r.status_code == 200, r.text
    await dispatch.tick()
    assert (await api.driver_state(driver))["offer"]["passenger_count"] == 5


async def test_boost_reoffers_drivers_who_declined_at_the_lower_price(api):
    driver = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await request(api, p)
    await api.act(driver, ride["id"], "reject")
    await dispatch.tick()
    assert (await api.driver_state(driver))["offer"] is None  # never re-offered at the same price

    r = await api.c.post(f"/api/passenger/rides/{ride['id']}/boost", headers=p["h"], json={"boost_paise": 1000})
    assert r.status_code == 200, r.text
    assert r.json()["fare"]["paise"] == 2000
    offer = (await api.driver_state(driver))["offer"]
    assert offer["ride_id"] == ride["id"] and offer["fare"]["paise"] == 2000


async def test_boost_rules(api):
    p = await api.passenger()
    ride = await request(api, p, boost=500)
    url = f"/api/passenger/rides/{ride['id']}/boost"
    assert (await api.c.post(url, headers=p["h"], json={"boost_paise": 500})).json()["code"] == "invalid_boost"
    assert (await api.c.post(url, headers=p["h"], json={"boost_paise": 1000})).status_code == 200
    assert (await api.c.post(url, headers=p["h"], json={"boost_paise": 2000})).status_code == 200
    r = await api.c.post(url, headers=p["h"], json={"boost_paise": 3000})  # the booking boost counts as the first
    assert r.json()["code"] == "boost_limit"


async def test_boost_is_refused_once_a_driver_is_assigned(api):
    _, p, ride = await assigned_ride(api)
    r = await api.c.post(f"/api/passenger/rides/{ride['id']}/boost", headers=p["h"], json={"boost_paise": 500})
    assert r.status_code == 409 and r.json()["code"] == "not_searching"


# --------------------------------------------------------------------------- cancellation fee

async def test_location_updates_record_distance_only_samples(api):
    driver, _, ride = await assigned_ride(api)
    await api.locate(driver, LIBRARY)
    async with SessionLocal() as db:
        samples = (await db.execute(select(RideApproachSample).where(
            RideApproachSample.ride_id == uuid.UUID(ride["id"])).order_by(RideApproachSample.id))).scalars().all()
    assert len(samples) == 2  # one at acceptance, one from the update
    assert samples[0].distance_m > samples[1].distance_m


async def test_cancelling_while_driver_approaches_costs_a_fee_and_blocks_booking(api):
    driver, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(40, 900), (0, 600)])
    q = await quote(api, p, ride["id"])
    assert q["approaching"] is True
    assert q["fee"]["paise"] == 1000  # 50% of Rs 10 is Rs 5, raised to the Rs 10 minimum

    r = await cancel(api, p, ride["id"], accepted_fee=0, expect=409)  # never charged without consent
    assert r["code"] == "fee_changed" and r["quote"]["fee"]["paise"] == 1000
    cancelled = await cancel(api, p, ride["id"], accepted_fee=1000)
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cancellation_fee"]["paise"] == 1000

    state = (await api.c.get("/api/passenger/state", headers=p["h"])).json()
    assert state["outstanding_fees"][0]["amount"]["paise"] == 1000
    blocked = await request(api, p, expect=402)
    assert blocked["code"] == "fee_outstanding"

    admin = await api.admin()
    fees = (await api.c.get("/api/admin/fees", headers=admin["h"])).json()
    assert len(fees) == 1 and fees[0]["ride_id"] == ride["id"]
    r = await api.c.post(f"/api/admin/fees/{fees[0]['payment_id']}", headers=admin["h"], json={"status": "PAID"})
    assert r.status_code == 200, r.text
    again = await api.c.post(f"/api/admin/fees/{fees[0]['payment_id']}", headers=admin["h"], json={"status": "WAIVED"})
    assert again.json()["code"] == "already_settled"

    await request(api, p)  # unblocked
    earnings = (await api.c.get("/api/driver/earnings", headers=driver["h"])).json()
    assert earnings["all_time"]["cancellation_fees_paise"] == 1000
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count(AuditLog.id)).where(AuditLog.action == "fee.settle"))).scalar_one() == 1


async def test_fee_is_half_the_fare_when_above_the_minimum(api):
    _, p, ride = await assigned_ride(api, passengers=3, boost=500)
    await set_samples(ride["id"], [(35, 800), (0, 400)])
    assert (await quote(api, p, ride["id"]))["fee"]["paise"] == 2250


async def test_cancelling_is_free_when_driver_is_stationary(api):
    _, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(40, 600), (20, 598), (0, 595)])  # GPS jitter, not progress
    q = await quote(api, p, ride["id"])
    assert q["fee"] is None and q["approaching"] is False
    cancelled = await cancel(api, p, ride["id"])
    assert cancelled["cancellation_fee"] is None
    await request(api, p)  # not blocked


async def test_cancelling_is_free_when_driver_moves_away(api):
    _, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(40, 500), (0, 750)])
    assert (await quote(api, p, ride["id"]))["fee"] is None


async def test_only_the_last_window_counts(api):
    # Drove towards the pickup a minute ago, then stopped for the last 30+ seconds: free.
    _, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(90, 1200), (45, 600), (31, 600), (0, 600)])
    assert (await quote(api, p, ride["id"]))["fee"] is None


async def test_cancelling_is_free_when_driver_stopped_reporting(api):
    _, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(80, 900), (45, 600)])  # approached, but nothing in the last 30 s
    assert (await quote(api, p, ride["id"]))["fee"] is None


async def test_no_fee_before_a_driver_accepts(api):
    await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await request(api, p)
    assert ride["status"] == "OFFERED"
    assert (await quote(api, p, ride["id"]))["fee"] is None
    assert (await cancel(api, p, ride["id"]))["cancellation_fee"] is None


async def test_admin_cancellation_never_charges_the_passenger(api):
    _, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(40, 900), (0, 600)])
    admin = await api.admin()
    r = await api.c.post(f"/api/admin/rides/{ride['id']}/cancel", headers=admin["h"], json={"reason": "Vehicle breakdown"})
    assert r.status_code == 200, r.text
    assert r.json()["cancellation_fee"] is None
    await request(api, p)


async def test_waived_fee_unblocks_booking(api):
    _, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(40, 900), (0, 600)])
    await cancel(api, p, ride["id"], accepted_fee=1000)
    admin = await api.admin()
    fee = (await api.c.get("/api/admin/fees", headers=admin["h"])).json()[0]
    r = await api.c.post(f"/api/admin/fees/{fee['payment_id']}", headers=admin["h"], json={"status": "WAIVED", "note": "First time"})
    assert r.status_code == 200, r.text
    await request(api, p)


async def test_released_ride_forgets_the_previous_drivers_approach(api):
    driver, p, ride = await assigned_ride(api)
    await set_samples(ride["id"], [(40, 900), (0, 600)])
    await api.act(driver, ride["id"], "cancel", {"reason": "Puncture"})
    async with SessionLocal() as db:
        left = (await db.execute(select(func.count(RideApproachSample.id)).where(
            RideApproachSample.ride_id == uuid.UUID(ride["id"])))).scalar_one()
    assert left == 0


# --------------------------------------------------------------------------- start PIN

async def test_trip_starts_only_with_the_passengers_pin(api):
    driver, p, ride = await assigned_ride(api)
    view = (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=p["h"])).json()
    pin = view["start_pin"]
    assert len(pin) == 4 and pin.isdigit()
    assert "start_pin" not in (await api.driver_state(driver))["active_ride"]  # the driver never sees it

    await api.locate(driver, LIBRARY)
    await api.act(driver, ride["id"], "arrived")
    wrong = "0000" if pin != "0000" else "1111"
    r = await api.act(driver, ride["id"], "start", {"pin": wrong}, expect=422)
    assert r["code"] == "wrong_pin"
    assert (await api.act(driver, ride["id"], "start", {"pin": pin}))["status"] == "IN_PROGRESS"
    view = (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=p["h"])).json()
    assert "start_pin" not in view  # no longer shown once on board


async def test_pin_guessing_is_rate_limited(api):
    driver, _, ride = await assigned_ride(api)
    await api.locate(driver, LIBRARY)
    await api.act(driver, ride["id"], "arrived")
    codes = []
    for guess in range(6):
        r = await api.c.post(f"/api/driver/rides/{ride['id']}/start", headers=driver["h"], json={"pin": f"{guess:04d}x"})
        codes.append(r.status_code)
    assert codes[-1] == 429


# --------------------------------------------------------------------------- demand map

async def test_demand_map_groups_recent_pickups_into_cells(api):
    driver = await api.driver(at=None, online=False)
    for _ in range(3):
        p = await api.passenger()
        await request(api, p)  # all at the Library, still searching (no driver online)
    data = (await api.c.get("/api/driver/demand", headers=driver["h"])).json()
    assert data["window_min"] == 60 and data["cell_m"] == 150
    top = data["cells"][0]
    assert top["requests"] == 3 and top["waiting"] == 3
    assert abs(top["lat"] - LIBRARY[0]) < 0.002 and abs(top["lng"] - LIBRARY[1]) < 0.002
    assert "passenger" not in str(data)


async def test_demand_map_is_for_drivers_only(api):
    p = await api.passenger()
    assert (await api.c.get("/api/driver/demand", headers=p["h"])).status_code == 403
