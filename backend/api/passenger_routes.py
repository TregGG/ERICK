from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from ..database import get_db
from ..models.ride import RideRequest
from ..schemas import RideRequestCreate
from ..auth.jwt import get_current_user
from ..services.matching import find_matching_rick
from typing import Dict, Any

router = APIRouter(prefix="/api/passenger", tags=["passenger"])

@router.post("/ride-request", status_code=status.HTTP_201_CREATED)
async def create_ride_request(request: RideRequestCreate, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user["role"] != "passenger":
        raise HTTPException(status_code=403, detail="Not authorized")
    
    pickup_point = from_shape(Point(request.pickup_lng, request.pickup_lat), srid=4326)
    dropoff_point = from_shape(Point(request.dropoff_lng, request.dropoff_lat), srid=4326)
    
    new_request = RideRequest(
        passenger_id=user["user_id"],
        pickup_location=pickup_point,
        pickup_name=request.pickup_name,
        dropoff_location=dropoff_point,
        dropoff_name=request.dropoff_name,
        status="pending"
    )
    db.add(new_request)
    await db.commit()
    await db.refresh(new_request)
    
    # Trigger matching engine
    await find_matching_rick(new_request.id, db)
    
    return {"request_id": str(new_request.id), "status": new_request.status}

@router.get("/ride-status/{request_id}")
async def get_ride_status(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id, RideRequest.passenger_id == user["user_id"]))
    ride_req = result.scalars().first()
    if not ride_req:
        raise HTTPException(status_code=404, detail="Ride request not found")
    
    return {"status": ride_req.status, "matched_ride_id": str(ride_req.matched_ride_id) if ride_req.matched_ride_id else None}

@router.post("/cancel/{request_id}")
async def cancel_ride(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id, RideRequest.passenger_id == user["user_id"]))
    ride_req = result.scalars().first()
    if not ride_req:
        raise HTTPException(status_code=404, detail="Ride request not found")
    
    if ride_req.status not in ["pending", "queued"]:
        raise HTTPException(status_code=400, detail="Cannot cancel in current state")
        
    ride_req.status = "cancelled"
    await db.commit()
    return {"message": "Ride cancelled successfully"}

@router.get("/ride-history")
async def ride_history(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RideRequest).filter(RideRequest.passenger_id == user["user_id"]).order_by(RideRequest.created_at.desc()))
    history = result.scalars().all()
    return [{"request_id": str(r.id), "status": r.status, "date": r.created_at} for r in history]
