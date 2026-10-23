import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Enum, text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from ..database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=True)
    phone = Column(String(15), unique=True, nullable=False)
    role = Column(Enum('passenger', 'driver', name='user_roles'), nullable=False)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=text("now()"), default=datetime.utcnow)
