"""Administrator API. Every route requires an admin session; every mutation is audit-logged."""
import json
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db, utcnow
from ..models import (
    AuditLog, CampusZone, DriverLocation, DriverProfile, Landmark, Payment, Rating, Ride, RideOffer, User, Vehicle,
)
from ..realtime import commit_and_publish, queue_event
from ..security import Principal, client_ip, require_admin
from ..services import dispatch
from ..services import ops_settings
from ..services import rides as ride_service
from ..services.audit import audit
from ..services.geo import get_campus
from ..states import ACTIVE, OfferStatus, RideStatus, values
from .auth import disconnect_user, revoke_all_sessions

router = APIRouter(prefix="/api/admin", tags=["admin"])


class VerificationIn(BaseModel):
    status: Literal["approved", "rejected", "suspended", "pending"]
    note: Optional[str] = Field(default=None, max_length=200)


class UserStatusIn(BaseModel):
    status: Literal["active", "suspended"]
    reason: Optional[str] = Field(default=None, max_length=200)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class FeeSettleIn(BaseModel):
    status: Literal["PAID", "WAIVED"]
    note: Optional[str] = Field(default=None, max_length=200)


class LandmarkIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=24)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    is_pickup_point: bool = False


class LandmarkPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    is_active: Optional[bool] = None
    is_pickup_point: Optional[bool] = None


async def _force_offline(db: AsyncSession, driver_id) -> Optional[uuid.UUID]:
    """Take a driver off duty and release any pending offer. Returns the ride id to re-dispatch."""
    profile = await db.get(DriverProfile, driver_id)
    if profile:
        profile.is_online, profile.online_since = False, None
    offer = await ride_service.pending_offer_for_driver(db, driver_id)
    if offer is None:
        return None
    ride = await db.get(Ride, offer.ride_id)
    offer.status, offer.responded_at = OfferStatus.CANCELLED.value, utcnow()
    if ride.status == RideStatus.OFFERED.value:
        await ride_service.transition(db, ride, RideStatus.REQUESTED, "admin", note="Driver taken offline by admin")
        await ride_service.publish(db, ride)
    return ride.id


