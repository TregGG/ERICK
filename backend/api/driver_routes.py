from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models.rickshaw import Rickshaw
from ..models.ride import RideRequest, RidePassenger
from ..schemas import RickshawCreate, SettingsUpdate
from ..auth.jwt import get_current_user
from ..services.matching import recheck_queue
from typing import Dict, Any
import uuid

router = APIRouter(prefix="/api/driver", tags=["driver"])

async def get_driver_rickshaw(user_id: str, db: AsyncSession) -> Rickshaw:
    result = await db.execute(select(Rickshaw).filter(Rickshaw.driver_id == user_id))
    rick = result.scalars().first()
    if not rick:
        raise HTTPException(status_code=404, detail="Rickshaw profile not found")
    return rick

@router.post("/register-rickshaw")
async def register_rickshaw(request: RickshawCreate, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user["role"] != "driver":
        raise HTTPException(status_code=403, detail="Not authorized")
    
    existing = await db.execute(select(Rickshaw).filter(Rickshaw.driver_id == user["user_id"]))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Rickshaw already registered for this driver")
        
    rick = Rickshaw(driver_id=user["user_id"], vehicle_number=request.vehicle_number)
    db.add(rick)
    await db.commit()
    return {"message": "Rickshaw registered successfully"}

@router.post("/go-online")
async def go_online(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rick = await get_driver_rickshaw(user["user_id"], db)
    rick.is_active = True
    await db.commit()
    await recheck_queue(db)
    return {"message": "Rickshaw is online"}

@router.post("/go-offline")
async def go_offline(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rick = await get_driver_rickshaw(user["user_id"], db)
    rick.is_active = False
    await db.commit()
    return {"message": "Rickshaw is offline"}

@router.post("/accept-passenger/{request_id}")
async def accept_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Stub: Normally would update request status and create Ride if needed
    return {"message": "Accepted passenger"}

@router.post("/reject-passenger/{request_id}")
async def reject_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Stub: Update status and throw back to queue
    return {"message": "Rejected passenger"}

@router.post("/pickup/{request_id}")
async def pickup_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Stub: Mark as picked up
    return {"message": "Passenger picked up"}

@router.post("/dropoff/{request_id}")
async def dropoff_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Stub: Mark as dropped off
    return {"message": "Passenger dropped off"}

@router.get("/active-ride")
async def get_active_ride(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Stub: Return active ride details
    return {"active_ride": None}

@router.put("/settings")
async def update_settings(settings: SettingsUpdate, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rick = await get_driver_rickshaw(user["user_id"], db)
    rick.max_detour_meters = settings.max_detour_meters
    await db.commit()
    return {"message": "Settings updated"}
