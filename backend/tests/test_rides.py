import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update

from backend.db import SessionLocal, utcnow
from backend.models import DriverLocation, Payment, Ride, RideEvent, RideOffer
from backend.services import dispatch

from .conftest import CENTURY_GATE, HOSPITAL, KASTURBA_BHAWAN, LIBRARY, OUTSIDE_CAMPUS, RAJENDRA_BHAWAN


async def test_ride_request_validation(api):
    p = await api.passenger()
    r = await api.request_ride(p, pickup=OUTSIDE_CAMPUS, expect=422)
    assert r["code"] == "outside_campus"
    r = await api.request_ride(p, pickup=LIBRARY, dropoff=(LIBRARY[0] + 0.0001, LIBRARY[1]), expect=422)
    assert r["code"] == "trip_too_short"
    r = await api.c.post("/api/passenger/rides", headers=p["h"], json={
        "pickup": {"lat": 120, "lng": 77.9}, "dropoff": {"lat": 29.86, "lng": 77.89}})
    assert r.status_code == 422


async def test_fare_is_integer_paise_and_route_is_labelled_approximate(api):
    p = await api.passenger()
    ride = await api.request_ride(p)
    assert ride["fare"] == {"paise": 1000, "currency": "INR", "display": "₹10"}
    # Routing is disabled in tests, so the distance must be flagged as a straight-line approximation.
    assert ride["route"]["source"] == "straight_line"
    assert ride["route"]["approximate"] is True


async def test_no_eligible_driver_keeps_searching_then_expires(api):
    p = await api.passenger()
    ride = await api.request_ride(p)
    assert ride["status"] == "REQUESTED"
    async with SessionLocal() as db:
        await db.execute(update(Ride).values(search_expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    await dispatch.tick()
    state = (await api.c.get("/api/passenger/state", headers=p["h"])).json()
    assert state["active_ride"] is None
    history = (await api.c.get("/api/passenger/rides", headers=p["h"])).json()
    assert history[0]["status"] == "NO_DRIVER_AVAILABLE"


async def test_idempotent_ride_request(api):
    p = await api.passenger()
    first = await api.request_ride(p, key="tap-1")
    again = await api.request_ride(p, key="tap-1", expect=200)
    assert again["id"] == first["id"]
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count(Ride.id)))).scalar_one() == 1


async def test_concurrent_duplicate_requests_create_one_ride(api):
    p = await api.passenger()
    results = await asyncio.gather(*[
        api.c.post("/api/passenger/rides", headers={**p["h"], "Idempotency-Key": "double-tap"}, json={
            "pickup": {"lat": LIBRARY[0], "lng": LIBRARY[1]}, "dropoff": {"lat": RAJENDRA_BHAWAN[0], "lng": RAJENDRA_BHAWAN[1]}})
        for _ in range(6)])
    assert all(r.status_code in (200, 201, 409) for r in results)
    assert len({r.json()["id"] for r in results if r.status_code in (200, 201)}) == 1
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count(Ride.id)))).scalar_one() == 1


async def test_one_active_ride_per_passenger(api):
    p = await api.passenger()
    await api.request_ride(p)
    r = await api.request_ride(p, expect=409)
    assert r["code"] == "active_ride_exists"


async def test_passengers_cannot_see_or_cancel_each_others_rides(api):
    a, b = await api.passenger(), await api.passenger()
    ride = await api.request_ride(a)
    assert (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=b["h"])).status_code == 404
    assert (await api.c.post(f"/api/passenger/rides/{ride['id']}/cancel", headers=b["h"], json={})).status_code == 404
    assert (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=a["h"])).status_code == 200


