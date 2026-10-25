from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db, utcnow
from ..models import AuthSession, DriverProfile, OtpChallenge, User, Vehicle
from ..realtime import manager
from ..security import (
    Principal, client_ip, create_access_token, current_principal, generate_otp, hash_otp, new_session,
    normalize_phone, otp_matches, rate_limiter,
)
from ..services import sms
from ..services.audit import audit
from ..services.rides import clean_text

router = APIRouter(prefix="/api/auth", tags=["auth"])
AppRole = Literal["passenger", "driver", "admin"]


class PhoneBody(BaseModel):
    phone: str
    app: AppRole

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value: str) -> str:
        return normalize_phone(value)


class VerifyBody(PhoneBody):
    code: str = Field(pattern=r"^\d{6}$")
    name: Optional[str] = Field(default=None, max_length=80)


class ProfileBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)


async def user_payload(db: AsyncSession, user: User) -> dict:
    data = {"id": str(user.id), "phone": user.phone, "name": user.name, "role": user.role, "status": user.status}
    if user.role == "driver":
        profile = await db.get(DriverProfile, user.id)
        vehicle = (await db.execute(select(Vehicle).where(Vehicle.driver_id == user.id))).scalar_one_or_none()
        data["driver"] = {
            "verification_status": profile.verification_status if profile else "pending",
            "is_online": bool(profile and profile.is_online),
            "vehicle_number": vehicle.registration_number if vehicle else None,
        }
    return data


def _role_mismatch(user: User, app: str) -> HTTPException:
    return HTTPException(403, f"This number is registered as a {user.role}. Please use the {user.role} app.")


@router.post("/otp/request")
async def request_otp(body: PhoneBody, request: Request, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    rate_limiter.check("otp_request_ip", ip, limit=settings.OTP_REQUESTS_PER_IP_PER_10_MIN, window_s=600)

    user = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    if user is not None and user.role != body.app:
        raise _role_mismatch(user, body.app)
    if user is None and body.app == "admin":
        raise HTTPException(403, "Administrator accounts are created by the transport office.")
    if user is not None and user.status != "active":
        raise HTTPException(403, "This account is suspended. Contact the campus transport office.")

    now = utcnow()
    last = (await db.execute(
        select(OtpChallenge).where(OtpChallenge.phone == body.phone).order_by(OtpChallenge.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if last is not None:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - (now - last.created_at).total_seconds()
        if wait > 0:
            raise HTTPException(429, f"Please wait {int(wait) + 1} seconds before requesting another code.",
                                headers={"Retry-After": str(int(wait) + 1)})
    sent_last_hour = (await db.execute(
        select(func.count(OtpChallenge.id)).where(OtpChallenge.phone == body.phone,
                                                  OtpChallenge.created_at >= now - timedelta(hours=1))
    )).scalar_one()
    if sent_last_hour >= settings.OTP_MAX_PER_HOUR:
        raise HTTPException(429, "Too many codes requested for this number. Try again in an hour.")

    # Older unused codes stop working as soon as a new one is issued.
    await db.execute(update(OtpChallenge).where(OtpChallenge.phone == body.phone, OtpChallenge.consumed_at.is_(None))
                     .values(consumed_at=now))
    code = generate_otp()
    db.add(OtpChallenge(phone=body.phone, app_role=body.app, code_hash=hash_otp(body.phone, code),
                        expires_at=now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS), request_ip=ip, created_at=now))
    try:
        await sms.send_otp(body.phone, code)
    except sms.SmsError:
        await db.rollback()
        raise HTTPException(503, "We could not send the code right now. Please try again shortly.")
    await db.commit()

    response = {"expires_in": settings.OTP_EXPIRY_SECONDS, "resend_in": settings.OTP_RESEND_COOLDOWN_SECONDS,
                "new_account": user is None}
    if settings.OTP_DEV_MODE and not settings.is_production:
        response["dev_otp"] = code
    return response


@router.post("/otp/verify")
async def verify_otp(body: VerifyBody, request: Request, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    rate_limiter.check("otp_verify_ip", ip, limit=30, window_s=600)
    now = utcnow()
    challenge = (await db.execute(
        select(OtpChallenge).where(OtpChallenge.phone == body.phone, OtpChallenge.app_role == body.app,
                                   OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if challenge is None or challenge.expires_at <= now:
        raise HTTPException(400, "This code has expired. Request a new one.")
    if challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many incorrect attempts. Request a new code.")
    if not otp_matches(body.phone, body.code, challenge.code_hash):
        challenge.attempts += 1
        remaining = settings.OTP_MAX_ATTEMPTS - challenge.attempts
        if remaining <= 0:
            challenge.consumed_at = now
        await db.commit()
        raise HTTPException(400, f"Incorrect code. {remaining} attempt(s) left." if remaining > 0
                            else "Too many incorrect attempts. Request a new code.")
    challenge.consumed_at = now

    user = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    if user is None:
        if body.app == "admin":
            raise HTTPException(403, "Administrator accounts are created by the transport office.")
        user = User(phone=body.phone, role=body.app, name=clean_text(body.name, 80), status="active", created_at=now)
        db.add(user)
        await db.flush()
        if body.app == "driver":
            db.add(DriverProfile(user_id=user.id, verification_status="pending"))
        audit(db, user, "account.created", "user", user.id, {"role": body.app}, ip)
    elif user.role != body.app:
        raise _role_mismatch(user, body.app)
    elif user.status != "active":
        raise HTTPException(403, "This account is suspended. Contact the campus transport office.")

    session = new_session(user, request.headers.get("user-agent"))
    db.add(session)
    if user.role == "admin":
        audit(db, user, "admin.login", "user", user.id, None, ip)
    await db.commit()
    return {"access_token": create_access_token(user, session), "token_type": "bearer",
            "expires_at": session.expires_at.isoformat(), "user": await user_payload(db, user)}


@router.post("/logout")
async def logout(principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    session = await db.get(AuthSession, principal.session_id)
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    return await user_payload(db, principal.user)


@router.patch("/me")
async def update_me(body: ProfileBody, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    name = clean_text(body.name, 80)
    if not name or len(name) < 2:
        raise HTTPException(422, "Enter your name (at least 2 characters).")
    principal.user.name = name
    await db.commit()
    return await user_payload(db, principal.user)


async def revoke_all_sessions(db: AsyncSession, user_id) -> None:
    await db.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                     .values(revoked_at=utcnow()))


async def disconnect_user(user_id, reason: str) -> None:
    await manager.close_user(user_id, 4403, reason)
