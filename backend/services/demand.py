"""Where passengers have been asking for rides, for the drivers' demand map.

Pickups from recent requests are counted in a ~150 m grid, so drivers see busy areas
rather than individual passengers. "Waiting" counts requests still looking for a
driver; "missed" counts requests that ended without one.
"""
import math
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import Ride
from ..states import SEARCHING, RideStatus, values
from .ops_settings import OpsSettings
from .rides import iso

CELL_M = 150
_M_PER_DEG_LAT = 111_320


async def demand_cells(db: AsyncSession, ops: OpsSettings) -> dict:
    now = utcnow()
    since = now - timedelta(minutes=ops.demand_window_min)
    rows = (await db.execute(
        select(Ride.pickup_lat, Ride.pickup_lng, Ride.status).where(Ride.created_at >= since)
    )).all()
    cells = {}
    for lat, lng, status in rows:
        lat_step = CELL_M / _M_PER_DEG_LAT
        lng_step = CELL_M / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
        key = (math.floor(lat / lat_step), math.floor(lng / lng_step))
        cell = cells.setdefault(key, {"lat": (key[0] + 0.5) * lat_step, "lng": (key[1] + 0.5) * lng_step,
                                      "requests": 0, "waiting": 0, "missed": 0})
        cell["requests"] += 1
        cell["waiting"] += status in values(SEARCHING)
        cell["missed"] += status == RideStatus.NO_DRIVER_AVAILABLE.value
    ranked = sorted(cells.values(), key=lambda c: (c["waiting"], c["requests"]), reverse=True)
    for cell in ranked:
        cell["lat"], cell["lng"] = round(cell["lat"], 5), round(cell["lng"], 5)
    return {"window_min": ops.demand_window_min, "cell_m": CELL_M, "cells": ranked, "generated_at": iso(now)}
