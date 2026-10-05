from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID

class RegisterRequest(BaseModel):
    username: str
    phone: str
    role: str = Field(..., pattern="^(passenger|driver)$")

class PhoneRequest(BaseModel):
    phone: str

class VerifyOTPRequest(BaseModel):
    phone: str
    otp: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class RideRequestCreate(BaseModel):
    pickup_lat: float
    pickup_lng: float
    pickup_name: Optional[str] = None
    dropoff_lat: float
    dropoff_lng: float
    dropoff_name: Optional[str] = None

class RickshawCreate(BaseModel):
    vehicle_number: str

class LocationUpdate(BaseModel):
    lat: float
    lng: float
    heading: Optional[float] = 0.0

class SettingsUpdate(BaseModel):
    max_detour_meters: int
