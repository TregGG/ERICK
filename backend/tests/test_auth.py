import uuid
from datetime import timedelta

from sqlalchemy import select, update

from backend.db import SessionLocal, utcnow
from backend.models import AuditLog, OtpChallenge, User


async def test_otp_login_creates_passenger_and_profile(api, client):
    p = await api.login("passenger", name="Aditi")
    me = await client.get("/api/auth/me", headers=p["h"])
    assert me.status_code == 200
    assert me.json()["role"] == "passenger"
    assert me.json()["phone"] == "+91" + p["phone"]
    assert me.json()["name"] == "Aditi"


async def test_otp_is_stored_hashed(api, client):
    phone = api.next_phone()
    r = await client.post("/api/auth/otp/request", json={"phone": phone, "app": "passenger"})
    code = r.json()["dev_otp"]
    async with SessionLocal() as db:
        challenge = (await db.execute(select(OtpChallenge))).scalar_one()
    assert code not in challenge.code_hash
    assert len(challenge.code_hash) == 64


async def test_wrong_code_counts_attempts_and_locks(api, client):
    phone = api.next_phone()
    r = await client.post("/api/auth/otp/request", json={"phone": phone, "app": "passenger"})
    real = r.json()["dev_otp"]
    wrong = "000000" if real != "000000" else "111111"
    for attempt in range(5):
        r = await client.post("/api/auth/otp/verify", json={"phone": phone, "app": "passenger", "code": wrong})
        assert r.status_code == 400
    # After the attempt limit even the correct code is refused.
    r = await client.post("/api/auth/otp/verify", json={"phone": phone, "app": "passenger", "code": real})
    assert r.status_code == 400
    assert "expired" in r.json()["detail"].lower() or "request a new" in r.json()["detail"].lower()


async def test_expired_code_rejected(api, client):
    phone = api.next_phone()
    r = await client.post("/api/auth/otp/request", json={"phone": phone, "app": "passenger"})
    code = r.json()["dev_otp"]
    async with SessionLocal() as db:
        await db.execute(update(OtpChallenge).values(expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    r = await client.post("/api/auth/otp/verify", json={"phone": phone, "app": "passenger", "code": code})
    assert r.status_code == 400
    assert "expired" in r.json()["detail"].lower()


async def test_resend_cooldown_and_old_code_invalidated(api, client):
    phone = api.next_phone()
    r1 = await client.post("/api/auth/otp/request", json={"phone": phone, "app": "passenger"})
    r2 = await client.post("/api/auth/otp/request", json={"phone": phone, "app": "passenger"})
    assert r2.status_code == 429
    async with SessionLocal() as db:  # pretend the cooldown passed
        await db.execute(update(OtpChallenge).values(created_at=utcnow() - timedelta(seconds=60)))
        await db.commit()
    r3 = await client.post("/api/auth/otp/request", json={"phone": phone, "app": "passenger"})
    assert r3.status_code == 200
    old = await client.post("/api/auth/otp/verify", json={"phone": phone, "app": "passenger", "code": r1.json()["dev_otp"]})
    if r1.json()["dev_otp"] != r3.json()["dev_otp"]:
        assert old.status_code == 400


async def test_invalid_phone_rejected(client):
    r = await client.post("/api/auth/otp/request", json={"phone": "12ab", "app": "passenger"})
    assert r.status_code == 422


async def test_role_cannot_be_switched_at_login(api, client):
    p = await api.passenger()
    r = await client.post("/api/auth/otp/request", json={"phone": p["phone"], "app": "driver"})
    assert r.status_code == 403
    assert "passenger" in r.json()["detail"]


async def test_admin_accounts_cannot_self_register(api, client):
    r = await client.post("/api/auth/otp/request", json={"phone": api.next_phone(), "app": "admin"})
    assert r.status_code == 403


async def test_logout_revokes_token(api, client):
    p = await api.passenger()
    assert (await client.post("/api/auth/logout", headers=p["h"])).status_code == 200
    assert (await client.get("/api/auth/me", headers=p["h"])).status_code == 401


async def test_tampered_and_missing_tokens_rejected(api, client):
    p = await api.passenger()
    assert (await client.get("/api/auth/me")).status_code == 401
    bad = {"Authorization": "Bearer " + p["token"][:-2] + "xx"}
    assert (await client.get("/api/auth/me", headers=bad)).status_code == 401


async def test_suspended_user_is_locked_out(api, client):
    p = await api.passenger()
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == uuid.UUID(p["user"]["id"])).values(status="suspended"))
        await db.commit()
    assert (await client.get("/api/auth/me", headers=p["h"])).status_code == 403


async def test_role_authorization_is_enforced_server_side(api, client):
    p = await api.passenger()
    d = await api.driver(online=False)
    assert (await client.get("/api/driver/state", headers=p["h"])).status_code == 403
    assert (await client.post("/api/driver/online", headers=p["h"], json={"online": True})).status_code == 403
    assert (await client.get("/api/admin/overview", headers=p["h"])).status_code == 403
    assert (await client.get("/api/admin/overview", headers=d["h"])).status_code == 403
    assert (await client.get("/api/passenger/state", headers=d["h"])).status_code == 403
    admin = await api.admin()
    assert (await client.get("/api/admin/overview", headers=admin["h"])).status_code == 200
    async with SessionLocal() as db:
        actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert "admin.login" in actions