async def test_full_lifecycle_with_persistence_and_history(api):
    driver = await api.driver(at=HOSPITAL, name="Ramesh Kumar")
    p = await api.passenger("Neha Gupta")
    ride = await api.request_ride(p)
    assert ride["status"] == "OFFERED"

    offer = (await api.driver_state(driver))["offer"]
    assert offer["ride_id"] == ride["id"]
    assert offer["passenger_first_name"] == "Neha"
    assert "phone" not in str(offer)  # no contact details before acceptance

    accepted = await api.act(driver, ride["id"], "accept")
    assert accepted["status"] == "ACCEPTED"
    assert accepted["passenger"]["phone"]  # shared once assigned

    view = (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=p["h"])).json()
    assert view["driver"]["name"] == "Ramesh Kumar"
    assert view["driver"]["vehicle_number"].startswith("UK 08 ER")
    assert view["driver_location"]["lat"] == HOSPITAL[0]

    # Arrival is only accepted near the pickup point.
    r = await api.act(driver, ride["id"], "arrived", expect=409)
    assert r["code"] == "not_at_pickup"
    await api.locate(driver, LIBRARY)
    assert (await api.act(driver, ride["id"], "arrived"))["status"] == "ARRIVED"

    r = await api.act(driver, ride["id"], "complete", expect=409)  # cannot skip IN_PROGRESS
    assert r["code"] == "invalid_transition"
    assert (await api.act(driver, ride["id"], "start"))["status"] == "IN_PROGRESS"
    await api.locate(driver, RAJENDRA_BHAWAN)
    done = await api.act(driver, ride["id"], "complete", {"cash_collected": True})
    assert done["status"] == "COMPLETED"
    assert done["payment"] == {"status": "PAID", "method": "cash"}

    detail = (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=p["h"])).json()
    assert [e["to"] for e in detail["timeline"]] == ["REQUESTED", "OFFERED", "ACCEPTED", "ARRIVED", "IN_PROGRESS", "COMPLETED"]
    assert detail["driver"]["phone"] is None  # contact details hidden after the trip
    assert "driver_location" not in detail

    earnings = (await api.c.get("/api/driver/earnings", headers=driver["h"])).json()
    assert earnings["all_time"]["trips"] == 1
    assert earnings["all_time"]["driver_share_paise"] == 1000
    assert earnings["all_time"]["cash_collected_paise"] == 1000

    r = await api.c.post(f"/api/rides/{ride['id']}/rating", headers=p["h"], json={"stars": 5, "comment": "Smooth"})
    assert r.status_code == 201
    assert (await api.c.post(f"/api/rides/{ride['id']}/rating", headers=p["h"], json={"stars": 4})).status_code == 409
    view = (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=p["h"])).json()
    assert view["driver"]["rating"] == {"average": 5.0, "count": 1}

    # The driver is free again and can receive the next offer.
    assert (await api.driver_state(driver))["active_ride"] is None


async def test_rejection_moves_to_next_driver_and_never_reoffers(api):
    near = await api.driver(at=LIBRARY, name="Near Driver")
    far = await api.driver(at=CENTURY_GATE, name="Far Driver")
    p = await api.passenger()
    ride = await api.request_ride(p)
    assert (await api.driver_state(near))["offer"]["ride_id"] == ride["id"]  # nearest first
    assert (await api.driver_state(far))["offer"] is None

    await api.act(near, ride["id"], "reject")
    assert (await api.driver_state(far))["offer"]["ride_id"] == ride["id"]
    await api.act(far, ride["id"], "reject")
    await dispatch.tick()
    assert (await api.driver_state(near))["offer"] is None  # rejected drivers are not asked again
    state = (await api.c.get("/api/passenger/state", headers=p["h"])).json()
    assert state["active_ride"]["status"] == "REQUESTED"


