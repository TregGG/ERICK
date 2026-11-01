import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db, utcnow
from ..models import Notification, Ride, SavedPlace
from ..realtime import commit_and_publish
from ..security import Principal, rate_limiter, require_passenger
from ..services import dispatch
from ..services import rides as ride_service
from ..services.geo import get_campus, haversine_m
from ..services.ops_settings import get_ops

router = APIRouter(prefix="/api/passenger", tags=["passenger"])


class PlaceIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    name: Optional[str] = Field(default=None, max_length=120)


class RideIn(BaseModel):
    pickup: PlaceIn
    dropoff: PlaceIn
    passenger_count: int = Field(default=1, ge=1, le=ride_service.MAX_PASSENGERS)
    boost_paise: int = Field(default=0, ge=0)


class BoostIn(BaseModel):
    boost_paise: int = Field(gt=0)


class CancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=200)
    # The fee the passenger saw and agreed to; a higher fee at cancel time is refused, never charged silently.
    accepted_fee_paise: int = Field(default=0, ge=0)


class SavedPlaceIn(PlaceIn):
    label: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)


@router.get("/state")
async def state(principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    return await passenger_state(db, principal)


async def passenger_state(db: AsyncSession, principal: Principal) -> dict:
    ride = await ride_service.active_ride_for_passenger(db, principal.id)
    unread = (await db.execute(select(func.count(Notification.id)).where(
        Notification.user_id == principal.id, Notification.read_at.is_(None)))).scalar_one()
    return {
        "active_ride": await ride_service.ride_view(db, ride, "passenger", principal.id) if ride else None,
        "outstanding_fees": await ride_service.outstanding_fees(db, principal.id),
        "unread_notifications": unread,
        "server_time": utcnow().isoformat(),
    }


@router.post("/rides", status_code=201)
async def request_ride(body: RideIn, response: Response,
                       idempotency_key: Optional[str] = Header(default=None, max_length=64),
                       principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    rate_limiter.check("ride_request", str(principal.id), limit=10, window_s=300)
    ride, created = await ride_service.create_ride(
        db, principal.user, body.pickup.model_dump(), body.dropoff.model_dump(),
        idempotency_key, settings.GEOFENCE_BUFFER_METERS, body.passenger_count, body.boost_paise,
    )
    await commit_and_publish(db)
    if created:
        await dispatch.dispatch_ride(ride.id)
        await db.refresh(ride)
    else:
        response.status_code = 200
    return await ride_service.ride_view(db, ride, "passenger", principal.id)


@router.get("/rides")
async def history(limit: int = Query(20, ge=1, le=100), before: Optional[datetime] = None,
                  principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    query = select(Ride).where(Ride.passenger_id == principal.id)
    if before:
        query = query.where(Ride.created_at < before)
    rides = (await db.execute(query.order_by(Ride.created_at.desc()).limit(limit))).scalars().all()
    return [await ride_service.ride_view(db, r, "passenger", principal.id) for r in rides]


async def _own_ride(db: AsyncSession, ride_id: str, principal: Principal) -> Ride:
    ride = await ride_service.get_ride(db, ride_id)
    if ride.passenger_id != principal.id:  # do not reveal that another passenger's ride exists
        raise ride_service.ServiceError(404, "Ride not found", code="not_found")
    return ride


@router.get("/rides/{ride_id}")
async def ride_detail(ride_id: str, principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    ride = await _own_ride(db, ride_id, principal)
    view = await ride_service.ride_view(db, ride, "passenger", principal.id)
    view["timeline"] = await ride_service.ride_events(db, ride.id)
    return view


@router.post("/rides/{ride_id}/cancel")
async def cancel(ride_id: str, body: CancelIn, principal: Principal = Depends(require_passenger),
                 db: AsyncSession = Depends(get_db)):
    ride = await _own_ride(db, ride_id, principal)
    if ride.status == "CANCELLED":
        return await ride_service.ride_view(db, ride, "passenger", principal.id)  # idempotent repeat
    await ride_service.cancel_ride(db, ride, principal.user, body.reason, body.accepted_fee_paise)
    await commit_and_publish(db)
    return await ride_service.ride_view(db, ride, "passenger", principal.id)


@router.get("/rides/{ride_id}/cancel-quote")
async def cancel_quote(ride_id: str, principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    ride = await _own_ride(db, ride_id, principal)
    return await ride_service.cancellation_quote(db, ride)


@router.post("/rides/{ride_id}/boost")
async def boost(ride_id: str, body: BoostIn, principal: Principal = Depends(require_passenger),
                db: AsyncSession = Depends(get_db)):
    rate_limiter.check("ride_boost", str(principal.id), limit=10, window_s=300)
    ride = await _own_ride(db, ride_id, principal)
    await ride_service.boost_ride(db, ride, body.boost_paise)
    await commit_and_publish(db)
    if ride.status == "REQUESTED":
        await dispatch.dispatch_ride(ride.id)
        await db.refresh(ride)
    return await ride_service.ride_view(db, ride, "passenger", principal.id)


@router.get("/nearby-drivers")
async def nearby_drivers(lat: Optional[float] = Query(default=None, ge=-90, le=90),
                         lng: Optional[float] = Query(default=None, ge=-180, le=180),
                         principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    """Every online rickshaw on campus for the passenger map, free or busy.

    With a pickup (lat/lng), `count` is the number of free rickshaws that could be offered this ride and
    each driver gets a distance; without one, `count` is all free rickshaws on campus.
    """
    ops = await get_ops(db)
    has_pickup = lat is not None and lng is not None
    rows = await dispatch.online_drivers(db, ops)
    if has_pickup:
        count = len(await dispatch.eligible_drivers(db, lat, lng, ops, limit=25))
    else:
        count = sum(1 for r in rows if not r.busy)
    now = utcnow()
    drivers = []
    for r in rows:
        drivers.append({
            # Positions are rounded to ~100 m so passengers see availability without tracking individual drivers.
            "lat": round(r.lat, 3), "lng": round(r.lng, 3), "busy": bool(r.busy),
            "distance_m": int(haversine_m(lat, lng, r.lat, r.lng)) if has_pickup else None,
            "age_s": int((now - r.recorded_at).total_seconds()),
        })
    drivers.sort(key=lambda d: (d["busy"], d["distance_m"] or 0))
    return {
        "count": count,
        "online": len(drivers),
        "available": sum(1 for d in drivers if not d["busy"]),
        "radius_m": ops.dispatch_radius_m,
        "approximate_positions": True,
        "drivers": drivers,
    }


@router.get("/places")
async def list_places(principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SavedPlace).where(SavedPlace.user_id == principal.id).order_by(SavedPlace.label))).scalars()
    return [{"id": str(p.id), "label": p.label, "name": p.name, "lat": p.lat, "lng": p.lng} for p in rows]


@router.post("/places", status_code=201)
async def save_place(body: SavedPlaceIn, principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    campus = await get_campus(db)
    if not campus.contains(body.lat, body.lng, settings.GEOFENCE_BUFFER_METERS):
        raise ride_service.ServiceError(422, "Saved places must be on campus.", code="outside_campus")
    label = ride_service.clean_text(body.label, 40)
    existing = (await db.execute(select(SavedPlace).where(SavedPlace.user_id == principal.id, SavedPlace.label == label))).scalar_one_or_none()
    if existing is None:
        count = (await db.execute(select(func.count(SavedPlace.id)).where(SavedPlace.user_id == principal.id))).scalar_one()
        if count >= 10:
            raise ride_service.ServiceError(409, "You can save up to 10 places.", code="too_many_places")
        existing = SavedPlace(user_id=principal.id, label=label)
        db.add(existing)
    existing.name, existing.lat, existing.lng = ride_service.clean_text(body.name, 120), body.lat, body.lng
    await db.commit()
    return {"id": str(existing.id), "label": existing.label, "name": existing.name, "lat": existing.lat, "lng": existing.lng}


@router.delete("/places/{place_id}", status_code=204)
async def delete_place(place_id: uuid.UUID, principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    place = await db.get(SavedPlace, place_id)
    if place is None or place.user_id != principal.id:
        raise ride_service.ServiceError(404, "Place not found", code="not_found")
    await db.delete(place)
    await db.commit()
