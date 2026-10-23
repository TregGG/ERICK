import uuid
from sqlalchemy import Column, String, Boolean, Integer, Float, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from ..database import Base

class Rickshaw(Base):
    __tablename__ = "rickshaws"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    driver_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    vehicle_number = Column(String(20), unique=True, nullable=False)
    capacity = Column(Integer, default=4)
    is_active = Column(Boolean, default=False)
    current_location = Column(Geometry('POINT', srid=4326))
    heading = Column(Float)
    current_passenger_count = Column(Integer, default=0)
    max_detour_meters = Column(Integer, default=500)
