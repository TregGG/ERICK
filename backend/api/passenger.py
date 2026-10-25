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
from ..services.geo import get_campus
from ..services.ops_settings import get_ops

router = APIRouter(prefix="/api/passenger", tags=["passenger"])


class PlaceIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    name: Optional[str] = Field(default=None, max_length=120)


class RideIn(BaseModel):
    pickup: PlaceIn
    dropoff: PlaceIn


class CancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=200)


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
        idempotency_key, settings.GEOFENCE_BUFFER_METERS,
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
    await ride_service.cancel_ride(db, ride, principal.user, body.reason)
    await commit_and_publish(db)
    return await ride_service.ride_view(db, ride, "passenger", principal.id)


@router.get("/nearby-drivers")
async def nearby_drivers(lat: float = Query(ge=-90, le=90), lng: float = Query(ge=-180, le=180),
                         principal: Principal = Depends(require_passenger), db: AsyncSession = Depends(get_db)):
    ops = await get_ops(db)
    candidates = await dispatch.eligible_drivers(db, lat, lng, ops, limit=25)
    now = utcnow()
    return {
        "count": len(candidates),
        "radius_m": ops.dispatch_radius_m,
        # Positions are rounded to ~100 m so passengers see availability without tracking individual drivers.
        "approximate_positions": True,
        "drivers": [{"lat": round(c.lat, 3), "lng": round(c.lng, 3), "distance_m": int(c.distance_m),
                     "age_s": int((now - c.recorded_at).total_seconds())} for c in candidates],
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
