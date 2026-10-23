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
        {"name": "Central Library", "lat": 28.6145, "lng": 77.2105},
        {"name": "Hostel Block A", "lat": 28.6155, "lng": 77.2118},
        {"name": "Hostel Block B", "lat": 28.6162, "lng": 77.2125},
        {"name": "Girls Hostel", "lat": 28.6150, "lng": 77.2065},
        {"name": "Cafeteria & Canteen", "lat": 28.6132, "lng": 77.2100},
        {"name": "Academic Block 1", "lat": 28.6122, "lng": 77.2082},
        {"name": "Academic Block 2", "lat": 28.6115, "lng": 77.2095},
        {"name": "Sports Complex", "lat": 28.6168, "lng": 77.2085},
        {"name": "Admin Building", "lat": 28.6140, "lng": 77.2075},
        {"name": "Auditorium", "lat": 28.6128, "lng": 77.2115},
    ]

@router.post("/check-geofence")
async def check_geofence(req: PointRequest):
    is_inside = is_within_campus(req.lat, req.lng)
    return {"is_inside": is_inside}