async def test_offer_timeout_moves_to_next_driver(api):
    first = await api.driver(at=LIBRARY)
    second = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await api.request_ride(p)
    async with SessionLocal() as db:
        await db.execute(update(RideOffer).values(expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    await dispatch.tick()
    assert (await api.driver_state(first))["offer"] is None
    assert (await api.driver_state(second))["offer"]["ride_id"] == ride["id"]
    r = await api.act(first, ride["id"], "accept", expect=409)  # late tap on an expired offer
    assert r["code"] == "offer_unavailable"
    async with SessionLocal() as db:
        statuses = sorted((await db.execute(select(RideOffer.status))).scalars())
    assert statuses == ["EXPIRED", "PENDING"]


async def test_competing_acceptance_cannot_double_assign(api):
    offered = await api.driver(at=LIBRARY)
    other = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await api.request_ride(p)

    # A driver without the offer cannot take the ride.
    r = await api.act(other, ride["id"], "accept", expect=409)
    assert r["code"] == "offer_unavailable"

    # The offered driver double-taps: many concurrent accepts, one assignment.
    results = await asyncio.gather(*[
        api.c.post(f"/api/driver/rides/{ride['id']}/accept", headers=offered["h"]) for _ in range(8)])
    assert any(r.status_code == 200 for r in results)
    assert all(r.status_code in (200, 409) for r in results)
    async with SessionLocal() as db:
        db_ride = await db.get(Ride, uuid.UUID(ride["id"]))
        accepted_events = (await db.execute(select(func.count(RideEvent.id)).where(
            RideEvent.ride_id == db_ride.id, RideEvent.to_status == "ACCEPTED"))).scalar_one()
    assert db_ride.status == "ACCEPTED"
    assert str(db_ride.driver_id) == offered["user"]["id"]
    assert accepted_events == 1


async def test_database_rejects_second_active_ride_for_a_driver(api):
    """The partial unique index is the final guard against double assignment."""
    from sqlalchemy.exc import IntegrityError
    driver = await api.driver(at=LIBRARY)
    p1, p2 = await api.passenger(), await api.passenger()
    r1 = await api.request_ride(p1)
    await api.act(driver, r1["id"], "accept")
    r2 = await api.request_ride(p2)
    async with SessionLocal() as db:
        try:
            await db.execute(update(Ride).where(Ride.id == uuid.UUID(r2["id"])).values(
                status="ACCEPTED", driver_id=uuid.UUID(driver["user"]["id"])))
            await db.commit()
            raised = False
        except IntegrityError:
            raised = True
    assert raised


async def test_passenger_cancel_while_offered_closes_offer(api):
    driver = await api.driver(at=LIBRARY)
    p = await api.passenger()
    ride = await api.request_ride(p)
    r = await api.c.post(f"/api/passenger/rides/{ride['id']}/cancel", headers=p["h"], json={"reason": "Plans changed"})
    assert r.json()["status"] == "CANCELLED"
    assert (await api.driver_state(driver))["offer"] is None
    assert (await api.act(driver, ride["id"], "accept", expect=409))["code"] == "offer_unavailable"
    again = await api.c.post(f"/api/passenger/rides/{ride['id']}/cancel", headers=p["h"], json={})
    assert again.status_code == 200  # repeating a cancel is harmless


async def test_passenger_cancel_after_accept_frees_driver(api):
    driver = await api.driver(at=LIBRARY)
    p1, p2 = await api.passenger(), await api.passenger()
    ride = await api.request_ride(p1)
    await api.act(driver, ride["id"], "accept")
    await api.c.post(f"/api/passenger/rides/{ride['id']}/cancel", headers=p1["h"], json={})
    assert (await api.driver_state(driver))["active_ride"] is None
    second = await api.request_ride(p2)
    assert (await api.driver_state(driver))["offer"]["ride_id"] == second["id"]


async def test_driver_release_reassigns_to_another_driver(api):
    first = await api.driver(at=LIBRARY)
    second = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await api.request_ride(p)
    await api.act(first, ride["id"], "accept")
    released = await api.act(first, ride["id"], "cancel", {"reason": "Puncture"})
    assert released["status"] == "REQUESTED"
    assert (await api.driver_state(second))["offer"]["ride_id"] == ride["id"]
    await api.act(second, ride["id"], "accept")
    view = (await api.c.get(f"/api/passenger/rides/{ride['id']}", headers=p["h"])).json()
    assert view["status"] == "ACCEPTED"


async def test_no_show_requires_waiting(api):
    driver = await api.driver(at=LIBRARY)
    p = await api.passenger()
    ride = await api.request_ride(p)
    await api.act(driver, ride["id"], "accept")
    await api.act(driver, ride["id"], "arrived")
    assert (await api.act(driver, ride["id"], "cancel", expect=409))["code"] == "no_show_too_early"
    async with SessionLocal() as db:
        await db.execute(update(Ride).values(arrived_at=utcnow() - timedelta(minutes=5)))
        await db.commit()
    assert (await api.act(driver, ride["id"], "cancel"))["status"] == "CANCELLED"


async def test_ineligible_drivers_are_not_offered_rides(api):
    stale = await api.driver(at=LIBRARY, name="Stale")
    outside = await api.driver(at=None, name="Outside")
    await api.locate(outside, OUTSIDE_CAMPUS, expect=200)
    pending = await api.driver(at=None, approved=False, online=False, name="Pending")
    offline = await api.driver(at=LIBRARY, name="Offline")
    await api.c.post("/api/driver/online", headers=offline["h"], json={"online": False})
    async with SessionLocal() as db:
        await db.execute(update(DriverLocation).where(DriverLocation.driver_id == uuid.UUID(stale["user"]["id"]))
                         .values(received_at=utcnow() - timedelta(minutes=5)))
        await db.commit()

    r = await api.c.post("/api/driver/online", headers=pending["h"], json={"online": True})
    assert r.status_code == 403 and r.json()["code"] == "not_verified"

    p = await api.passenger()
    ride = await api.request_ride(p)
    assert ride["status"] == "REQUESTED"
    for d in (stale, outside, offline):
        assert (await api.driver_state(d))["offer"] is None

    nearby = (await api.c.get("/api/passenger/nearby-drivers", params={"lat": LIBRARY[0], "lng": LIBRARY[1]}, headers=p["h"])).json()
    assert nearby["count"] == 0

    fresh = await api.driver(at=KASTURBA_BHAWAN, name="Fresh")
    await dispatch.tick()
    assert (await api.driver_state(fresh))["offer"]["ride_id"] == ride["id"]


async def test_driver_cannot_go_offline_mid_trip_and_offline_clears_location(api):
    driver = await api.driver(at=LIBRARY)
    p = await api.passenger()
    ride = await api.request_ride(p)
    await api.act(driver, ride["id"], "accept")
    r = await api.c.post("/api/driver/online", headers=driver["h"], json={"online": False})
    assert r.status_code == 409
    await api.c.post(f"/api/passenger/rides/{ride['id']}/cancel", headers=p["h"], json={})
    r = await api.c.post("/api/driver/online", headers=driver["h"], json={"online": False})
    assert r.status_code == 200
    assert r.json()["location"] is None  # position is not retained once off duty


async def test_offline_with_pending_offer_redispatches(api):
    first = await api.driver(at=LIBRARY)
    second = await api.driver(at=HOSPITAL)
    p = await api.passenger()
    ride = await api.request_ride(p)
    await api.c.post("/api/driver/online", headers=first["h"], json={"online": False})
    assert (await api.driver_state(second))["offer"]["ride_id"] == ride["id"]


async def test_cash_pending_can_be_collected_later(api):
    driver = await api.driver(at=LIBRARY)
    p = await api.passenger()
    ride = await api.request_ride(p)
    for action in ("accept", "arrived", "start"):
        await api.act(driver, ride["id"], action)
    done = await api.act(driver, ride["id"], "complete", {"cash_collected": False})
    assert done["payment"]["status"] == "PENDING"
    assert (await api.act(driver, ride["id"], "collect-cash"))["status"] == "PAID"
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count(Payment.id)))).scalar_one() == 1


