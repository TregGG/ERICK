from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models.rickshaw import Rickshaw
from ..models.ride import RideRequest, RidePassenger, Ride
from ..models.user import User
from ..schemas import RickshawCreate, SettingsUpdate
from ..auth.jwt import get_current_user
from ..services.matching import recheck_queue, find_matching_rick, record_rejection
from ..services.routing import update_ride_route, sequence_stops
from ..websocket.manager import manager
from geoalchemy2.shape import to_shape
from typing import Dict, Any, List
from datetime import datetime
import uuid
import json

router = APIRouter(prefix="/api/driver", tags=["driver"])

async def get_driver_rickshaw(user_id: str, db: AsyncSession) -> Rickshaw:
    result = await db.execute(select(Rickshaw).filter(Rickshaw.driver_id == user_id))
    rick = result.scalars().first()
    if not rick:
        raise HTTPException(status_code=404, detail="Rickshaw profile not found")
    return rick

@router.get("/my-rickshaw")
async def get_my_rickshaw(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rickshaw).filter(Rickshaw.driver_id == user["user_id"]))
    rick = result.scalars().first()
    if not rick:
        return {"registered": False, "rickshaw": None}
    return {
        "registered": True,
        "rickshaw": {
            "id": str(rick.id),
            "vehicle_number": rick.vehicle_number,
            "capacity": rick.capacity,
            "is_active": rick.is_active,
            "max_detour_meters": rick.max_detour_meters
        }
    }

@router.post("/register-rickshaw")
async def register_rickshaw(request: RickshawCreate, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    clean_vnum = request.vehicle_number.strip().upper()
    existing = await db.execute(select(Rickshaw).filter(Rickshaw.driver_id == user["user_id"]))
    rick = existing.scalars().first()
    if rick:
        rick.vehicle_number = clean_vnum
        await db.commit()
        return {"message": "Rickshaw profile updated", "vehicle_number": rick.vehicle_number}
        
    rick = Rickshaw(driver_id=user["user_id"], vehicle_number=clean_vnum)
    db.add(rick)
    await db.commit()
    return {"message": "Rickshaw registered successfully", "vehicle_number": rick.vehicle_number}

@router.post("/go-online")
async def go_online(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rick = await get_driver_rickshaw(user["user_id"], db)
    rick.is_active = True
    if rick.current_location is None:
        from ..config import settings
        from shapely.geometry import Point
        from geoalchemy2.shape import from_shape
        rick.current_location = from_shape(Point(settings.CAMPUS_CENTER_LNG, settings.CAMPUS_CENTER_LAT), srid=4326)
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
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id))
    ride_req = result.scalars().first()
    if not ride_req:
        raise HTTPException(status_code=404, detail="Ride request not found")

    rick = await get_driver_rickshaw(user["user_id"], db)

    # Idempotent check: if already matched to this driver's active ride
    ride_res = await db.execute(select(Ride).filter(Ride.rickshaw_id == rick.id, Ride.status == "active"))
    active_ride = ride_res.scalars().first()
    if ride_req.status == "matched" and active_ride and ride_req.matched_ride_id == active_ride.id:
        return {"message": "Accepted passenger", "ride_id": str(active_ride.id)}

    if ride_req.status not in ["pending", "queued"]:
        raise HTTPException(status_code=400, detail=f"Request cannot be accepted in state: {ride_req.status}")

    if rick.current_passenger_count >= rick.capacity:
        raise HTTPException(status_code=400, detail="Rickshaw is already full (max 4 passengers)")

    # Find existing active ride or create one
    if not active_ride:
        active_ride = Ride(rickshaw_id=rick.id, status="active", started_at=datetime.utcnow())
        db.add(active_ride)
        await db.flush()

    ride_req.matched_ride_id = active_ride.id
    ride_req.status = "matched"

    # Add RidePassenger record
    rp_res = await db.execute(
        select(RidePassenger).filter(RidePassenger.ride_id == active_ride.id, RidePassenger.request_id == ride_req.id)
    )
    rp = rp_res.scalars().first()
    if not rp:
        rp = RidePassenger(ride_id=active_ride.id, request_id=ride_req.id, status="waiting_pickup")
        db.add(rp)

    rick.current_passenger_count += 1
    await db.commit()

    # Get driver display name
    driver_user = await db.get(User, user["user_id"])
    driver_name = (driver_user.name or driver_user.username or "Driver") if driver_user else "Driver"

    # Notify passenger via WebSocket
    await manager.send_to_user(
        str(ride_req.passenger_id),
        {
            "type": "ride_matched",
            "data": {
                "ride_id": str(active_ride.id),
                "driver_name": driver_name,
                "vehicle_number": rick.vehicle_number,
                "eta_minutes": 3
            }
        }
    )

    # Calculate and broadcast updated dynamic route to driver
    d_lat, d_lng = 28.6139, 77.2090
    if rick.current_location is not None:
        pt = to_shape(rick.current_location)
        d_lat, d_lng = pt.y, pt.x
    await update_ride_route(active_ride.id, d_lat, d_lng, db)

    return {"message": "Accepted passenger", "ride_id": str(active_ride.id)}

@router.post("/reject-passenger/{request_id}")
async def reject_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id))
    ride_req = result.scalars().first()
    if ride_req and ride_req.status in ["pending", "queued"]:
        ride_req.status = "queued"
        await db.commit()

        # Exclude this driver and cascade to next best candidate rickshaw
        rick = await get_driver_rickshaw(user["user_id"], db)
        if rick:
            record_rejection(request_id, rick.id)
            await find_matching_rick(request_id, db, exclude_rick_ids=[rick.id])

    return {"message": "Rejected passenger"}

