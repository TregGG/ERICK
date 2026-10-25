"""Relational schema. Migrations in backend/migrations are generated from and kept in sync with this module."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, SmallInteger,
    String, Text, UniqueConstraint, Uuid, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow
from .states import ACTIVE, DRIVER_ASSIGNED, OfferStatus, RideStatus

AutoId = BigInteger().with_variant(Integer, "sqlite")


def _in(column: str, statuses) -> str:
    order = list(type(next(iter(statuses))))  # enum definition order keeps generated SQL stable
    return f"{column} IN ({', '.join(repr(s.value) for s in sorted(statuses, key=order.index))})"


ACTIVE_RIDE_SQL = _in("status", ACTIVE)
ASSIGNED_RIDE_SQL = _in("status", DRIVER_ASSIGNED)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('passenger', 'driver', 'admin')", name="ck_users_role"),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_users_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class DriverProfile(Base):
    __tablename__ = "driver_profiles"
    __table_args__ = (
        CheckConstraint(
            "verification_status IN ('pending', 'approved', 'rejected', 'suspended')",
            name="ck_driver_profiles_verification",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    verification_note: Mapped[Optional[str]] = mapped_column(String(200))
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    online_since: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (CheckConstraint("capacity BETWEEN 1 AND 6", name="ck_vehicles_capacity"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    driver_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    registration_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    user_agent: Mapped[Optional[str]] = mapped_column(String(200))


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    __table_args__ = (Index("ix_otp_challenges_phone_created", "phone", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(16), nullable=False)
    app_role: Mapped[str] = mapped_column(String(16), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    request_ip: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Landmark(Base):
    __tablename__ = "landmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    is_pickup_point: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    osm_ref: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CampusZone(Base):
    __tablename__ = "campus_zones"
    __table_args__ = (CheckConstraint("kind IN ('boundary', 'operating')", name="ck_campus_zones_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    geojson: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class Ride(Base):
    __tablename__ = "rides"
    __table_args__ = (
        CheckConstraint(_in("status", RideStatus), name="ck_rides_status"),
        CheckConstraint("fare_paise >= 0", name="ck_rides_fare"),
        CheckConstraint("pickup_lat BETWEEN -90 AND 90 AND dropoff_lat BETWEEN -90 AND 90", name="ck_rides_lat"),
        CheckConstraint("pickup_lng BETWEEN -180 AND 180 AND dropoff_lng BETWEEN -180 AND 180", name="ck_rides_lng"),
        CheckConstraint(f"driver_id IS NOT NULL OR NOT ({ASSIGNED_RIDE_SQL})", name="ck_rides_assigned_has_driver"),
        UniqueConstraint("passenger_id", "idempotency_key", name="uq_rides_passenger_idempotency"),
        Index("ix_rides_status", "status"),
        Index("ix_rides_passenger_created", "passenger_id", "created_at"),
        Index("ix_rides_driver_created", "driver_id", "created_at"),
        Index("uq_rides_one_active_per_passenger", "passenger_id", unique=True,
              postgresql_where=text(ACTIVE_RIDE_SQL), sqlite_where=text(ACTIVE_RIDE_SQL)),
        Index("uq_rides_one_active_per_driver", "driver_id", unique=True,
              postgresql_where=text(ASSIGNED_RIDE_SQL), sqlite_where=text(ASSIGNED_RIDE_SQL)),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    passenger_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id"))
    vehicle_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("vehicles.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=RideStatus.REQUESTED.value)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    pickup_name: Mapped[str] = mapped_column(String(120), nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lng: Mapped[float] = mapped_column(Float, nullable=False)
    dropoff_name: Mapped[str] = mapped_column(String(120), nullable=False)
    dropoff_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dropoff_lng: Mapped[float] = mapped_column(Float, nullable=False)

    route_distance_m: Mapped[Optional[int]] = mapped_column(Integer)
    route_duration_s: Mapped[Optional[int]] = mapped_column(Integer)
    route_source: Mapped[Optional[str]] = mapped_column(String(24))
    fare_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    offer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(200))
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(16))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    arrived_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class RideOffer(Base):
    __tablename__ = "ride_offers"
    __table_args__ = (
        CheckConstraint(_in("status", OfferStatus), name="ck_ride_offers_status"),
        UniqueConstraint("ride_id", "driver_id", name="uq_ride_offers_ride_driver"),
        Index("uq_ride_offers_one_pending_per_ride", "ride_id", unique=True,
              postgresql_where=text("status = 'PENDING'"), sqlite_where=text("status = 'PENDING'")),
        Index("uq_ride_offers_one_pending_per_driver", "driver_id", unique=True,
              postgresql_where=text("status = 'PENDING'"), sqlite_where=text("status = 'PENDING'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ride_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rides.id", ondelete="CASCADE"), nullable=False)
    driver_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OfferStatus.PENDING.value)
    distance_to_pickup_m: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    responded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class RideEvent(Base):
    __tablename__ = "ride_events"
    __table_args__ = (Index("ix_ride_events_ride", "ride_id", "id"),)

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    ride_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rides.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class DriverLocation(Base):
    """Latest known position per driver. History is deliberately not retained (privacy)."""

    __tablename__ = "driver_locations"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180", name="ck_driver_locations_coords"),
        CheckConstraint("source IN ('gps', 'simulator')", name="ck_driver_locations_source"),
    )

    driver_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    heading: Mapped[Optional[float]] = mapped_column(Float)
    speed_mps: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="gps")
    inside_campus: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_paise >= 0", name="ck_payments_amount"),
        CheckConstraint("method IN ('cash')", name="ck_payments_method"),
        CheckConstraint("status IN ('PENDING', 'PAID', 'WAIVED')", name="ck_payments_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ride_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rides.id", ondelete="CASCADE"), unique=True, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="cash")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    collected_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("stars BETWEEN 1 AND 5", name="ck_ratings_stars"),
        UniqueConstraint("ride_id", "rater_id", name="uq_ratings_ride_rater"),
        Index("ix_ratings_ratee", "ratee_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ride_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rides.id", ondelete="CASCADE"), nullable=False)
    rater_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    ratee_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    stars: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(String(300))
    ride_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("rides.id", ondelete="SET NULL"))
    read_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class SavedPlace(Base):
    __tablename__ = "saved_places"
    __table_args__ = (UniqueConstraint("user_id", "label", name="uq_saved_places_user_label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(32))
    target_id: Mapped[Optional[str]] = mapped_column(String(64))
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
