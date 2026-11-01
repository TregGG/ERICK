import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db, utcnow
from ..models import DriverLocation, DriverProfile, Payment, Ride, RideOffer, Vehicle
from ..realtime import commit_and_publish, queue_event
from ..security import Principal, rate_limiter, require_driver
from ..services import demand, dispatch, locations
from ..services import rides as ride_service
from ..services.ops_settings import get_ops
from ..states import OfferStatus, RideStatus

router = APIRouter(prefix="/api/driver", tags=["driver"])
IST = ride_service.IST
VEHICLE_RE = re.compile(r"^[A-Z]{2}[ -]?\d{1,2}[ -]?[A-Z]{0,3}[ -]?\d{1,4}$")


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    vehicle_number: str = Field(min_length=4, max_length=20)
    # Passenger seats; only rides with a party that fits are offered to this driver.
    capacity: Optional[int] = Field(default=None, ge=1, le=ride_service.MAX_PASSENGERS)


class OnlineIn(BaseModel):
    online: bool


class CompleteIn(BaseModel):
    cash_collected: bool = True


class CancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=200)


class StartIn(BaseModel):
    pin: Optional[str] = Field(default=None, max_length=8)


async def driver_state(db: AsyncSession, principal: Principal) -> dict:
    profile = await db.get(DriverProfile, principal.id)
    vehicle = (await db.execute(select(Vehicle).where(Vehicle.driver_id == principal.id))).scalar_one_or_none()
    location = await db.get(DriverLocation, principal.id)
    ops = await get_ops(db)
    offer = await ride_service.pending_offer_for_driver(db, principal.id)
    offer_data = None
    if offer is not None and offer.expires_at > utcnow():
        offer_data = await ride_service.offer_view(db, offer, await db.get(Ride, offer.ride_id))
    ride = await ride_service.active_ride_for_driver(db, principal.id)
    return {
        "profile": {
            "name": principal.user.name,
            "phone": principal.user.phone,
            "verification_status": profile.verification_status if profile else "pending",
            "verification_note": profile.verification_note if profile else None,
            "is_online": bool(profile and profile.is_online),
        },
        "vehicle": {"number": vehicle.registration_number, "capacity": vehicle.capacity} if vehicle else None,
        "location": ride_service.location_view(location),
        "location_eligible": bool(location and location.inside_campus
                                  and (utcnow() - location.received_at).total_seconds() <= ops.location_stale_s),
        "offer": offer_data,
        "active_ride": await ride_service.ride_view(db, ride, "driver", principal.id) if ride else None,
        "policy": {"offer_timeout_s": ops.offer_timeout_s, "location_stale_s": ops.location_stale_s,
                   "require_verification": ops.require_driver_verification,
                   "simulation_enabled": settings.SIMULATION_ENABLED},
        "server_time": utcnow().isoformat(),
    }


