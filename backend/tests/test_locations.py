from .conftest import LIBRARY, OUTSIDE_CAMPUS, now_iso


async def post(api, d, **overrides):
    body = {"lat": LIBRARY[0], "lng": LIBRARY[1], "accuracy_m": 10, "timestamp": now_iso(), "source": "gps"}
    body.update(overrides)
    return await api.c.post("/api/driver/location", headers=d["h"], json=body)


async def test_valid_location_is_stored_with_freshness(api):
    d = await api.driver(at=None)
    r = await post(api, d)
    assert r.status_code == 200 and r.json()["accepted"] and r.json()["inside_campus"]
    state = await api.driver_state(d)
    assert state["location"]["source"] == "gps"
    assert state["location"]["recorded_at"]
    assert state["location_eligible"] is True


async def test_invalid_coordinates_rejected(api):
    d = await api.driver(at=None)
    assert (await post(api, d, lat=91)).status_code == 422
    assert (await post(api, d, lng=-181)).status_code == 422
    assert (await post(api, d, accuracy_m=0)).status_code == 422
    assert (await post(api, d, heading=400)).status_code == 422


async def test_stale_future_and_inaccurate_readings_rejected(api):
    d = await api.driver(at=None)
    r = await post(api, d, timestamp=now_iso(-600))
    assert r.status_code == 422 and r.json()["code"] == "stale_location"
    r = await post(api, d, timestamp=now_iso(+600))
    assert r.status_code == 422 and r.json()["code"] == "clock_skew"
    r = await post(api, d, accuracy_m=450)
    assert r.status_code == 422 and r.json()["code"] == "low_accuracy"


async def test_out_of_order_reading_is_ignored(api):
    d = await api.driver(at=None)
    assert (await post(api, d, timestamp=now_iso(-5))).json()["accepted"]
    late = await post(api, d, lat=29.8700, timestamp=now_iso(-20))
    assert late.json() == {"accepted": False, "reason": "out_of_order"}
    assert (await api.driver_state(d))["location"]["lat"] == LIBRARY[0]


async def test_outside_campus_is_flagged_and_ineligible(api):
    d = await api.driver(at=None)
    r = await post(api, d, lat=OUTSIDE_CAMPUS[0], lng=OUTSIDE_CAMPUS[1])
    assert r.status_code == 200
    assert r.json()["inside_campus"] is False and "outside" in r.json()["warning"]
    assert (await api.driver_state(d))["location_eligible"] is False


async def test_location_requires_driver_to_be_online(api):
    d = await api.driver(at=None, online=False)
    r = await post(api, d)
    assert r.status_code == 409 and r.json()["code"] == "offline"


async def test_passengers_cannot_report_driver_locations(api):
    p = await api.passenger()
    r = await api.c.post("/api/driver/location", headers=p["h"], json={
        "lat": LIBRARY[0], "lng": LIBRARY[1], "accuracy_m": 5, "timestamp": now_iso()})
    assert r.status_code == 403


async def test_simulator_source_is_labelled(api):
    d = await api.driver(at=None)
    assert (await post(api, d, source="simulator")).status_code == 200
    assert (await api.driver_state(d))["location"]["source"] == "simulator"
    assert (await post(api, d, source="teleport")).status_code == 422
