from fastapi import APIRouter
from pydantic import BaseModel
from ..services.geofence import is_within_campus

router = APIRouter(prefix="/api/locations", tags=["locations"])

class PointRequest(BaseModel):
    lat: float
    lng: float

@router.get("/landmarks")
async def get_landmarks():
    return [
        {"name": "Main Gate", "lat": 28.6139, "lng": 77.2090},
        {"name": "Library", "lat": 28.6145, "lng": 77.2100},
        {"name": "Hostel Block A", "lat": 28.6150, "lng": 77.2110},
        {"name": "Academic Block 1", "lat": 28.6120, "lng": 77.2080},
    ]

@router.post("/check-geofence")
async def check_geofence(req: PointRequest):
    is_inside = is_within_campus(req.lat, req.lng)
    return {"is_inside": is_inside}