@router.post("/pickup/{request_id}")
async def pickup_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id))
    ride_req = result.scalars().first()
    if not ride_req:
        raise HTTPException(status_code=404, detail="Ride request not found")

    rick = await get_driver_rickshaw(user["user_id"], db)

    rp_res = await db.execute(select(RidePassenger).filter(RidePassenger.request_id == ride_req.id))
    rp = rp_res.scalars().first()
    if rp:
        rp.status = "in_vehicle"
        rp.picked_up_at = datetime.utcnow()

    ride_req.status = "picked_up"
    await db.commit()

    # Notify passenger
    await manager.send_to_user(
        str(ride_req.passenger_id),
        {
            "type": "ride_status",
            "data": {
                "status": "picked_up"
            }
        }
    )

    # Recalculate route after pickup
    if ride_req.matched_ride_id:
        d_lat, d_lng = 28.6139, 77.2090
        if rick.current_location is not None:
            pt = to_shape(rick.current_location)
            d_lat, d_lng = pt.y, pt.x
        await update_ride_route(ride_req.matched_ride_id, d_lat, d_lng, db)

    return {"message": "Passenger picked up"}

@router.post("/dropoff/{request_id}")
async def dropoff_passenger(request_id: str, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id))
    ride_req = result.scalars().first()
    if not ride_req:
        raise HTTPException(status_code=404, detail="Ride request not found")

    rick = await get_driver_rickshaw(user["user_id"], db)

    rp_res = await db.execute(select(RidePassenger).filter(RidePassenger.request_id == ride_req.id))
    rp = rp_res.scalars().first()
    if rp:
        rp.status = "dropped_off"
        rp.dropped_off_at = datetime.utcnow()

    ride_req.status = "completed"
    rick.current_passenger_count = max(0, rick.current_passenger_count - 1)

    # Check if any remaining passengers in active ride
    ride_completed = False
    if ride_req.matched_ride_id:
        active_remaining = await db.execute(
            select(RidePassenger)
            .filter(RidePassenger.ride_id == ride_req.matched_ride_id, RidePassenger.status != "dropped_off")
        )
        if not active_remaining.scalars().first():
            ride = await db.get(Ride, ride_req.matched_ride_id)
            if ride:
                ride.status = "completed"
                ride.completed_at = datetime.utcnow()
                ride_completed = True

    await db.commit()

    # Notify passenger
    await manager.send_to_user(
        str(ride_req.passenger_id),
        {
            "type": "ride_status",
            "data": {
                "status": "completed"
            }
        }
    )

    # Update route for remaining passengers if ride is still active
    if ride_req.matched_ride_id and not ride_completed:
        d_lat, d_lng = 28.6139, 77.2090
        if rick.current_location is not None:
            pt = to_shape(rick.current_location)
            d_lat, d_lng = pt.y, pt.x
        await update_ride_route(ride_req.matched_ride_id, d_lat, d_lng, db)

    # Recheck queue since rickshaw has freed up capacity
    await recheck_queue(db)

    return {"message": "Passenger dropped off"}

@router.get("/active-ride")
async def get_active_ride(user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rick = await get_driver_rickshaw(user["user_id"], db)
    ride_res = await db.execute(select(Ride).filter(Ride.rickshaw_id == rick.id, Ride.status == "active"))
    active_ride = ride_res.scalars().first()
    if not active_ride:
        return {"active_ride": None, "passengers": [], "stops": [], "next_stop": None}

    rows = await db.execute(
        select(RidePassenger, RideRequest, User)
        .join(RideRequest, RidePassenger.request_id == RideRequest.id)
        .join(User, RideRequest.passenger_id == User.id)
        .filter(RidePassenger.ride_id == active_ride.id)
        .order_by(RidePassenger.picked_up_at.desc().nulls_first())
    )

    passengers = []
    for rp, req, pass_user in rows.all():
        p_geom = to_shape(req.pickup_location) if req.pickup_location is not None else None
        d_geom = to_shape(req.dropoff_location) if req.dropoff_location is not None else None

        passengers.append({
            "request_id": str(req.id),
            "name": pass_user.name or pass_user.username or "Passenger",
            "phone": pass_user.phone,
            "status": rp.status,
            "pickup_name": req.pickup_name or "Pickup",
            "pickup_lat": p_geom.y if p_geom else None,
            "pickup_lng": p_geom.x if p_geom else None,
            "dropoff_name": req.dropoff_name or "Dropoff",
            "dropoff_lat": d_geom.y if d_geom else None,
            "dropoff_lng": d_geom.x if d_geom else None
        })

    # Driver coordinates
    d_lat, d_lng = 28.6139, 77.2090
    if rick.current_location is not None:
        pt = to_shape(rick.current_location)
        d_lat, d_lng = pt.y, pt.x

    # Compute ordered sequence of remaining stops
    active_passengers = [p for p in passengers if p["status"] != "dropped_off"]
    stops = sequence_stops(d_lat, d_lng, active_passengers) if active_passengers else []

    polyline = []
    if active_ride.route_polyline:
        try:
            polyline = json.loads(active_ride.route_polyline)
        except Exception:
            polyline = []

    return {
        "active_ride": {
            "id": str(active_ride.id),
            "status": active_ride.status,
            "route_polyline": polyline
        },
        "passengers": passengers,
        "stops": stops,
        "next_stop": stops[0] if stops else None
    }

@router.put("/settings")
async def update_settings(settings: SettingsUpdate, user: Dict[str, Any] = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rick = await get_driver_rickshaw(user["user_id"], db)
    rick.max_detour_meters = settings.max_detour_meters
    await db.commit()
    await recheck_queue(db)
    return {"message": "Settings updated"}

