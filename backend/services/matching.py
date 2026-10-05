from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models.ride import RideRequest
from ..models.rickshaw import Rickshaw

async def find_matching_rick(request_id: str, db: AsyncSession):
    # Phase 1 simplistic matching: just mark as queued and wait for a rickshaw to pick up
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id))
    ride_req = result.scalars().first()
    if not ride_req:
        return
    
    # Try finding an active rickshaw (simple logic: first active with capacity)
    # In a real app, this would use PostGIS ST_Distance and heading logic.
    rick_result = await db.execute(
        select(Rickshaw)
        .filter(Rickshaw.is_active == True, Rickshaw.current_passenger_count < Rickshaw.capacity)
    )
    rick = rick_result.scalars().first()
    
    if rick:
        # We pretend it sent for manual accept or auto-assigned
        ride_req.status = "queued"
    else:
        ride_req.status = "queued"
        
    await db.commit()

async def recheck_queue(db: AsyncSession):
    # Re-evaluate all queued requests when rickshaw state changes
    queued_reqs = await db.execute(select(RideRequest).filter(RideRequest.status == "queued"))
    for req in queued_reqs.scalars().all():
        await find_matching_rick(req.id, db)
