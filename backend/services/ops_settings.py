"""Administrator-configurable operational parameters (fare, dispatch policy).

Values live in the system_settings table so they survive restarts and every
change is attributable. Defaults apply until an administrator overrides them.
"""
import time
from dataclasses import asdict, dataclass, fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SystemSetting

OPS_KEY = "operations"


@dataclass(frozen=True)
class OpsSettings:
    # Flat development fare carried over from the original app (Rs 10), stored in paise.
    fare_paise: int = 1000
    dispatch_radius_m: int = 2500
    offer_timeout_s: int = 20
    location_stale_s: int = 45
    search_timeout_s: int = 300
    max_offers_per_ride: int = 8
    arrival_radius_m: int = 300
    no_show_wait_s: int = 180
    # Share of each fare credited to the driver. 100 = no platform commission (no commission model is defined yet).
    driver_share_percent: int = 100
    require_driver_verification: bool = True

    LIMITS = {
        "driver_share_percent": (0, 100),
        "no_show_wait_s": (0, 1800),
        "fare_paise": (0, 50000),
        "dispatch_radius_m": (200, 10000),
        "offer_timeout_s": (10, 120),
        "location_stale_s": (15, 600),
        "search_timeout_s": (60, 1800),
        "max_offers_per_ride": (1, 50),
        "arrival_radius_m": (50, 2000),
    }


def validate_update(current: OpsSettings, changes: dict) -> OpsSettings:
    allowed = {f.name for f in fields(OpsSettings)}
    merged = asdict(current)
    for key, value in changes.items():
        if key not in allowed:
            raise ValueError(f"Unknown setting: {key}")
        if key == "require_driver_verification":
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be a whole number")
            low, high = OpsSettings.LIMITS[key]
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
        merged[key] = value
    return OpsSettings(**merged)


_cache = {"value": None, "at": 0.0}


async def get_ops(db: AsyncSession, max_age: float = 5.0) -> OpsSettings:
    if _cache["value"] is not None and time.monotonic() - _cache["at"] < max_age:
        return _cache["value"]
    row = await db.get(SystemSetting, OPS_KEY)
    known = {f.name for f in fields(OpsSettings)}
    value = OpsSettings(**{k: v for k, v in (row.value if row else {}).items() if k in known})
    _cache.update(value=value, at=time.monotonic())
    return value


async def save_ops(db: AsyncSession, value: OpsSettings, actor_id) -> None:
    row = await db.get(SystemSetting, OPS_KEY)
    if row is None:
        db.add(SystemSetting(key=OPS_KEY, value=asdict(value), updated_by=actor_id))
    else:
        row.value = asdict(value)
        row.updated_by = actor_id
    invalidate()


def invalidate() -> None:
    _cache.update(value=None, at=0.0)


async def list_settings(db: AsyncSession) -> dict:
    result = await db.execute(select(SystemSetting))
    return {row.key: row.value for row in result.scalars()}
