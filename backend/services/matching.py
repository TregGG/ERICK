import math
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2.shape import to_shape
from ..models.ride import Ride, RideRequest, RidePassenger
from ..models.rickshaw import Rickshaw
from ..models.user import User
from ..websocket.manager import manager
from ..config import settings
from .routing import sequence_stops, update_ride_route

def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates forward azimuth bearing in degrees [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing_rad = math.atan2(y, x)
    return (math.degrees(bearing_rad) + 360.0) % 360.0

def calculate_direction_difference(b1: float, b2: float) -> float:
    """Calculates smallest angular difference between two bearings in [0, 180]."""
    diff = abs(b1 - b2) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
    return diff

def calculate_direction_similarity(b1: float, b2: float) -> float:
    """Cosine similarity of two bearings: 1.0 (exact same) to -1.0 (exact opposite)."""
    diff = calculate_direction_difference(b1, b2)
    return math.cos(math.radians(diff))

async def calculate_route_detour(
    rick_lat: float,
    rick_lng: float,
    candidate_passengers: List[Dict[str, Any]],
    new_passenger: Dict[str, Any]
) -> int:
    """
    Computes added route detour (meters) when inserting a new passenger
    into the active stop sequence.
    """
    # 1. Baseline distance for current passengers
    curr_stops = sequence_stops(rick_lat, rick_lng, candidate_passengers)
    curr_dist = 0.0
    if curr_stops:
        prev = (rick_lat, rick_lng)
        for s in curr_stops:
            curr_dist += haversine_distance_meters(prev[0], prev[1], s["lat"], s["lng"])
            prev = (s["lat"], s["lng"])

    # 2. Combined distance with new passenger
    pooled_passengers = candidate_passengers + [new_passenger]
    pooled_stops = sequence_stops(rick_lat, rick_lng, pooled_passengers)
    pooled_dist = 0.0
    if pooled_stops:
        prev = (rick_lat, rick_lng)
        for s in pooled_stops:
            pooled_dist += haversine_distance_meters(prev[0], prev[1], s["lat"], s["lng"])
            prev = (s["lat"], s["lng"])

    # Direct trip distance of new passenger
    direct_trip = haversine_distance_meters(
        new_passenger["pickup_lat"], new_passenger["pickup_lng"],
        new_passenger["dropoff_lat"], new_passenger["dropoff_lng"]
    )

    # Added detour is extra distance beyond direct trip
    added_detour = max(0.0, pooled_dist - curr_dist - direct_trip)
    # Also factor in approach distance to pickup
    approach_dist = haversine_distance_meters(
        rick_lat, rick_lng,
        new_passenger["pickup_lat"], new_passenger["pickup_lng"]
    )

    detour_meters = int(max(added_detour, approach_dist * 0.5))
    return detour_meters

_rejected_request_ricks: Dict[str, set] = {}

def record_rejection(request_id: str, rick_id: Any):
    rid_str = str(request_id)
    if rid_str not in _rejected_request_ricks:
        _rejected_request_ricks[rid_str] = set()
    _rejected_request_ricks[rid_str].add(str(rick_id))

async def find_matching_rick(
    request_id: str,
    db: AsyncSession,
    exclude_rick_ids: Optional[List[Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Smart Matching Engine:
    - Direction clustering & bearing alignment
    - Route detour calculation
    - Driver max_detour_meters tolerance enforcement
    - Auto-accept for on-route requests (detour <= 60m)
    - Manual popup for detour between 60m and max_detour_meters
    """
    if exclude_rick_ids is None:
        exclude_rick_ids = []

    # Combine explicit exclusions with historical rejections (normalized as strings)
    combined_exclusions = {str(x) for x in exclude_rick_ids} | _rejected_request_ricks.get(str(request_id), set())

    # Fetch ride request
    result = await db.execute(select(RideRequest).filter(RideRequest.id == request_id))
    ride_req = result.scalars().first()
    if not ride_req or ride_req.status in ["matched", "picked_up", "completed", "cancelled"]:
        return None

    # Fetch passenger user details
    passenger_user = await db.get(User, ride_req.passenger_id)
    passenger_name = (passenger_user.name or passenger_user.username or "Passenger") if passenger_user else "Passenger"

    # Extract coordinates from geometry
    pickup_geom = to_shape(ride_req.pickup_location)
    dropoff_geom = to_shape(ride_req.dropoff_location)
    p_lat, p_lng = pickup_geom.y, pickup_geom.x
    d_lat, d_lng = dropoff_geom.y, dropoff_geom.x

    # Compute passenger trip bearing
    p_bearing = calculate_bearing(p_lat, p_lng, d_lat, d_lng)

    # Query active rickshaws with remaining capacity
    ricks_result = await db.execute(
        select(Rickshaw)
        .filter(Rickshaw.is_active == True, Rickshaw.current_passenger_count < Rickshaw.capacity)
    )
    all_active_ricks = ricks_result.scalars().all()

    # Filter out excluded rickshaws (e.g. previously rejected)
    candidate_ricks = [r for r in all_active_ricks if str(r.id) not in combined_exclusions]

    if not candidate_ricks:
        ride_req.status = "queued"
        await db.commit()
        await manager.send_to_user(
            str(ride_req.passenger_id),
            {"type": "queue_position", "data": {"position": 1}}
        )
        return None

    scored_candidates = []

    new_passenger_data = {
        "request_id": str(ride_req.id),
        "name": passenger_name,
        "status": "waiting_pickup",
        "pickup_name": ride_req.pickup_name or "Pickup",
        "pickup_lat": p_lat,
        "pickup_lng": p_lng,
        "dropoff_name": ride_req.dropoff_name or "Dropoff",
        "dropoff_lat": d_lat,
        "dropoff_lng": d_lng
    }

    for rick in candidate_ricks:
        r_lat, r_lng = 28.6139, 77.2090
        if rick.current_location is not None:
            pt = to_shape(rick.current_location)
            r_lat, r_lng = pt.y, pt.x

        dist_to_pickup = haversine_distance_meters(r_lat, r_lng, p_lat, p_lng)

        # Case A: Rickshaw is EMPTY (0 passengers)
        if rick.current_passenger_count == 0:
            detour_meters = int(dist_to_pickup)
            dir_similarity = 1.0  # Empty rickshaw is fully compatible with passenger direction
            angle_diff = 0.0

        # Case B: Rickshaw already has passengers (Pooled Ride)
        else:
            # Fetch active passengers in this rickshaw's active ride
            ride_res = await db.execute(
                select(Ride).filter(Ride.rickshaw_id == rick.id, Ride.status == "active")
            )
            active_ride = ride_res.scalars().first()

            existing_passengers = []
            target_dropoff = None

            if active_ride:
                rows = await db.execute(
                    select(RidePassenger, RideRequest, User)
                    .join(RideRequest, RidePassenger.request_id == RideRequest.id)
                    .join(User, RideRequest.passenger_id == User.id)
                    .filter(RidePassenger.ride_id == active_ride.id, RidePassenger.status != "dropped_off")
                )
                for rp, req, u in rows.all():
                    p_g = to_shape(req.pickup_location) if req.pickup_location is not None else None
                    d_g = to_shape(req.dropoff_location) if req.dropoff_location is not None else None
                    if d_g:
                        target_dropoff = (d_g.y, d_g.x)
                    existing_passengers.append({
                        "request_id": str(req.id),
                        "name": u.name or u.username or "Passenger",
                        "status": rp.status,
                        "pickup_name": req.pickup_name or "Pickup",
                        "pickup_lat": p_g.y if p_g else None,
                        "pickup_lng": p_g.x if p_g else None,
                        "dropoff_name": req.dropoff_name or "Dropoff",
                        "dropoff_lat": d_g.y if d_g else None,
                        "dropoff_lng": d_g.x if d_g else None,
                        "_rp_obj": rp
                    })

            # Calculate primary direction of active ride
            if target_dropoff:
                r_bearing = calculate_bearing(r_lat, r_lng, target_dropoff[0], target_dropoff[1])
            elif rick.heading is not None and rick.heading > 0:
                r_bearing = rick.heading
            else:
                r_bearing = p_bearing  # Fallback

            angle_diff = calculate_direction_difference(p_bearing, r_bearing)
            dir_similarity = calculate_direction_similarity(p_bearing, r_bearing)

            # Hard filter: if heading in opposite direction (> 110 degrees), disqualify
            if angle_diff > 110.0:
                continue

            # Calculate route detour
            detour_meters = await calculate_route_detour(
                r_lat, r_lng, existing_passengers, new_passenger_data
            )

        # Check driver detour tolerance
        max_tolerance = rick.max_detour_meters or settings.DEFAULT_MAX_DETOUR_METERS
        if detour_meters > max_tolerance:
            # Exceeds driver tolerance -> disqualify candidate
            continue

        # Candidate scoring
        detour_score = max(0.0, 1000.0 - detour_meters)
        dir_score = max(0.0, dir_similarity) * 500.0
        seat_score = (rick.capacity - rick.current_passenger_count) * 100.0
        proximity_score = max(0.0, 500.0 - dist_to_pickup)

        total_score = detour_score + dir_score + seat_score + proximity_score

        scored_candidates.append({
            "rick": rick,
            "detour_meters": detour_meters,
            "dir_similarity": dir_similarity,
            "angle_diff": angle_diff,
            "dist_to_pickup": dist_to_pickup,
            "total_score": total_score,
            "r_lat": r_lat,
            "r_lng": r_lng
        })

    # If no candidate qualified within direction and detour thresholds
    if not scored_candidates:
        ride_req.status = "queued"
        await db.commit()
        await manager.send_to_user(
            str(ride_req.passenger_id),
            {"type": "queue_position", "data": {"position": 1}}
        )
        return None

    # Pick highest scoring candidate
    scored_candidates.sort(key=lambda x: x["total_score"], reverse=True)
    best = scored_candidates[0]
    best_rick = best["rick"]
    detour_meters = best["detour_meters"]
    r_lat, r_lng = best["r_lat"], best["r_lng"]

    # ---------------------------------------------------------
    # DECISION: Auto-Accept vs Manual Popup
    # ---------------------------------------------------------
    AUTO_ACCEPT_THRESHOLD_METERS = 60

    if detour_meters <= AUTO_ACCEPT_THRESHOLD_METERS:
        # EXECUTE AUTO-ACCEPT DIRECTLY
        ride_res = await db.execute(
            select(Ride).filter(Ride.rickshaw_id == best_rick.id, Ride.status == "active")
        )
        active_ride = ride_res.scalars().first()
        if not active_ride:
            active_ride = Ride(rickshaw_id=best_rick.id, status="active")
            db.add(active_ride)
            await db.flush()

        ride_req.matched_ride_id = active_ride.id
        ride_req.status = "matched"

        rp = RidePassenger(ride_id=active_ride.id, request_id=ride_req.id, status="waiting_pickup")
        db.add(rp)
        best_rick.current_passenger_count += 1
        await db.commit()

        # Notify passenger ride_matched
        driver_user = await db.get(User, best_rick.driver_id)
        driver_name = (driver_user.name or driver_user.username or "Driver") if driver_user else "Driver"
        eta_minutes = max(1, int(best["dist_to_pickup"] / 250))

        await manager.send_to_user(
            str(ride_req.passenger_id),
            {
                "type": "ride_matched",
                "data": {
                    "ride_id": str(active_ride.id),
                    "driver_name": driver_name,
                    "vehicle_number": best_rick.vehicle_number,
                    "eta_minutes": eta_minutes
                }
            }
        )

        # Recalculate route and broadcast to driver & passengers
        await update_ride_route(active_ride.id, r_lat, r_lng, db)

        # Notify driver that passenger was auto-pooled
        await manager.send_to_user(
            str(best_rick.driver_id),
            {
                "type": "new_passenger_request",
                "data": {
                    "request_id": str(ride_req.id),
                    "passenger_name": passenger_name,
                    "pickup": {"name": ride_req.pickup_name or "Pickup", "lat": p_lat, "lng": p_lng},
                    "dropoff": {"name": ride_req.dropoff_name or "Dropoff", "lat": d_lat, "lng": d_lng},
                    "detour_meters": detour_meters,
                    "is_auto_accepted": True
                }
            }
        )

        return {
            "matched": True,
            "is_auto_accepted": True,
            "rick_id": str(best_rick.id),
            "detour_meters": detour_meters
        }

    else:
        # MANUAL ACCEPT POPUP
        ride_req.status = "queued"
        await db.commit()

        await manager.send_to_user(
            str(ride_req.passenger_id),
            {"type": "queue_position", "data": {"position": 1}}
        )

        payload = {
            "type": "new_passenger_request",
            "data": {
                "request_id": str(ride_req.id),
                "passenger_name": passenger_name,
                "pickup": {
                    "name": ride_req.pickup_name or "Pickup Location",
                    "lat": p_lat,
                    "lng": p_lng
                },
                "dropoff": {
                    "name": ride_req.dropoff_name or "Dropoff Location",
                    "lat": d_lat,
                    "lng": d_lng
                },
                "detour_meters": detour_meters,
                "is_auto_accepted": False
            }
        }

        await manager.send_to_user(str(best_rick.driver_id), payload)

        return {
            "matched": True,
            "is_auto_accepted": False,
            "rick_id": str(best_rick.id),
            "detour_meters": detour_meters
        }

async def recheck_queue(db: AsyncSession):
    """Re-evaluates all queued requests when rickshaw state changes."""
    cutoff = datetime.utcnow() - timedelta(minutes=15)
    queued_reqs = await db.execute(
        select(RideRequest)
        .filter(RideRequest.status.in_(["pending", "queued"]), RideRequest.created_at >= cutoff)
        .order_by(RideRequest.created_at.asc())
    )
    for req in queued_reqs.scalars().all():
        await find_matching_rick(req.id, db)
