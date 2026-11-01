"""Dispatch: offer each searching ride to one eligible driver at a time.

Eligible driver = online, approved (when verification is required), account
active, vehicle registered with enough seats for the party, a fresh in-campus
location, no assigned ride, no other pending offer, and never offered this ride
before (so a rejection or
timeout moves on to the next candidate). Candidates are ranked by distance to
the pickup; PostGIS performs the radius search on PostgreSQL.

Safety does not depend on the in-process lock: the partial unique indexes on
ride_offers and rides reject any duplicate pending offer or double assignment.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import and_, exists, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import DriverLocation, DriverProfile, Ride, RideOffer, User, Vehicle
from ..realtime import commit_and_publish, discard_events, queue_event
from ..states import DRIVER_ASSIGNED, OfferStatus, RideStatus, values
from . import rides as ride_service
from .geo import haversine_m
from .ops_settings import OpsSettings, get_ops

log = logging.getLogger("erick.dispatch")
_lock = asyncio.Lock()


@dataclass
class Candidate:
    driver_id: uuid.UUID
    distance_m: float
    lat: float
    lng: float
    recorded_at: object


async def eligible_drivers(db: AsyncSession, lat: float, lng: float, ops: OpsSettings,
                           exclude_ride_id: Optional[uuid.UUID] = None, limit: int = 20,
                           min_capacity: int = 1) -> List[Candidate]:
    now = utcnow()
    fresh_after = now - timedelta(seconds=ops.location_stale_s)
    conditions = [
        DriverProfile.is_online.is_(True),
        User.status == "active",
        DriverLocation.inside_campus.is_(True),
        DriverLocation.received_at >= fresh_after,
        ~exists().where(and_(Ride.driver_id == DriverProfile.user_id, Ride.status.in_(values(DRIVER_ASSIGNED)))),
        ~exists().where(and_(RideOffer.driver_id == DriverProfile.user_id, RideOffer.status == OfferStatus.PENDING.value)),
    ]
    if ops.require_driver_verification:
        conditions.append(DriverProfile.verification_status == "approved")
    if min_capacity > 1:
        conditions.append(Vehicle.capacity >= min_capacity)
    if exclude_ride_id is not None:
        conditions.append(~exists().where(and_(RideOffer.driver_id == DriverProfile.user_id, RideOffer.ride_id == exclude_ride_id)))

    query = (
        select(DriverLocation.driver_id, DriverLocation.lat, DriverLocation.lng, DriverLocation.recorded_at)
        .join(DriverProfile, DriverProfile.user_id == DriverLocation.driver_id)
        .join(User, User.id == DriverLocation.driver_id)
        .join(Vehicle, Vehicle.driver_id == DriverLocation.driver_id)
        .where(*conditions)
    )
    params = {"lat": lat, "lng": lng, "radius": ops.dispatch_radius_m}
    if not settings.is_sqlite:
        point = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography"
        query = (query.where(text(f"ST_DWithin(driver_locations.geog, {point}, :radius)"))
                 .order_by(text(f"ST_Distance(driver_locations.geog, {point})")).limit(limit))
    rows = (await db.execute(query, params)).all()
    candidates = []
    for row in rows:
        distance = haversine_m(lat, lng, row.lat, row.lng)
        if distance <= ops.dispatch_radius_m:
            candidates.append(Candidate(row.driver_id, distance, row.lat, row.lng, row.recorded_at))
    candidates.sort(key=lambda c: c.distance_m)
    return candidates[:limit]


async def _offer_to_next(db: AsyncSession, ride: Ride, ops: OpsSettings) -> str:
    now = utcnow()
    if now >= ride.search_expires_at or ride.offer_count >= ops.max_offers_per_ride:
        await ride_service.transition(db, ride, RideStatus.NO_DRIVER_AVAILABLE, "system",
                                      note="Search window closed" if now >= ride.search_expires_at else "Offer limit reached")
        await ride_service.publish(db, ride)
        ride_service.notify(db, ride.passenger_id, "no_driver", "No rickshaw available",
                            "No driver accepted your request. Please try again in a few minutes.", ride.id)
        await commit_and_publish(db)
        return "no_driver"

    candidates = await eligible_drivers(db, ride.pickup_lat, ride.pickup_lng, ops, exclude_ride_id=ride.id, limit=5,
                                        min_capacity=ride.passenger_count)
    for candidate in candidates:
        offer = RideOffer(id=uuid.uuid4(), ride_id=ride.id, driver_id=candidate.driver_id,
                          status=OfferStatus.PENDING.value, distance_to_pickup_m=int(candidate.distance_m),
                          created_at=now, expires_at=now + timedelta(seconds=ops.offer_timeout_s))
        try:
            async with db.begin_nested():
                db.add(offer)
                await db.flush()
        except IntegrityError:
            continue  # the driver received another offer a moment ago; try the next one
        await ride_service.transition(db, ride, RideStatus.OFFERED, "system",
                                      note=f"Offered to driver {str(candidate.driver_id)[:8]}",
                                      offer_count=ride.offer_count + 1)
        queue_event(db, candidate.driver_id, "offer.new", await ride_service.offer_view(db, offer, ride))
        await ride_service.publish(db, ride)
        await commit_and_publish(db)
        return "offered"
    await db.rollback()
    return "waiting"


async def dispatch_ride(ride_id) -> str:
    """Try to offer a REQUESTED ride. Safe to call repeatedly and concurrently."""
    async with _lock:
        async with SessionLocal() as db:
            try:
                ride = await db.get(Ride, ride_id, populate_existing=True)
                if ride is None or ride.status != RideStatus.REQUESTED.value:
                    return "skipped"
                return await _offer_to_next(db, ride, await get_ops(db))
            except (ride_service.ServiceError, IntegrityError) as exc:
                await db.rollback()
                discard_events(db)
                log.info("dispatch of %s skipped: %s", ride_id, exc)
                return "conflict"


async def expire_offers() -> int:
    """Expire overdue offers and return their rides to the search pool."""
    expired = 0
    async with _lock:
        async with SessionLocal() as db:
            overdue = (await db.execute(
                select(RideOffer).where(RideOffer.status == OfferStatus.PENDING.value, RideOffer.expires_at <= utcnow())
            )).scalars().all()
            for offer in overdue:
                try:
                    offer.status, offer.responded_at = OfferStatus.EXPIRED.value, utcnow()
                    ride = await db.get(Ride, offer.ride_id, populate_existing=True)
                    if ride is not None and ride.status == RideStatus.OFFERED.value:
                        await ride_service.transition(db, ride, RideStatus.REQUESTED, "system", note="Offer timed out")
                        await ride_service.publish(db, ride)
                    queue_event(db, offer.driver_id, "offer.closed",
                                {"offer_id": str(offer.id), "ride_id": str(offer.ride_id), "reason": "EXPIRED"})
                    await commit_and_publish(db)
                    expired += 1
                except (ride_service.ServiceError, IntegrityError) as exc:
                    await db.rollback()
                    discard_events(db)
                    log.info("offer %s expiry skipped: %s", offer.id, exc)
    return expired


async def tick() -> None:
    await expire_offers()
    async with SessionLocal() as db:
        searching = (await db.execute(
            select(Ride.id).where(Ride.status == RideStatus.REQUESTED.value).order_by(Ride.created_at)
        )).scalars().all()
    for ride_id in searching:
        await dispatch_ride(ride_id)


async def run_forever(stop: asyncio.Event) -> None:
    log.info("dispatch loop started (tick %.1fs)", settings.DISPATCH_TICK_SECONDS)
    while not stop.is_set():
        try:
            await tick()
        except Exception:
            log.exception("dispatch tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.DISPATCH_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
