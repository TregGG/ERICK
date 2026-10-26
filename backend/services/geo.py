"""Campus geography: distance helpers and the operating-area geofence.

Geometry is computed in a local equirectangular projection (metres) centred on
the campus, which is accurate to well under a metre at campus scale.
"""
import json
import math
from dataclasses import dataclass
from typing import Optional

from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import BACKEND_DIR
from ..models import CampusZone

CAMPUS_FIXTURE = BACKEND_DIR / "data" / "iitr_campus.json"
EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass
class CampusGeometry:
    origin_lat: float
    origin_lng: float
    area_m: object  # shapely geometry in local metres
    geojson: list   # GeoJSON geometries (lng/lat) for display

    def _project(self, lat: float, lng: float):
        x = math.radians(lng - self.origin_lng) * EARTH_RADIUS_M * math.cos(math.radians(self.origin_lat))
        y = math.radians(lat - self.origin_lat) * EARTH_RADIUS_M
        return x, y

    def distance_outside_m(self, lat: float, lng: float) -> float:
        """0 when inside the operating area, otherwise metres to its edge."""
        return float(self.area_m.distance(Point(self._project(lat, lng))))

    def contains(self, lat: float, lng: float, buffer_m: float = 0.0) -> bool:
        return self.distance_outside_m(lat, lng) <= buffer_m


_campus: Optional[CampusGeometry] = None


def _build(geometries: list) -> CampusGeometry:
    shapes = [shape(g) for g in geometries]
    centroid = unary_union(shapes).centroid
    geo = CampusGeometry(origin_lat=centroid.y, origin_lng=centroid.x, area_m=None, geojson=geometries)
    projected = []
    for s in shapes:
        polygons = [s] if s.geom_type == "Polygon" else list(s.geoms)
        for poly in polygons:
            projected.append(Polygon([geo._project(lat, lng) for lng, lat in poly.exterior.coords]))
    geo.area_m = unary_union(projected)
    return geo


async def load_campus(db: AsyncSession) -> CampusGeometry:
    """Load the operating area (operating zones if defined, else the campus boundary)."""
    global _campus
    rows = (await db.execute(select(CampusZone).where(CampusZone.is_active.is_(True)))).scalars().all()
    operating = [json.loads(z.geojson) for z in rows if z.kind == "operating"]
    boundary = [json.loads(z.geojson) for z in rows if z.kind == "boundary"]
    geometries = operating or boundary
    if not geometries:
        fixture = json.loads(CAMPUS_FIXTURE.read_text(encoding="utf-8"))
        geometries = [{"type": "Polygon", "coordinates": fixture["boundary"]["coordinates"]}]
    _campus = _build(geometries)
    return _campus


async def get_campus(db: AsyncSession) -> CampusGeometry:
    return _campus or await load_campus(db)


def reset_campus_cache() -> None:
    global _campus
    _campus = None
