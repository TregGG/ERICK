import math
from ..config import settings

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000 # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def is_within_campus(lat: float, lng: float) -> bool:
    dist = haversine(lat, lng, settings.CAMPUS_CENTER_LAT, settings.CAMPUS_CENTER_LNG)
    return dist <= settings.CAMPUS_RADIUS_METERS
