"""Ride lifecycle: creation, validated state transitions and role-specific views.

Every transition is a compare-and-set UPDATE on (status, version), so two
concurrent requests can never both succeed, and database partial unique indexes
guarantee at most one active ride per passenger and per driver.
"""
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import (
    DriverLocation, DriverProfile, Notification, Payment, Rating, Ride, RideApproachSample, RideEvent, RideOffer, User,
    Vehicle,
)
from ..realtime import queue_event
from ..states import (
    ACTIVE, DRIVER_ASSIGNED, PASSENGER_CANCELLABLE, SEARCHING, OfferStatus, RideStatus, can_transition, values,
)
from . import routing
from .geo import get_campus, haversine_m
from .ops_settings import get_ops

MIN_TRIP_METERS = 100
MAX_PASSENGERS = 6
# Approach samples are only needed for the cancellation window; older ones are deleted.
APPROACH_SAMPLE_RETENTION_S = 120
APPROACHING = frozenset({RideStatus.ACCEPTED, RideStatus.ARRIVED})
IST = timezone(timedelta(hours=5, minutes=30))  # India has no DST
_CONTROL = re.compile(r"[\x00-\x1f\x7f<>]")


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str, code: str = "error", extra: Optional[dict] = None):
        super().__init__(detail)
        self.status_code, self.detail, self.code, self.extra = status_code, detail, code, extra or {}


