"""Driver location ingestion and validation.

Only the latest position is stored. Positions are accepted only while the driver
is online or on a trip, and are deleted when the driver goes offline.
"""
import math
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import utcnow
from ..models import DriverLocation, DriverProfile, User
from ..realtime import queue_event
from . import rides as ride_service
from .geo import get_campus


class LocationReport(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(gt=0, le=100000)
    heading: Optional[float] = Field(default=None, ge=0, lt=360)
    speed_mps: Optional[float] = Field(default=None, ge=0, le=60)
    timestamp: datetime
    source: str = Field(default="gps", pattern="^(gps|simulator)$")

    @field_validator("lat", "lng", "accuracy_m")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value

    @field_validator("timestamp")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def record(db: AsyncSession, driver: User, report: LocationReport) -> dict:
    """Validate and store a location. Returns an acknowledgement; raises ServiceError when rejected."""
    if report.source == "simulator" and not settings.SIMULATION_ENABLED:
        raise ride_service.ServiceError(403, "The location simulator is disabled on this server.", code="simulation_disabled")

    now = utcnow()
    age = (now - report.timestamp).total_seconds()
    if age > settings.LOCATION_MAX_AGE_SECONDS:
        raise ride_service.ServiceError(422, f"Location reading is {int(age)} s old.", code="stale_location")
    if age < -settings.LOCATION_MAX_FUTURE_SKEW_SECONDS:
        raise ride_service.ServiceError(422, "Location timestamp is in the future. Check the device clock.", code="clock_skew")
    if report.accuracy_m > settings.LOCATION_MAX_ACCURACY_METERS:
        raise ride_service.ServiceError(422, f"GPS accuracy is too low (±{int(report.accuracy_m)} m). "
                                             "Move to open sky or enable precise location.", code="low_accuracy")

    profile = await db.get(DriverProfile, driver.id)
    active = await ride_service.active_ride_for_driver(db, driver.id)
    if not (profile and profile.is_online) and active is None:
        raise ride_service.ServiceError(409, "Go online to share your location.", code="offline")

    current = await db.get(DriverLocation, driver.id)
    if current is not None:
        since = (now - current.received_at).total_seconds()
        if since < settings.LOCATION_MIN_INTERVAL_SECONDS:
            return {"accepted": False, "reason": "throttled", "retry_after_s": round(settings.LOCATION_MIN_INTERVAL_SECONDS - since, 2)}
        if report.timestamp < current.recorded_at:
            return {"accepted": False, "reason": "out_of_order"}

    campus = await get_campus(db)
    outside_m = campus.distance_outside_m(report.lat, report.lng)
    inside = outside_m <= settings.GEOFENCE_BUFFER_METERS + min(report.accuracy_m, 50)

    if current is None:
        current = DriverLocation(driver_id=driver.id)
        db.add(current)
    current.lat, current.lng = report.lat, report.lng
    current.accuracy_m, current.heading, current.speed_mps = report.accuracy_m, report.heading, report.speed_mps
    current.source, current.inside_campus = report.source, inside
    current.recorded_at, current.received_at = report.timestamp, now

    if active is not None:
        queue_event(db, active.passenger_id, "driver.location", {"ride_id": str(active.id), **ride_service.location_view(current)})

    ack = {"accepted": True, "inside_campus": inside, "received_at": now.isoformat()}
    if not inside:
        ack["warning"] = (f"You are about {int(outside_m)} m outside the campus operating area. "
                          "You will not receive new ride offers until you return.")
    return ack


async def forget(db: AsyncSession, driver_id) -> None:
    location = await db.get(DriverLocation, driver_id)
    if location is not None:
        await db.delete(location)