async def test_passenger_map_shows_all_online_rickshaws_free_and_busy(api):
    busy = await api.driver(at=HOSPITAL, name="Busy")
    ride = await api.request_ride(await api.passenger(), pickup=HOSPITAL)
    await api.act(busy, ride["id"], "accept")
    free = await api.driver(at=LIBRARY, name="Free")
    await api.driver(at=KASTURBA_BHAWAN, online=False, name="Offline")

    p = await api.passenger()
    # Without a pickup: every online rickshaw on campus, free ones first.
    r = await api.c.get("/api/passenger/nearby-drivers", headers=p["h"])
    assert r.status_code == 200, r.text
    nearby = r.json()
    assert (nearby["online"], nearby["available"], nearby["count"]) == (2, 1, 1)
    assert [d["busy"] for d in nearby["drivers"]] == [False, True]
    assert all(d["distance_m"] is None for d in nearby["drivers"])
    # Positions stay approximate (~100 m) so passengers can't track an individual driver.
    assert nearby["drivers"][0]["lat"] == round(LIBRARY[0], 3)

    # With a pickup: count is the free rickshaws that could take the ride, and distances are filled in.
    nearby = (await api.c.get("/api/passenger/nearby-drivers", params={"lat": LIBRARY[0], "lng": LIBRARY[1]},
                              headers=p["h"])).json()
    assert nearby["count"] == 1 and nearby["online"] == 2
    assert nearby["drivers"][0]["busy"] is False and nearby["drivers"][0]["distance_m"] < 150
    assert free  # the free driver is the one listed first