def clean_text(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    value = _CONTROL.sub("", value).strip()
    return value[:limit] or None


def rupees(paise: int) -> str:
    return f"₹{paise // 100}" if paise % 100 == 0 else f"₹{paise // 100}.{paise % 100:02d}"


def iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def fare_total(base_paise: int, boost_paise: int, passenger_count: int) -> int:
    return (base_paise + boost_paise) * passenger_count


def money(paise: int) -> dict:
    return {"paise": paise, "display": rupees(paise)}


# --------------------------------------------------------------------------- views

async def driver_rating(db: AsyncSession, driver_id) -> dict:
    row = (await db.execute(
        select(func.avg(Rating.stars), func.count(Rating.id)).where(Rating.ratee_id == driver_id)
    )).one()
    return {"average": round(float(row[0]), 2) if row[0] is not None else None, "count": int(row[1])}


def location_view(loc: Optional[DriverLocation]) -> Optional[dict]:
    if loc is None:
        return None
    return {
        "lat": loc.lat, "lng": loc.lng, "accuracy_m": round(loc.accuracy_m, 1),
        "heading": loc.heading, "speed_mps": loc.speed_mps, "source": loc.source,
        "recorded_at": iso(loc.recorded_at), "received_at": iso(loc.received_at),
    }


def _base_view(ride: Ride) -> dict:
    return {
        "id": str(ride.id),
        "status": ride.status,
        "version": ride.version,
        "pickup": {"name": ride.pickup_name, "lat": ride.pickup_lat, "lng": ride.pickup_lng},
        "dropoff": {"name": ride.dropoff_name, "lat": ride.dropoff_lat, "lng": ride.dropoff_lng},
        "route": {
            "distance_m": ride.route_distance_m, "duration_s": ride.route_duration_s,
            "source": ride.route_source, "approximate": ride.route_source != "osrm",
        },
        "fare": {"paise": ride.fare_paise, "currency": ride.currency, "display": rupees(ride.fare_paise)},
        "passenger_count": ride.passenger_count,
        "fare_breakdown": {
            "base_per_passenger": money(ride.base_fare_paise), "boost_per_passenger": money(ride.boost_paise),
            "passengers": ride.passenger_count, "boost_count": ride.boost_count,
        },
        "cancellation_fee": money(ride.cancellation_fee_paise) if ride.cancellation_fee_paise else None,
        "requires_pin": ride.start_pin is not None,
        "created_at": iso(ride.created_at),
        "search_expires_at": iso(ride.search_expires_at),
        "accepted_at": iso(ride.accepted_at),
        "arrived_at": iso(ride.arrived_at),
        "started_at": iso(ride.started_at),
        "completed_at": iso(ride.completed_at),
        "cancelled_at": iso(ride.cancelled_at),
        "cancel_reason": ride.cancel_reason,
        "cancelled_by": ride.cancelled_by,
    }


async def ride_view(db: AsyncSession, ride: Ride, viewer: str, viewer_id=None) -> dict:
    view = _base_view(ride)
    assigned = ride.status in values(DRIVER_ASSIGNED)
    if viewer in ("passenger", "admin") and ride.driver_id:
        driver = await db.get(User, ride.driver_id)
        vehicle = await db.get(Vehicle, ride.vehicle_id) if ride.vehicle_id else None
        view["driver"] = {
            "id": str(driver.id) if viewer == "admin" else None,
            "name": driver.name or "Campus driver",
            # Contact details are shared only while the trip is active.
            "phone": driver.phone if (assigned or viewer == "admin") else None,
            "vehicle_number": vehicle.registration_number if vehicle else None,
            "rating": await driver_rating(db, driver.id),
        }
        if assigned:
            view["driver_location"] = location_view(await db.get(DriverLocation, ride.driver_id))
    if viewer == "passenger" and ride.status in (RideStatus.ACCEPTED.value, RideStatus.ARRIVED.value):
        view["start_pin"] = ride.start_pin  # only the passenger ever sees the PIN, and only until boarding
    if viewer in ("driver", "admin"):
        passenger = await db.get(User, ride.passenger_id)
        first_name = (passenger.name or "Passenger").split()[0]
        view["passenger"] = {
            "name": passenger.name if (assigned or viewer == "admin") else first_name,
            "phone": passenger.phone if (assigned or viewer == "admin") else None,
        }
    if ride.status == RideStatus.COMPLETED.value:
        payment = (await db.execute(select(Payment).where(Payment.ride_id == ride.id))).scalar_one_or_none()
        view["payment"] = {"status": payment.status, "method": payment.method} if payment else None
        if viewer_id is not None:
            mine = (await db.execute(select(Rating.stars).where(Rating.ride_id == ride.id, Rating.rater_id == viewer_id))).scalar_one_or_none()
            view["my_rating"] = mine
    return view


async def offer_view(db: AsyncSession, offer: RideOffer, ride: Ride) -> dict:
    passenger = await db.get(User, ride.passenger_id)
    view = _base_view(ride)
    return {
        "offer_id": str(offer.id),
        "ride_id": str(ride.id),
        "expires_at": iso(offer.expires_at),
        "distance_to_pickup_m": offer.distance_to_pickup_m,
        # Only what a driver needs to decide: no phone number or full name before acceptance.
        "passenger_first_name": (passenger.name or "Passenger").split()[0],
        "pickup": view["pickup"], "dropoff": view["dropoff"], "route": view["route"], "fare": view["fare"],
        "passenger_count": view["passenger_count"], "fare_breakdown": view["fare_breakdown"],
    }


def publish_ride(db: AsyncSession, ride: Ride, passenger_view: dict, driver_view: Optional[dict]) -> None:
    queue_event(db, ride.passenger_id, "ride.updated", passenger_view)
    if ride.driver_id and driver_view is not None:
        queue_event(db, ride.driver_id, "ride.updated", driver_view)


async def publish(db: AsyncSession, ride: Ride) -> None:
    passenger_view = await ride_view(db, ride, "passenger", ride.passenger_id)
    driver_view = await ride_view(db, ride, "driver", ride.driver_id) if ride.driver_id else None
    publish_ride(db, ride, passenger_view, driver_view)


def notify(db: AsyncSession, user_id, kind: str, title: str, body: Optional[str] = None, ride_id=None) -> None:
    note = Notification(id=uuid.uuid4(), user_id=user_id, kind=kind, title=title, body=body, ride_id=ride_id, created_at=utcnow())
    db.add(note)
    queue_event(db, user_id, "notification", {
        "id": str(note.id), "kind": kind, "title": title, "body": body,
        "ride_id": str(ride_id) if ride_id else None, "created_at": iso(note.created_at),
    })


# --------------------------------------------------------------------------- transitions

async def transition(db: AsyncSession, ride: Ride, target: RideStatus, actor_role: str,
                     actor_id=None, note: Optional[str] = None, **fields) -> Ride:
    current = RideStatus(ride.status)
    if not can_transition(current, target):
        raise ServiceError(409, f"This ride is {current.value.replace('_', ' ').lower()} and cannot change to "
                                f"{target.value.replace('_', ' ').lower()}.", code="invalid_transition")
    result = await db.execute(
        update(Ride)
        .where(Ride.id == ride.id, Ride.status == current.value, Ride.version == ride.version)
        .values(status=target.value, version=Ride.version + 1, updated_at=utcnow(), **fields)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ServiceError(409, "This ride was just updated. Refreshing to the latest state.", code="stale_ride")
    db.add(RideEvent(ride_id=ride.id, from_status=current.value, to_status=target.value,
                     actor_user_id=actor_id, actor_role=actor_role, note=clean_text(note, 200), created_at=utcnow()))
    if current in APPROACHING and target not in APPROACHING:
        await db.execute(delete(RideApproachSample).where(RideApproachSample.ride_id == ride.id))
    await db.flush()
    await db.refresh(ride)
    return ride


async def get_ride(db: AsyncSession, ride_id) -> Ride:
    try:
        rid = uuid.UUID(str(ride_id))
    except ValueError:
        raise ServiceError(404, "Ride not found", code="not_found")
    ride = await db.get(Ride, rid, populate_existing=True)
    if ride is None:
        raise ServiceError(404, "Ride not found", code="not_found")
    return ride


async def active_ride_for_passenger(db: AsyncSession, passenger_id) -> Optional[Ride]:
    return (await db.execute(
        select(Ride).where(Ride.passenger_id == passenger_id, Ride.status.in_(values(ACTIVE)))
    )).scalar_one_or_none()


async def active_ride_for_driver(db: AsyncSession, driver_id) -> Optional[Ride]:
    return (await db.execute(
        select(Ride).where(Ride.driver_id == driver_id, Ride.status.in_(values(DRIVER_ASSIGNED)))
    )).scalar_one_or_none()


async def pending_offer_for_driver(db: AsyncSession, driver_id) -> Optional[RideOffer]:
    return (await db.execute(
        select(RideOffer).where(RideOffer.driver_id == driver_id, RideOffer.status == OfferStatus.PENDING.value)
    )).scalar_one_or_none()


async def _validate_point(db: AsyncSession, label: str, lat: float, lng: float, buffer_m: float) -> None:
    campus = await get_campus(db)
    outside = campus.distance_outside_m(lat, lng)
    if outside > buffer_m:
        raise ServiceError(422, f"{label} is {int(outside)} m outside the IIT Roorkee operating area. "
                                f"Choose a point on campus.", code="outside_campus")


async def outstanding_fees(db: AsyncSession, passenger_id) -> list:
    rows = (await db.execute(
        select(Payment, Ride).join(Ride, Ride.id == Payment.ride_id)
        .where(Ride.passenger_id == passenger_id, Payment.kind == "cancellation_fee", Payment.status == "PENDING")
        .order_by(Payment.created_at)
    )).all()
    return [{"payment_id": str(pay.id), "ride_id": str(ride.id), "amount": money(pay.amount_paise),
             "cancelled_at": iso(ride.cancelled_at), "pickup": ride.pickup_name} for pay, ride in rows]


async def create_ride(db: AsyncSession, passenger: User, pickup: dict, dropoff: dict,
                      idempotency_key: Optional[str], geofence_buffer_m: float,
                      passenger_count: int = 1, boost_paise: int = 0) -> tuple[Ride, bool]:
    """Returns (ride, created). A repeated idempotency key returns the original ride."""
    if idempotency_key:
        existing = (await db.execute(
            select(Ride).where(Ride.passenger_id == passenger.id, Ride.idempotency_key == idempotency_key)
        )).scalar_one_or_none()
        if existing:
            return existing, False

    active = await active_ride_for_passenger(db, passenger.id)
    if active:
        raise ServiceError(409, "You already have an active ride.", code="active_ride_exists", extra={"ride_id": str(active.id)})

    owed = await outstanding_fees(db, passenger.id)
    if owed:
        total = sum(f["amount"]["paise"] for f in owed)
        raise ServiceError(402, f"You owe a {rupees(total)} cancellation fee. Settle it with the transport office "
                                "before booking again.", code="fee_outstanding", extra={"fees": owed})

    ops = await get_ops(db)
    if not 1 <= passenger_count <= MAX_PASSENGERS:
        raise ServiceError(422, f"A rickshaw takes 1 to {MAX_PASSENGERS} passengers.", code="invalid_passenger_count")
    if boost_paise < 0 or boost_paise > ops.max_boost_paise:
        raise ServiceError(422, f"Boost can be up to {rupees(ops.max_boost_paise)} per passenger.", code="invalid_boost")

    await _validate_point(db, "Pickup", pickup["lat"], pickup["lng"], geofence_buffer_m)
    await _validate_point(db, "Drop-off", dropoff["lat"], dropoff["lng"], geofence_buffer_m)
    if haversine_m(pickup["lat"], pickup["lng"], dropoff["lat"], dropoff["lng"]) < MIN_TRIP_METERS:
        raise ServiceError(422, f"Pickup and drop-off must be at least {MIN_TRIP_METERS} m apart.", code="trip_too_short")

    route = await routing.get_route((pickup["lat"], pickup["lng"]), (dropoff["lat"], dropoff["lng"]))
    now = utcnow()
    ride = Ride(
        id=uuid.uuid4(), passenger_id=passenger.id, status=RideStatus.REQUESTED.value, version=1,
        pickup_name=clean_text(pickup.get("name"), 120) or f"Pin {pickup['lat']:.5f}, {pickup['lng']:.5f}",
        pickup_lat=pickup["lat"], pickup_lng=pickup["lng"],
        dropoff_name=clean_text(dropoff.get("name"), 120) or f"Pin {dropoff['lat']:.5f}, {dropoff['lng']:.5f}",
        dropoff_lat=dropoff["lat"], dropoff_lng=dropoff["lng"],
        route_distance_m=route["distance_m"], route_duration_s=route["duration_s"], route_source=route["source"],
        fare_paise=fare_total(ops.fare_paise, boost_paise, passenger_count), base_fare_paise=ops.fare_paise,
        boost_paise=boost_paise, passenger_count=passenger_count, boost_count=1 if boost_paise else 0,
        idempotency_key=idempotency_key, offer_count=0, start_pin=f"{secrets.randbelow(10000):04d}",
        search_expires_at=now + timedelta(seconds=ops.search_timeout_s), created_at=now, updated_at=now,
    )
    db.add(ride)
    db.add(RideEvent(ride_id=ride.id, from_status=None, to_status=RideStatus.REQUESTED.value,
                     actor_user_id=passenger.id, actor_role="passenger", created_at=now))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing = (await db.execute(
                select(Ride).where(Ride.passenger_id == passenger.id, Ride.idempotency_key == idempotency_key)
            )).scalar_one_or_none()
            if existing:
                return existing, False
        raise ServiceError(409, "You already have an active ride.", code="active_ride_exists")
    await publish(db, ride)
    return ride, True


async def boost_ride(db: AsyncSession, ride: Ride, boost_paise: int) -> Ride:
    """Raise the per-passenger boost of a searching ride.

    Drivers who declined or let the offer expire at the lower price become eligible again,
    and the search window restarts.
    """
    ops = await get_ops(db)
    if ride.status not in values(SEARCHING):
        raise ServiceError(409, "You can boost a ride only while it is looking for a driver.", code="not_searching")
    if ride.boost_count >= ops.max_boosts_per_ride:
        raise ServiceError(409, f"You can boost a ride at most {ops.max_boosts_per_ride} times.", code="boost_limit")
    if boost_paise <= ride.boost_paise:
        raise ServiceError(422, "A boost can only increase the fare.", code="invalid_boost")
    if boost_paise > ops.max_boost_paise:
        raise ServiceError(422, f"Boost can be up to {rupees(ops.max_boost_paise)} per passenger.", code="invalid_boost")
    await db.execute(delete(RideOffer).where(
        RideOffer.ride_id == ride.id, RideOffer.status.in_([OfferStatus.REJECTED.value, OfferStatus.EXPIRED.value])))
    pending = (await db.execute(
        select(RideOffer).where(RideOffer.ride_id == ride.id, RideOffer.status == OfferStatus.PENDING.value)
    )).scalar_one_or_none()
    result = await db.execute(
        update(Ride)
        .where(Ride.id == ride.id, Ride.status == ride.status, Ride.version == ride.version)
        .values(boost_paise=boost_paise, boost_count=Ride.boost_count + 1,
                fare_paise=fare_total(ride.base_fare_paise, boost_paise, ride.passenger_count),
                offer_count=1 if pending else 0, search_expires_at=utcnow() + timedelta(seconds=ops.search_timeout_s),
                version=Ride.version + 1, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ServiceError(409, "This ride was just updated. Refreshing to the latest state.", code="stale_ride")
    db.add(RideEvent(ride_id=ride.id, from_status=ride.status, to_status=ride.status, actor_user_id=ride.passenger_id,
                     actor_role="passenger", note=f"Boosted to +{rupees(boost_paise)} per passenger", created_at=utcnow()))
    await db.flush()
    await db.refresh(ride)
    if pending is not None:
        queue_event(db, pending.driver_id, "offer.new", await offer_view(db, pending, ride))
    await publish(db, ride)
    return ride


async def record_approach(db: AsyncSession, ride: Ride, driver_lat: float, driver_lng: float, accuracy_m: float) -> None:
    now = utcnow()
    db.add(RideApproachSample(ride_id=ride.id, accuracy_m=accuracy_m, recorded_at=now,
                              distance_m=int(haversine_m(driver_lat, driver_lng, ride.pickup_lat, ride.pickup_lng))))
    await db.execute(delete(RideApproachSample).where(
        RideApproachSample.ride_id == ride.id,
        RideApproachSample.recorded_at < now - timedelta(seconds=APPROACH_SAMPLE_RETENTION_S)))


async def cancellation_quote(db: AsyncSession, ride: Ride) -> dict:
    """What the passenger pays to cancel now.

    Free unless the assigned driver got more than approach_min_m closer to the pickup over
    the last cancel_window_s. A driver who is stationary, moving away or has stopped reporting
    a location cannot be shown to be approaching, so cancelling is free.
    """
    ops = await get_ops(db)
    free = {"fee": None, "approaching": False}
    if RideStatus(ride.status) not in APPROACHING:
        return {**free, "reason": "No driver is on the way yet."}
    samples = (await db.execute(
        select(RideApproachSample).where(RideApproachSample.ride_id == ride.id).order_by(RideApproachSample.recorded_at)
    )).scalars().all()
    window = timedelta(seconds=ops.cancel_window_s)
    if not samples or utcnow() - samples[-1].recorded_at > window:
        return {**free, "reason": "Your driver has not moved recently."}
    latest = samples[-1]
    reference = next((s for s in reversed(samples) if s.recorded_at <= latest.recorded_at - window), samples[0])
    closer_m = reference.distance_m - latest.distance_m
    if closer_m <= ops.approach_min_m:
        return {**free, "reason": "Your driver is not moving towards the pickup."}
    fee = min(max(round(ride.fare_paise * ops.cancel_fee_percent / 100), ops.cancel_fee_min_paise), ride.fare_paise)
    if fee <= 0:
        return {**free, "reason": "Cancellation is free."}
    return {"fee": money(fee), "approaching": True, "distance_m": latest.distance_m,
            "reason": f"Your driver is on the way ({latest.distance_m} m away)."}


async def _close_pending_offer(db: AsyncSession, ride: Ride, status: OfferStatus) -> Optional[RideOffer]:
    offer = (await db.execute(
        select(RideOffer).where(RideOffer.ride_id == ride.id, RideOffer.status == OfferStatus.PENDING.value)
    )).scalar_one_or_none()
    if offer:
        offer.status = status.value
        offer.responded_at = utcnow()
        queue_event(db, offer.driver_id, "offer.closed", {"offer_id": str(offer.id), "ride_id": str(ride.id), "reason": status.value})
    return offer


async def cancel_ride(db: AsyncSession, ride: Ride, actor: User, reason: Optional[str],
                      accepted_fee_paise: int = 0) -> Ride:
    """Passenger or admin cancellation. Passengers pay a fee when the driver is approaching (never admins)."""
    role = actor.role
    if role == "passenger" and ride.status not in values(PASSENGER_CANCELLABLE):
        raise ServiceError(409, "This ride can no longer be cancelled.", code="not_cancellable")
    fee_paise = 0
    if role == "passenger":
        quote = await cancellation_quote(db, ride)
        fee_paise = quote["fee"]["paise"] if quote["fee"] else 0
        if fee_paise > accepted_fee_paise:
            raise ServiceError(409, f"Cancelling now costs {rupees(fee_paise)}. {quote['reason']}",
                               code="fee_changed", extra={"quote": quote})
    previous_driver = ride.driver_id
    await _close_pending_offer(db, ride, OfferStatus.CANCELLED)
    await transition(db, ride, RideStatus.CANCELLED, role, actor.id, reason,
                     cancelled_at=utcnow(), cancel_reason=clean_text(reason, 200) or "No reason given", cancelled_by=role,
                     cancellation_fee_paise=fee_paise or None)
    if fee_paise:
        db.add(Payment(ride_id=ride.id, amount_paise=fee_paise, kind="cancellation_fee", method="cash", status="PENDING"))
        await db.flush()
        notify(db, ride.passenger_id, "cancellation_fee", f"Cancellation fee {rupees(fee_paise)}",
               "Your driver was on the way. Settle the fee with the transport office before your next ride.", ride.id)
    await publish(db, ride)
    if previous_driver:
        notify(db, previous_driver, "ride_cancelled", "Trip cancelled",
               f"The {'passenger' if role == 'passenger' else 'transport office'} cancelled the trip."
               + (f" A {rupees(fee_paise)} cancellation fee was charged to the passenger." if fee_paise else ""), ride.id)
    if role == "admin":
        notify(db, ride.passenger_id, "ride_cancelled", "Ride cancelled by the transport office", ride.cancel_reason, ride.id)
    return ride


async def ensure_driver_can_work(db: AsyncSession, driver: User) -> tuple[DriverProfile, Vehicle]:
    profile = await db.get(DriverProfile, driver.id)
    vehicle = (await db.execute(select(Vehicle).where(Vehicle.driver_id == driver.id))).scalar_one_or_none()
    if profile is None or vehicle is None:
        raise ServiceError(409, "Add your name and vehicle number before going online.", code="profile_incomplete")
    ops = await get_ops(db)
    if ops.require_driver_verification and profile.verification_status != "approved":
        raise ServiceError(403, f"Your driver account is {profile.verification_status}. "
                                "The transport office must approve it before you can take rides.", code="not_verified")
    return profile, vehicle


async def driver_accept(db: AsyncSession, driver: User, ride: Ride) -> Ride:
    _, vehicle = await ensure_driver_can_work(db, driver)
    now = utcnow()
    claimed = await db.execute(
        update(RideOffer)
        .where(RideOffer.ride_id == ride.id, RideOffer.driver_id == driver.id,
               RideOffer.status == OfferStatus.PENDING.value, RideOffer.expires_at > now)
        .values(status=OfferStatus.ACCEPTED.value, responded_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise ServiceError(409, "This ride offer is no longer available.", code="offer_unavailable")
    await transition(db, ride, RideStatus.ACCEPTED, "driver", driver.id,
                     driver_id=driver.id, vehicle_id=vehicle.id, accepted_at=now)
    loc = await db.get(DriverLocation, driver.id)
    if loc is not None:
        await record_approach(db, ride, loc.lat, loc.lng, loc.accuracy_m)
    await publish(db, ride)
    notify(db, ride.passenger_id, "ride_accepted", "Driver assigned",
           f"{driver.name or 'Your driver'} ({vehicle.registration_number}) is on the way.", ride.id)
    return ride


async def driver_reject(db: AsyncSession, driver: User, ride: Ride) -> Ride:
    now = utcnow()
    result = await db.execute(
        update(RideOffer)
        .where(RideOffer.ride_id == ride.id, RideOffer.driver_id == driver.id, RideOffer.status == OfferStatus.PENDING.value)
        .values(status=OfferStatus.REJECTED.value, responded_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ServiceError(409, "This ride offer is no longer available.", code="offer_unavailable")
    await transition(db, ride, RideStatus.REQUESTED, "driver", driver.id, "Offer declined")
    await publish(db, ride)
    return ride


def _assigned_to(ride: Ride, driver: User) -> None:
    if ride.driver_id != driver.id:
        raise ServiceError(404, "Ride not found", code="not_found")


async def driver_arrived(db: AsyncSession, driver: User, ride: Ride) -> Ride:
    _assigned_to(ride, driver)
    ops = await get_ops(db)
    loc = await db.get(DriverLocation, driver.id)
    fresh = loc is not None and (utcnow() - loc.received_at).total_seconds() <= ops.location_stale_s
    if not fresh:
        raise ServiceError(409, "Your current location is needed to confirm arrival. Check that location sharing is on.",
                           code="location_required")
    distance = haversine_m(loc.lat, loc.lng, ride.pickup_lat, ride.pickup_lng)
    if distance > ops.arrival_radius_m + loc.accuracy_m:
        raise ServiceError(409, f"You are about {int(distance)} m from the pickup point. Mark arrival when you get there.",
                           code="not_at_pickup")
    await transition(db, ride, RideStatus.ARRIVED, "driver", driver.id, arrived_at=utcnow())
    await publish(db, ride)
    notify(db, ride.passenger_id, "driver_arrived", "Your rickshaw has arrived", ride.pickup_name, ride.id)
    return ride


async def driver_start(db: AsyncSession, driver: User, ride: Ride, pin: Optional[str] = None) -> Ride:
    _assigned_to(ride, driver)
    if ride.status == RideStatus.ARRIVED.value and ride.start_pin is not None \
            and not hmac.compare_digest((pin or "").strip(), ride.start_pin):
        raise ServiceError(422, "That PIN doesn't match. Ask the passenger for the 4-digit PIN shown in their app.",
                           code="wrong_pin")
    await transition(db, ride, RideStatus.IN_PROGRESS, "driver", driver.id, started_at=utcnow())
    await publish(db, ride)
    return ride


async def driver_complete(db: AsyncSession, driver: User, ride: Ride, cash_collected: bool) -> Ride:
    _assigned_to(ride, driver)
    await transition(db, ride, RideStatus.COMPLETED, "driver", driver.id, completed_at=utcnow())
    db.add(Payment(ride_id=ride.id, amount_paise=ride.fare_paise, kind="fare", method="cash",
                   status="PAID" if cash_collected else "PENDING",
                   collected_by=driver.id if cash_collected else None))
    await db.flush()
    await publish(db, ride)
    notify(db, ride.passenger_id, "ride_completed", "Trip completed",
           f"Fare {rupees(ride.fare_paise)} ({'paid in cash' if cash_collected else 'cash payment pending'}).", ride.id)
    return ride


async def mark_cash_collected(db: AsyncSession, driver: User, ride: Ride) -> Payment:
    _assigned_to(ride, driver)
    payment = (await db.execute(select(Payment).where(Payment.ride_id == ride.id))).scalar_one_or_none()
    if payment is None:
        raise ServiceError(409, "This trip has no payment to collect yet.", code="no_payment")
    if payment.status == "PENDING":
        payment.status, payment.collected_by = "PAID", driver.id
    return payment


async def driver_cancel(db: AsyncSession, driver: User, ride: Ride, reason: Optional[str]) -> Ride:
    """Before arrival the ride is released for reassignment; after arrival only a no-show cancels it."""
    _assigned_to(ride, driver)
    ops = await get_ops(db)
    if ride.status == RideStatus.ACCEPTED.value:
        await transition(db, ride, RideStatus.REQUESTED, "driver", driver.id, clean_text(reason, 200) or "Driver released ride",
                         driver_id=None, vehicle_id=None, accepted_at=None)
        queue_event(db, driver.id, "ride.released", {"ride_id": str(ride.id)})
        await publish(db, ride)
        notify(db, ride.passenger_id, "driver_reassigning", "Finding you another driver",
               "Your driver could not continue. We are offering your ride to another driver.", ride.id)
        return ride
    if ride.status == RideStatus.ARRIVED.value:
        waited = (utcnow() - ride.arrived_at).total_seconds() if ride.arrived_at else 0
        if waited < ops.no_show_wait_s:
            raise ServiceError(409, f"Wait at least {int((ops.no_show_wait_s - waited) // 60) + 1} more minute(s) before "
                                    "reporting a no-show.", code="no_show_too_early")
        await transition(db, ride, RideStatus.CANCELLED, "driver", driver.id, "Passenger no-show",
                         cancelled_at=utcnow(), cancel_reason="Passenger did not arrive at the pickup point", cancelled_by="driver")
        await publish(db, ride)
        notify(db, ride.passenger_id, "ride_cancelled", "Ride cancelled",
               "Your driver waited at the pickup point but could not find you.", ride.id)
        return ride
    raise ServiceError(409, "This trip can no longer be cancelled by the driver.", code="not_cancellable")


async def rate_ride(db: AsyncSession, rater: User, ride: Ride, stars: int, comment: Optional[str]) -> Rating:
    if ride.status != RideStatus.COMPLETED.value:
        raise ServiceError(409, "You can rate a ride after it is completed.", code="not_completed")
    if rater.id == ride.passenger_id:
        ratee = ride.driver_id
    elif rater.id == ride.driver_id:
        ratee = ride.passenger_id
    else:
        raise ServiceError(404, "Ride not found", code="not_found")
    rating = Rating(ride_id=ride.id, rater_id=rater.id, ratee_id=ratee, stars=stars, comment=clean_text(comment, 500))
    db.add(rating)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ServiceError(409, "You have already rated this ride.", code="already_rated")
    return rating


async def ride_events(db: AsyncSession, ride_id) -> list:
    rows = (await db.execute(select(RideEvent).where(RideEvent.ride_id == ride_id).order_by(RideEvent.id))).scalars()
    return [{"from": e.from_status, "to": e.to_status, "by": e.actor_role, "note": e.note, "at": iso(e.created_at)} for e in rows]
