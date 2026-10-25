"""Road routing through an OSRM-compatible HTTP API, with an explicitly labelled fallback.

The public OSRM demo server allows roughly one request per second and is not
meant for production traffic; requests are cached, throttled and time-limited.
Configure ROUTING_BASE_URL to a self-hosted OSRM instance for production.
When routing is unavailable the response says so (source="straight_line",
approximate=True) instead of passing straight-line distance off as road distance.
"""
import asyncio
import time
from collections import OrderedDict
from typing import Optional

import httpx

from ..config import settings
from .geo import haversine_m

_CACHE_MAX = 500
_CACHE_TTL = 3600.0
_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_lock = asyncio.Lock()
_last_request = 0.0
_client: Optional[httpx.AsyncClient] = None


def _speed_mps() -> float:
    return settings.ERICKSHAW_AVG_SPEED_KMH / 3.6


def _key(a, b) -> tuple:
    return (round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4))


def straight_line_estimate(a, b) -> dict:
    distance = haversine_m(a[0], a[1], b[0], b[1])
    return {
        "distance_m": int(round(distance)),
        "duration_s": int(round(distance / _speed_mps())),
        "geometry": [[a[0], a[1]], [b[0], b[1]]],
        "source": "straight_line",
        "approximate": True,
        "note": "Routing service unavailable: straight-line distance, not road distance.",
    }


async def _fetch_osrm(a, b) -> Optional[dict]:
    global _last_request, _client
    if not settings.ROUTING_BASE_URL:
        return None
    url = (f"{settings.ROUTING_BASE_URL.rstrip('/')}/route/v1/{settings.ROUTING_PROFILE}/"
           f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}")
    async with _lock:
        wait = settings.ROUTING_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
        if _client is None:
            _client = httpx.AsyncClient(
                timeout=settings.ROUTING_TIMEOUT_SECONDS,
                headers={"User-Agent": "IITR-Campus-Erickshaw/1.0 (campus mobility project)"},
            )
        try:
            response = await _client.get(url, params={"overview": "full", "geometries": "geojson"})
        except httpx.HTTPError:
            return None
    if response.status_code != 200:
        return None
    data = response.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    route = data["routes"][0]
    distance = float(route["distance"])
    return {
        "distance_m": int(round(distance)),
        "duration_s": int(round(distance / _speed_mps())),
        "geometry": [[lat, lng] for lng, lat in route["geometry"]["coordinates"]],
        "source": "osrm",
        "approximate": False,
        "note": f"Road distance from OSRM; time assumes {settings.ERICKSHAW_AVG_SPEED_KMH:g} km/h average.",
    }


async def get_route(a, b) -> dict:
    """a and b are (lat, lng) tuples."""
    key = _key(a, b)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        _cache.move_to_end(key)
        return cached[1]
    route = await _fetch_osrm(a, b)
    if route is None:
        return straight_line_estimate(a, b)
    _cache[key] = (time.monotonic(), route)
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return route


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
