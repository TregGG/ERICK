import uuid
from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from datetime import datetime
from ..database import Base

class Ride(Base):
    __tablename__ = "rides"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), default=uuid.uuid4)
    rickshaw_id = Column(UUID(as_uuid=True), ForeignKey("rickshaws.id"))
    status = Column(Enum('active', 'completed', name='ride_status'))
    route_polyline = Column(Text)
    started_at = Column(DateTime, server_default=text("now()"), default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class RideRequest(Base):
    __tablename__ = "ride_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), default=uuid.uuid4)
    passenger_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    pickup_location = Column(Geometry('POINT', srid=4326))
    pickup_name = Column(String(100))
    dropoff_location = Column(Geometry('POINT', srid=4326))
    dropoff_name = Column(String(100))
    status = Column(Enum('pending', 'queued', 'matched', 'pickup_enroute', 'picked_up', 'completed', 'cancelled', name='request_status'))
    matched_ride_id = Column(UUID(as_uuid=True), ForeignKey("rides.id"), nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), default=datetime.utcnow)

class RidePassenger(Base):
    __tablename__ = "ride_passengers"

    ride_id = Column(UUID(as_uuid=True), ForeignKey("rides.id"), primary_key=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("ride_requests.id"), primary_key=True)
    pickup_order = Column(Integer)
    dropoff_order = Column(Integer)
    picked_up_at = Column(DateTime, nullable=True)
    dropped_off_at = Column(DateTime, nullable=True)
    status = Column(Enum('waiting_pickup', 'in_vehicle', 'dropped_off', name='passenger_status'))
