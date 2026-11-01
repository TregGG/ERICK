import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db, utcnow
from ..models import CampusZone, Landmark, Notification, Ride
from ..realtime import manager
from ..security import Principal, current_principal, rate_limiter
from ..services import rides as ride_service
from ..services import routing
from ..services.geo import CAMPUS_FIXTURE, get_campus
from ..services.ops_settings import get_ops

router = APIRouter(tags=["public"])
dispatch_state = {"running": False}


@router.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    database = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "runtime": "fastapi",
        "database": database,
        "database_engine": "sqlite" if settings.is_sqlite else "postgresql",
        "dispatch_loop": dispatch_state["running"],
        "websocket_connections": manager.connection_count(),
        "environment": settings.APP_ENV,
        "time": utcnow().isoformat(),
    }


@router.get("/api/config")
async def client_config(db: AsyncSession = Depends(get_db)):
    ops = await get_ops(db)
    campus = await get_campus(db)
    return {
        "environment": settings.APP_ENV,
        "otp_dev_mode": settings.OTP_DEV_MODE and not settings.is_production,
        "simulation_enabled": settings.SIMULATION_ENABLED,
        "fare": {"paise": ops.fare_paise, "display": ride_service.rupees(ops.fare_paise), "model": "per_passenger",
                 "max_passengers": ride_service.MAX_PASSENGERS, "max_boost": ride_service.money(ops.max_boost_paise),
                 "max_boosts_per_ride": ops.max_boosts_per_ride},
        "cancellation": {"fee_percent": ops.cancel_fee_percent, "min_fee": ride_service.money(ops.cancel_fee_min_paise),
                         "window_s": ops.cancel_window_s},
        "offer_timeout_s": ops.offer_timeout_s,
        "location_stale_s": ops.location_stale_s,
        "campus_center": {"lat": campus.origin_lat, "lng": campus.origin_lng},
        "map": {
            "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "&copy; OpenStreetMap contributors",
        },
    }


@router.get("/api/campus")
async def campus(db: AsyncSession = Depends(get_db)):
    zones = (await db.execute(select(CampusZone).where(CampusZone.is_active.is_(True)))).scalars().all()
    landmarks = (await db.execute(
        select(Landmark).where(Landmark.is_active.is_(True)).order_by(Landmark.category, Landmark.name)
    )).scalars().all()
    fixture = json.loads(CAMPUS_FIXTURE.read_text(encoding="utf-8"))
    return {
        "name": fixture["campus"],
        "data_source": fixture["source"],
        "verification": fixture["verification"],
        "zones": [{"name": z.name, "kind": z.kind, "geometry": json.loads(z.geojson)} for z in zones],
        "landmarks": [
            {"id": l.id, "name": l.name, "category": l.category, "lat": l.lat, "lng": l.lng,
             "is_pickup_point": l.is_pickup_point}
            for l in landmarks
        ],
    }


@router.get("/api/routes/preview")
async def route_preview(
    from_lat: float = Query(ge=-90, le=90), from_lng: float = Query(ge=-180, le=180),
    to_lat: float = Query(ge=-90, le=90), to_lng: float = Query(ge=-180, le=180),
    principal: Principal = Depends(current_principal),
):
    rate_limiter.check("route_preview", str(principal.id), limit=30, window_s=60)
    return await routing.get_route((from_lat, from_lng), (to_lat, to_lng))


class RatingBody(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=500)


@router.post("/api/rides/{ride_id}/rating", status_code=201)
async def rate(ride_id: str, body: RatingBody, principal: Principal = Depends(current_principal),
               db: AsyncSession = Depends(get_db)):
    ride = await ride_service.get_ride(db, ride_id)
    if principal.id not in (ride.passenger_id, ride.driver_id):
        raise ride_service.ServiceError(404, "Ride not found", code="not_found")
    rating = await ride_service.rate_ride(db, principal.user, ride, body.stars, body.comment)
    await db.commit()
    return {"stars": rating.stars}


@router.get("/api/notifications")
async def notifications(limit: int = Query(20, ge=1, le=100), principal: Principal = Depends(current_principal),
                        db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Notification).where(Notification.user_id == principal.id)
        .order_by(Notification.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body,
             "ride_id": str(n.ride_id) if n.ride_id else None, "read": n.read_at is not None,
             "created_at": ride_service.iso(n.created_at)} for n in rows]


@router.post("/api/notifications/read")
async def mark_read(principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    await db.execute(update(Notification).where(Notification.user_id == principal.id, Notification.read_at.is_(None))
                     .values(read_at=utcnow()))
    await db.commit()
    return {"ok": True}