@router.get("/overview")
async def overview(admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ops = await ops_settings.get_ops(db)
    now = utcnow()
    since = now - timedelta(hours=24)
    by_status = dict((await db.execute(
        select(Ride.status, func.count(Ride.id)).where(Ride.status.in_(values(ACTIVE))).group_by(Ride.status)
    )).all())
    last_day = dict((await db.execute(
        select(Ride.status, func.count(Ride.id)).where(Ride.created_at >= since).group_by(Ride.status)
    )).all())
    online = (await db.execute(select(func.count()).select_from(DriverProfile).where(DriverProfile.is_online.is_(True)))).scalar_one()
    fresh = (await db.execute(select(func.count()).select_from(DriverLocation).where(
        DriverLocation.received_at >= now - timedelta(seconds=ops.location_stale_s), DriverLocation.inside_campus.is_(True)))).scalar_one()
    pending = (await db.execute(select(func.count()).select_from(DriverProfile).where(DriverProfile.verification_status == "pending"))).scalar_one()
    return {"active_rides": by_status, "last_24h": last_day, "drivers_online": online,
            "drivers_with_fresh_location": fresh, "drivers_pending_verification": pending}


@router.get("/drivers")
async def drivers(admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ops = await ops_settings.get_ops(db)
    rows = (await db.execute(
        select(User, DriverProfile, Vehicle, DriverLocation)
        .join(DriverProfile, DriverProfile.user_id == User.id, isouter=True)
        .join(Vehicle, Vehicle.driver_id == User.id, isouter=True)
        .join(DriverLocation, DriverLocation.driver_id == User.id, isouter=True)
        .where(User.role == "driver").order_by(User.created_at.desc())
    )).all()
    completed = dict((await db.execute(
        select(Ride.driver_id, func.count(Ride.id)).where(Ride.status == RideStatus.COMPLETED.value).group_by(Ride.driver_id)
    )).all())
    ratings = {r[0]: (round(float(r[1]), 2), r[2]) for r in (await db.execute(
        select(Rating.ratee_id, func.avg(Rating.stars), func.count(Rating.id)).group_by(Rating.ratee_id))).all()}
    now = utcnow()
    result = []
    for user, profile, vehicle, loc in rows:
        age = int((now - loc.received_at).total_seconds()) if loc else None
        result.append({
            "id": str(user.id), "name": user.name, "phone": user.phone, "account_status": user.status,
            "verification_status": profile.verification_status if profile else "pending",
            "verification_note": profile.verification_note if profile else None,
            "is_online": bool(profile and profile.is_online),
            "vehicle_number": vehicle.registration_number if vehicle else None,
            "location_age_s": age, "location_fresh": age is not None and age <= ops.location_stale_s,
            "location_source": loc.source if loc else None,
            "completed_rides": completed.get(user.id, 0),
            "rating": ratings.get(user.id, (None, 0))[0], "rating_count": ratings.get(user.id, (None, 0))[1],
            "joined_at": ride_service.iso(user.created_at),
        })
    return result


@router.post("/drivers/{driver_id}/verification")
async def set_verification(driver_id: uuid.UUID, body: VerificationIn, request: Request,
                           admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    profile = await db.get(DriverProfile, driver_id)
    if profile is None:
        raise ride_service.ServiceError(404, "Driver not found", code="not_found")
    if body.status != "approved" and await ride_service.active_ride_for_driver(db, driver_id):
        raise ride_service.ServiceError(409, "This driver is on a trip. Cancel or wait for it to finish first.", code="on_trip")
    previous = profile.verification_status
    profile.verification_status = body.status
    profile.verification_note = ride_service.clean_text(body.note, 200)
    redispatch = await _force_offline(db, driver_id) if body.status != "approved" else None
    audit(db, admin.user, "driver.verification", "user", driver_id, {"from": previous, "to": body.status, "note": body.note}, client_ip(request))
    ride_service.notify(db, driver_id, "verification", f"Account {body.status}", profile.verification_note)
    queue_event(db, driver_id, "account.updated", {"verification_status": body.status})
    await commit_and_publish(db)
    if redispatch:
        await dispatch.dispatch_ride(redispatch)
    return {"id": str(driver_id), "verification_status": body.status}


@router.get("/users")
async def users(role: Optional[str] = Query(None, pattern="^(passenger|driver|admin)$"), q: Optional[str] = Query(None, max_length=40),
                admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    query = select(User)
    if role:
        query = query.where(User.role == role)
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.name.ilike(like), User.phone.like(like)))
    rows = (await db.execute(query.order_by(User.created_at.desc()).limit(200))).scalars()
    return [{"id": str(u.id), "name": u.name, "phone": u.phone, "role": u.role, "status": u.status,
             "joined_at": ride_service.iso(u.created_at)} for u in rows]


@router.post("/users/{user_id}/status")
async def set_user_status(user_id: uuid.UUID, body: UserStatusIn, request: Request,
                          admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if user_id == admin.id:
        raise ride_service.ServiceError(409, "You cannot change your own account status.", code="self_action")
    user = await db.get(User, user_id)
    if user is None:
        raise ride_service.ServiceError(404, "User not found", code="not_found")
    redispatch = None
    if body.status == "suspended":
        if user.role == "driver" and await ride_service.active_ride_for_driver(db, user.id):
            raise ride_service.ServiceError(409, "This driver is on a trip. Cancel or wait for it to finish first.", code="on_trip")
        if user.role == "passenger":
            active = await ride_service.active_ride_for_passenger(db, user.id)
            if active:
                await ride_service.cancel_ride(db, active, admin.user, "Account suspended")
        if user.role == "driver":
            redispatch = await _force_offline(db, user.id)
        await revoke_all_sessions(db, user.id)
    previous = user.status
    user.status = body.status
    audit(db, admin.user, "user.status", "user", user.id, {"from": previous, "to": body.status, "reason": body.reason}, client_ip(request))
    await commit_and_publish(db)
    if body.status == "suspended":
        await disconnect_user(user.id, "Account suspended")
    if redispatch:
        await dispatch.dispatch_ride(redispatch)
    return {"id": str(user.id), "status": user.status}


@router.get("/rides")
async def rides(status: Optional[str] = Query(None, max_length=24), active_only: bool = False,
                limit: int = Query(50, ge=1, le=200), admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    query = select(Ride)
    if active_only:
        query = query.where(Ride.status.in_(values(ACTIVE)))
    elif status:
        query = query.where(Ride.status == status)
    rows = (await db.execute(query.order_by(Ride.created_at.desc()).limit(limit))).scalars().all()
    return [await ride_service.ride_view(db, r, "admin") for r in rows]


@router.get("/rides/{ride_id}")
async def ride_detail(ride_id: str, admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ride = await ride_service.get_ride(db, ride_id)
    view = await ride_service.ride_view(db, ride, "admin")
    view["timeline"] = await ride_service.ride_events(db, ride.id)
    offers = (await db.execute(select(RideOffer, User).join(User, User.id == RideOffer.driver_id)
                               .where(RideOffer.ride_id == ride.id).order_by(RideOffer.created_at))).all()
    view["offers"] = [{"driver": u.name, "status": o.status, "distance_to_pickup_m": o.distance_to_pickup_m,
                       "created_at": ride_service.iso(o.created_at), "responded_at": ride_service.iso(o.responded_at)} for o, u in offers]
    return view


@router.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, body: CancelIn, request: Request,
                      admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ride = await ride_service.get_ride(db, ride_id)
    await ride_service.cancel_ride(db, ride, admin.user, body.reason)
    audit(db, admin.user, "ride.cancel", "ride", ride.id, {"reason": body.reason}, client_ip(request))
    await commit_and_publish(db)
    return await ride_service.ride_view(db, ride, "admin")


@router.get("/fees")
async def fees(status: Literal["PENDING", "PAID", "WAIVED"] = "PENDING", limit: int = Query(100, ge=1, le=500),
               admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Payment, Ride, User).join(Ride, Ride.id == Payment.ride_id).join(User, User.id == Ride.passenger_id)
        .where(Payment.kind == "cancellation_fee", Payment.status == status)
        .order_by(Payment.created_at.desc()).limit(limit)
    )).all()
    drivers = {u.id: u for u in (await db.execute(
        select(User).where(User.id.in_({r.driver_id for _, r, _ in rows if r.driver_id})))).scalars()}
    return [{
        "payment_id": str(pay.id), "ride_id": str(ride.id), "status": pay.status,
        "amount": ride_service.money(pay.amount_paise),
        "passenger": {"name": passenger.name, "phone": passenger.phone},
        "driver": drivers[ride.driver_id].name if ride.driver_id in drivers else None,
        "pickup": ride.pickup_name, "cancelled_at": ride_service.iso(ride.cancelled_at),
        "updated_at": ride_service.iso(pay.updated_at),
    } for pay, ride, passenger in rows]


@router.post("/fees/{payment_id}")
async def settle_fee(payment_id: uuid.UUID, body: FeeSettleIn, request: Request,
                     admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    payment = await db.get(Payment, payment_id)
    if payment is None or payment.kind != "cancellation_fee":
        raise ride_service.ServiceError(404, "Fee not found", code="not_found")
    if payment.status != "PENDING":
        raise ride_service.ServiceError(409, f"This fee is already {payment.status.lower()}.", code="already_settled")
    payment.status = body.status
    payment.collected_by = admin.id if body.status == "PAID" else None
    ride = await db.get(Ride, payment.ride_id)
    audit(db, admin.user, "fee.settle", "payment", payment.id,
          {"ride_id": str(payment.ride_id), "amount_paise": payment.amount_paise, "status": body.status, "note": body.note},
          client_ip(request))
    ride_service.notify(db, ride.passenger_id, "fee_settled",
                        f"Cancellation fee {'paid' if body.status == 'PAID' else 'waived'}",
                        "You can book rides again.", ride.id)
    await commit_and_publish(db)
    return {"payment_id": str(payment.id), "status": payment.status}


@router.get("/settings")
async def get_settings(admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ops = await ops_settings.get_ops(db, max_age=0)
    return {"values": asdict(ops), "limits": ops_settings.OpsSettings.LIMITS}


@router.put("/settings")
async def put_settings(changes: dict, request: Request, admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    current = await ops_settings.get_ops(db, max_age=0)
    try:
        updated = ops_settings.validate_update(current, changes)
    except ValueError as exc:
        raise ride_service.ServiceError(422, str(exc), code="invalid_setting")
    diff = {k: {"from": getattr(current, k), "to": v} for k, v in asdict(updated).items() if getattr(current, k) != v}
    await ops_settings.save_ops(db, updated, admin.id)
    audit(db, admin.user, "settings.update", "system_settings", ops_settings.OPS_KEY, diff, client_ip(request))
    await db.commit()
    return {"values": asdict(updated), "changed": diff}


@router.get("/landmarks")
async def list_landmarks(admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Landmark).order_by(Landmark.category, Landmark.name))).scalars()
    return [{"id": l.id, "name": l.name, "category": l.category, "lat": l.lat, "lng": l.lng,
             "is_pickup_point": l.is_pickup_point, "is_active": l.is_active, "source": l.source, "osm_ref": l.osm_ref}
            for l in rows]


@router.post("/landmarks", status_code=201)
async def add_landmark(body: LandmarkIn, request: Request, admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    campus = await get_campus(db)
    if not campus.contains(body.lat, body.lng):
        raise ride_service.ServiceError(422, "Landmarks must be inside the campus boundary.", code="outside_campus")
    landmark = Landmark(name=ride_service.clean_text(body.name, 120), category=ride_service.clean_text(body.category, 24).lower(),
                        lat=body.lat, lng=body.lng, is_pickup_point=body.is_pickup_point,
                        source=f"Added by administrator {admin.user.name or admin.user.phone[-4:]}")
    db.add(landmark)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ride_service.ServiceError(409, "A landmark with that name already exists.", code="duplicate")
    audit(db, admin.user, "landmark.create", "landmark", landmark.id, {"name": landmark.name}, client_ip(request))
    await db.commit()
    return {"id": landmark.id}


@router.patch("/landmarks/{landmark_id}")
async def patch_landmark(landmark_id: int, body: LandmarkPatch, request: Request,
                         admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    landmark = await db.get(Landmark, landmark_id)
    if landmark is None:
        raise ride_service.ServiceError(404, "Landmark not found", code="not_found")
    changes = body.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(landmark, key, ride_service.clean_text(value, 120) if key == "name" else value)
    audit(db, admin.user, "landmark.update", "landmark", landmark.id, changes, client_ip(request))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ride_service.ServiceError(409, "A landmark with that name already exists.", code="duplicate")
    return {"id": landmark.id, **changes}


@router.get("/zones")
async def zones(admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CampusZone))).scalars()
    return [{"id": z.id, "name": z.name, "kind": z.kind, "is_active": z.is_active, "source": z.source,
             "geometry": json.loads(z.geojson)} for z in rows]


@router.get("/reports")
async def reports(days: int = Query(14, ge=1, le=90), admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    rides = (await db.execute(select(Ride).where(Ride.created_at >= since))).scalars().all()
    daily, pickups, waits, trips = {}, {}, [], []
    for r in rides:
        day = r.created_at.astimezone(ride_service.IST).date().isoformat()
        bucket = daily.setdefault(day, {"requested": 0, "COMPLETED": 0, "CANCELLED": 0, "NO_DRIVER_AVAILABLE": 0})
        bucket["requested"] += 1
        if r.status in bucket:
            bucket[r.status] += 1
        pickups[r.pickup_name] = pickups.get(r.pickup_name, 0) + 1
        if r.accepted_at:
            waits.append((r.accepted_at - r.created_at).total_seconds())
        if r.started_at and r.completed_at:
            trips.append((r.completed_at - r.started_at).total_seconds())
    performance = (await db.execute(
        select(User.name, RideOffer.status, func.count(RideOffer.id))
        .join(User, User.id == RideOffer.driver_id).where(RideOffer.created_at >= since)
        .group_by(User.name, RideOffer.status)
    )).all()
    drivers_perf = {}
    for name, status, count in performance:
        drivers_perf.setdefault(name or "Unnamed", {})[status] = count
    cancellations = (await db.execute(
        select(Ride.cancelled_by, func.count(Ride.id)).where(Ride.created_at >= since, Ride.status == RideStatus.CANCELLED.value)
        .group_by(Ride.cancelled_by))).all()
    return {
        "days": days,
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "top_pickups": sorted(({"name": k, "rides": v} for k, v in pickups.items()), key=lambda x: -x["rides"])[:8],
        "avg_wait_to_accept_s": round(sum(waits) / len(waits)) if waits else None,
        "avg_trip_duration_s": round(sum(trips) / len(trips)) if trips else None,
        "cancellations_by": {k or "unknown": v for k, v in cancellations},
        "driver_offers": [{"driver": k, **v} for k, v in sorted(drivers_perf.items())],
    }


@router.get("/audit")
async def audit_log(limit: int = Query(100, ge=1, le=500), admin: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AuditLog, User.name).join(User, User.id == AuditLog.actor_user_id, isouter=True)
                             .order_by(AuditLog.id.desc()).limit(limit))).all()
    return [{"id": a.id, "actor": name or a.actor_role, "actor_role": a.actor_role, "action": a.action,
             "target_type": a.target_type, "target_id": a.target_id, "details": a.details,
             "at": ride_service.iso(a.created_at)} for a, name in rows]