@router.get("/state")
async def state(principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    return await driver_state(db, principal)


@router.put("/profile")
async def update_profile(body: ProfileIn, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    number = re.sub(r"\s+", " ", body.vehicle_number.strip().upper())
    if not VEHICLE_RE.fullmatch(number):
        raise ride_service.ServiceError(422, "Enter the registration number as printed, e.g. UK 08 ER 1234.", code="bad_vehicle_number")
    if await ride_service.active_ride_for_driver(db, principal.id):
        raise ride_service.ServiceError(409, "Finish your current trip before changing your profile.", code="on_trip")
    principal.user.name = ride_service.clean_text(body.name, 80)
    vehicle = (await db.execute(select(Vehicle).where(Vehicle.driver_id == principal.id))).scalar_one_or_none()
    if vehicle is None:
        vehicle = Vehicle(driver_id=principal.id, registration_number=number)
        db.add(vehicle)
    else:
        vehicle.registration_number = number
    if body.capacity is not None:
        vehicle.capacity = body.capacity
    if await db.get(DriverProfile, principal.id) is None:
        db.add(DriverProfile(user_id=principal.id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ride_service.ServiceError(409, "That vehicle number is registered to another driver.", code="vehicle_taken")
    return await driver_state(db, principal)


@router.post("/online")
async def set_online(body: OnlineIn, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    offer = None
    if body.online:
        profile, _ = await ride_service.ensure_driver_can_work(db, principal.user)
    else:
        profile = await db.get(DriverProfile, principal.id)
    if profile is None:
        raise ride_service.ServiceError(409, "Complete your driver profile first.", code="profile_incomplete")
    if body.online:
        if not profile.is_online:
            profile.is_online, profile.online_since = True, utcnow()
    else:
        if await ride_service.active_ride_for_driver(db, principal.id):
            raise ride_service.ServiceError(409, "Finish or release your current trip before going offline.", code="on_trip")
        offer = await ride_service.pending_offer_for_driver(db, principal.id)
        if offer is not None:
            await ride_service.driver_reject(db, principal.user, await db.get(Ride, offer.ride_id))
            queue_event(db, principal.id, "offer.closed", {"offer_id": str(offer.id), "ride_id": str(offer.ride_id), "reason": "OFFLINE"})
        profile.is_online, profile.online_since = False, None
        await locations.forget(db, principal.id)  # stop retaining a position once off duty
    await commit_and_publish(db)
    if offer is not None:
        await dispatch.dispatch_ride(offer.ride_id)
    return await driver_state(db, principal)


@router.post("/location")
async def report_location(body: locations.LocationReport, principal: Principal = Depends(require_driver),
                          db: AsyncSession = Depends(get_db)):
    """REST fallback for when the WebSocket is unavailable."""
    ack = await locations.record(db, principal.user, body)
    await commit_and_publish(db)
    return ack


async def _ride(db: AsyncSession, ride_id: str) -> Ride:
    return await ride_service.get_ride(db, ride_id)


@router.post("/rides/{ride_id}/accept")
async def accept(ride_id: str, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    ride = await _ride(db, ride_id)
    if ride.driver_id == principal.id and ride.status == RideStatus.ACCEPTED.value:
        return await ride_service.ride_view(db, ride, "driver", principal.id)  # repeated tap: already ours
    try:
        await ride_service.driver_accept(db, principal.user, ride)
        await commit_and_publish(db)
    except IntegrityError:
        await db.rollback()
        raise ride_service.ServiceError(409, "You already have an active trip.", code="driver_busy")
    return await ride_service.ride_view(db, ride, "driver", principal.id)


@router.post("/rides/{ride_id}/reject")
async def reject(ride_id: str, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    ride = await _ride(db, ride_id)
    await ride_service.driver_reject(db, principal.user, ride)
    await commit_and_publish(db)
    await dispatch.dispatch_ride(ride.id)
    return {"ok": True}


@router.post("/rides/{ride_id}/arrived")
async def arrived(ride_id: str, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    ride = await _ride(db, ride_id)
    await ride_service.driver_arrived(db, principal.user, ride)
    await commit_and_publish(db)
    return await ride_service.ride_view(db, ride, "driver", principal.id)


@router.post("/rides/{ride_id}/start")
async def start(ride_id: str, body: StartIn = StartIn(), principal: Principal = Depends(require_driver),
                db: AsyncSession = Depends(get_db)):
    rate_limiter.check("start_pin", ride_id, limit=5, window_s=300)  # 10,000 PINs: no guessing
    ride = await _ride(db, ride_id)
    await ride_service.driver_start(db, principal.user, ride, body.pin)
    await commit_and_publish(db)
    return await ride_service.ride_view(db, ride, "driver", principal.id)


@router.post("/rides/{ride_id}/complete")
async def complete(ride_id: str, body: CompleteIn, principal: Principal = Depends(require_driver),
                   db: AsyncSession = Depends(get_db)):
    ride = await _ride(db, ride_id)
    await ride_service.driver_complete(db, principal.user, ride, body.cash_collected)
    await commit_and_publish(db)
    return await ride_service.ride_view(db, ride, "driver", principal.id)


@router.post("/rides/{ride_id}/collect-cash")
async def collect_cash(ride_id: str, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    ride = await _ride(db, ride_id)
    payment = await ride_service.mark_cash_collected(db, principal.user, ride)
    await db.commit()
    return {"status": payment.status}


@router.post("/rides/{ride_id}/cancel")
async def cancel(ride_id: str, body: CancelIn, principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    ride = await _ride(db, ride_id)
    await ride_service.driver_cancel(db, principal.user, ride, body.reason)
    await commit_and_publish(db)
    if ride.status == RideStatus.REQUESTED.value:
        await dispatch.dispatch_ride(ride.id)
    return {"ok": True, "status": ride.status}


@router.get("/demand")
async def demand_map(principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    return await demand.demand_cells(db, await get_ops(db))


@router.get("/rides")
async def history(limit: int = Query(20, ge=1, le=100), before: Optional[datetime] = None,
                  principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    query = select(Ride).where(Ride.driver_id == principal.id)
    if before:
        query = query.where(Ride.created_at < before)
    rides = (await db.execute(query.order_by(Ride.created_at.desc()).limit(limit))).scalars().all()
    return [await ride_service.ride_view(db, r, "driver", principal.id) for r in rides]


@router.get("/earnings")
async def earnings(principal: Principal = Depends(require_driver), db: AsyncSession = Depends(get_db)):
    """Derived only from completed rides and their recorded payments."""
    ops = await get_ops(db)
    now = utcnow()
    start_of_day = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)

    async def window(since):
        conditions = [Ride.driver_id == principal.id, Ride.status == RideStatus.COMPLETED.value]
        if since is not None:
            conditions.append(Ride.completed_at >= since)
        row = (await db.execute(
            select(func.count(Ride.id), func.coalesce(func.sum(Ride.fare_paise), 0),
                   func.coalesce(func.sum(Payment.amount_paise).filter(Payment.status == "PAID"), 0))
            .select_from(Ride).outerjoin(Payment, Payment.ride_id == Ride.id).where(and_(*conditions))
        )).one()
        trips, fares, collected = int(row[0]), int(row[1]), int(row[2])
        fee_conditions = [Ride.driver_id == principal.id, Payment.kind == "cancellation_fee", Payment.status == "PAID"]
        if since is not None:
            fee_conditions.append(Ride.cancelled_at >= since)
        fees = int((await db.execute(
            select(func.coalesce(func.sum(Payment.amount_paise), 0)).select_from(Payment)
            .join(Ride, Ride.id == Payment.ride_id).where(and_(*fee_conditions))
        )).scalar_one())
        return {"trips": trips, "fares_paise": fares, "cash_collected_paise": collected,
                "cancellation_fees_paise": fees,
                "driver_share_paise": fares * ops.driver_share_percent // 100,
                "display": ride_service.rupees(fares * ops.driver_share_percent // 100)}

    offers = (await db.execute(
        select(RideOffer.status, func.count(RideOffer.id)).where(RideOffer.driver_id == principal.id).group_by(RideOffer.status)
    )).all()
    offer_counts = {status: count for status, count in offers}
    responded = sum(offer_counts.get(s.value, 0) for s in (OfferStatus.ACCEPTED, OfferStatus.REJECTED, OfferStatus.EXPIRED))
    return {
        "today": await window(start_of_day),
        "last_7_days": await window(now - timedelta(days=7)),
        "all_time": await window(None),
        "driver_share_percent": ops.driver_share_percent,
        "rating": await ride_service.driver_rating(db, principal.id),
        "acceptance_rate": round(offer_counts.get(OfferStatus.ACCEPTED.value, 0) / responded, 2) if responded else None,
        "timezone_note": "'Today' starts at midnight IST.",
    }
