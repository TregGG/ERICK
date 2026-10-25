from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import AuditLog

from .conftest import LIBRARY


async def test_driver_verification_flow(api):
    admin = await api.admin()
    d = await api.driver(at=None, approved=False, online=False)
    r = await api.c.post("/api/driver/online", headers=d["h"], json={"online": True})
    assert r.status_code == 403
    drivers = (await api.c.get("/api/admin/drivers", headers=admin["h"])).json()
    assert drivers[0]["verification_status"] == "pending"
    r = await api.c.post(f"/api/admin/drivers/{d['user']['id']}/verification", headers=admin["h"],
                         json={"status": "approved", "note": "Licence checked"})
    assert r.status_code == 200
    assert (await api.c.post("/api/driver/online", headers=d["h"], json={"online": True})).status_code == 200

    # Suspension takes the driver offline immediately.
    await api.locate(d, LIBRARY)
    r = await api.c.post(f"/api/admin/drivers/{d['user']['id']}/verification", headers=admin["h"], json={"status": "suspended"})
    state = await api.driver_state(d)
    assert state["profile"]["is_online"] is False and state["profile"]["verification_status"] == "suspended"


async def test_settings_are_validated_audited_and_applied(api):
    admin = await api.admin()
    r = await api.c.put("/api/admin/settings", headers=admin["h"], json={"fare_paise": 1.5})
    assert r.status_code == 422
    r = await api.c.put("/api/admin/settings", headers=admin["h"], json={"surge": 2})
    assert r.status_code == 422
    r = await api.c.put("/api/admin/settings", headers=admin["h"], json={"fare_paise": 1500, "offer_timeout_s": 30})
    assert r.status_code == 200 and r.json()["changed"]["fare_paise"] == {"from": 1000, "to": 1500}

    p = await api.passenger()
    ride = await api.request_ride(p)
    assert ride["fare"]["paise"] == 1500 and ride["fare"]["display"] == "₹15"
    async with SessionLocal() as db:
        actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert "settings.update" in actions


async def test_admin_cancel_and_user_suspension(api):
    admin = await api.admin()
    p = await api.passenger()
    ride = await api.request_ride(p)
    r = await api.c.post(f"/api/admin/rides/{ride['id']}/cancel", headers=admin["h"], json={"reason": "Duplicate booking"})
    assert r.status_code == 200 and r.json()["cancelled_by"] == "admin"

    r = await api.c.post(f"/api/admin/users/{p['user']['id']}/status", headers=admin["h"], json={"status": "suspended"})
    assert r.status_code == 200
    assert (await api.c.get("/api/auth/me", headers=p["h"])).status_code == 401  # sessions revoked
    assert (await api.c.post("/api/auth/otp/request", json={"phone": p["phone"], "app": "passenger"})).status_code == 403

    r = await api.c.post(f"/api/admin/users/{admin['user']['id']}/status", headers=admin["h"], json={"status": "suspended"})
    assert r.status_code == 409  # cannot lock yourself out


async def test_reports_and_audit_require_admin(api):
    admin = await api.admin()
    p = await api.passenger()
    assert (await api.c.get("/api/admin/reports", headers=p["h"])).status_code == 403
    assert (await api.c.get("/api/admin/audit", headers=p["h"])).status_code == 403
    assert (await api.c.get("/api/admin/reports", headers=admin["h"])).status_code == 200
    audit = (await api.c.get("/api/admin/audit", headers=admin["h"])).json()
    assert audit and all("otp" not in str(entry).lower() for entry in audit)


async def test_landmarks_are_verified_campus_data(api):
    r = await api.c.get("/api/campus")
    data = r.json()
    assert "OpenStreetMap" in data["data_source"]
    assert any(l["name"] == "Mahatma Gandhi Central Library" for l in data["landmarks"])
    assert data["zones"][0]["kind"] == "boundary"
    admin = await api.admin()
    r = await api.c.post("/api/admin/landmarks", headers=admin["h"],
                         json={"name": "Off campus", "category": "test", "lat": 29.95, "lng": 77.99})
    assert r.status_code == 422
