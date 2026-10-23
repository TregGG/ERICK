import math
import json
import httpx
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from geoalchemy2.shape import to_shape
from ..models.ride import Ride, RideRequest, RidePassenger
from ..models.rickshaw import Rickshaw
from ..models.user import User
from ..websocket.manager import manager

def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def sequence_stops(driver_lat: float, driver_lng: float, passengers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sequences pickup and dropoff stops using precedence-constrained greedy nearest-neighbor.
    Constraint: For any passenger, pickup MUST be visited before dropoff.
    """
    stop_pool = []
    picked_up_requests = set()

    for p in passengers:
        req_id = p["request_id"]
        status = p.get("status", "waiting_pickup")

        if status == "waiting_pickup":
            # Needs pickup first, then dropoff
            if p.get("pickup_lat") is not None and p.get("pickup_lng") is not None:
                stop_pool.append({
                    "type": "pickup",
                    "request_id": req_id,
                    "passenger_name": p["name"],
                    "name": p.get("pickup_name", "Pickup"),
                    "lat": p["pickup_lat"],
                    "lng": p["pickup_lng"]
                })
            if p.get("dropoff_lat") is not None and p.get("dropoff_lng") is not None:
                stop_pool.append({
                    "type": "dropoff",
                    "request_id": req_id,
                    "passenger_name": p["name"],
                    "name": p.get("dropoff_name", "Dropoff"),
                    "lat": p["dropoff_lat"],
                    "lng": p["dropoff_lng"]
                })
        elif status == "in_vehicle":
            # Already in vehicle, only needs dropoff
            picked_up_requests.add(req_id)
            if p.get("dropoff_lat") is not None and p.get("dropoff_lng") is not None:
                stop_pool.append({
                    "type": "dropoff",
                    "request_id": req_id,
                    "passenger_name": p["name"],
                    "name": p.get("dropoff_name", "Dropoff"),
                    "lat": p["dropoff_lat"],
                    "lng": p["dropoff_lng"]
                })

    ordered_stops = []
    curr_lat, curr_lng = driver_lat, driver_lng

    while stop_pool:
        # Find all valid next candidates (precedence check)
        valid_candidates = []
        for s in stop_pool:
            if s["type"] == "pickup":
                valid_candidates.append(s)
            elif s["type"] == "dropoff":
                if s["request_id"] in picked_up_requests:
                    valid_candidates.append(s)

        if not valid_candidates:
            # Fallback (should not occur under normal state transitions)
            valid_candidates = stop_pool

        # Pick nearest candidate from current location
        best_candidate = None
        min_dist = float("inf")

        for c in valid_candidates:
            dist = haversine_distance_meters(curr_lat, curr_lng, c["lat"], c["lng"])
            if dist < min_dist:
                min_dist = dist
                best_candidate = c

        ordered_stops.append(best_candidate)
        stop_pool.remove(best_candidate)

        if best_candidate["type"] == "pickup":
            picked_up_requests.add(best_candidate["request_id"])

        curr_lat, curr_lng = best_candidate["lat"], best_candidate["lng"]

    # Assign sequential order
    for idx, s in enumerate(ordered_stops):
        s["order"] = idx + 1

    return ordered_stops

async def fetch_road_route(coordinates: List[Tuple[float, float]]) -> Dict[str, Any]:
    """
    Fetches real road-network polyline from OpenStreetMap OSRM routing API.
    Falls back gracefully to straight-line segments if unreachable or offline.
    """
    if len(coordinates) < 2:
        return {
            "polyline": coordinates,
            "distance_meters": 0,
            "duration_seconds": 0
        }

    # Format OSRM query: {lng},{lat};{lng},{lat}...
    coords_str = ";".join(f"{round(lng, 6)},{round(lat, 6)}" for lat, lng in coordinates)
    url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("routes") and len(data["routes"]) > 0:
                    primary_route = data["routes"][0]
                    # GeoJSON is [lng, lat], convert to [lat, lng] for Leaflet
                    polyline = [[pt[1], pt[0]] for pt in primary_route["geometry"]["coordinates"]]
                    return {
                        "polyline": polyline,
                        "distance_meters": int(primary_route.get("distance", 0)),
                        "duration_seconds": int(primary_route.get("duration", 0))
                    }
    except Exception as e:
        print(f"OSRM routing service unavailable, using straight-line fallback: {e}")

    # Fallback to straight-line segment distances
    total_dist = 0.0
    for i in range(len(coordinates) - 1):
        total_dist += haversine_distance_meters(
            coordinates[i][0], coordinates[i][1],
            coordinates[i+1][0], coordinates[i+1][1]
        )

    # Assume average campus e-rickshaw speed: ~18 km/h = 5 m/s
    duration_estimate = int(total_dist / 5.0)

    return {
        "polyline": [[lat, lng] for lat, lng in coordinates],
        "distance_meters": int(total_dist),
        "duration_seconds": duration_estimate
    }

async def update_ride_route(
    ride_id: Any,
    driver_lat: float,
    driver_lng: float,
    db: AsyncSession
) -> Optional[Dict[str, Any]]:
    """
    Re-sequences stops, calculates road polyline, saves route to DB,
    and broadcasts route_updated event to driver via WebSocket.
    """
    ride = await db.get(Ride, ride_id)
    if not ride or ride.status != "active":
        return None

    rick = await db.get(Rickshaw, ride.rickshaw_id)
    if not rick:
        return None

    # Fetch active passengers in this ride
    rows = await db.execute(
        select(RidePassenger, RideRequest, User)
        .join(RideRequest, RidePassenger.request_id == RideRequest.id)
        .join(User, RideRequest.passenger_id == User.id)
        .filter(RidePassenger.ride_id == ride.id, RidePassenger.status != "dropped_off")
    )

    passengers = []
    for rp, req, pass_user in rows.all():
        p_geom = to_shape(req.pickup_location) if req.pickup_location is not None else None
        d_geom = to_shape(req.dropoff_location) if req.dropoff_location is not None else None
        passengers.append({
            "request_id": str(req.id),
            "passenger_id": str(req.passenger_id),
            "name": pass_user.name or pass_user.username or "Passenger",
            "status": rp.status,
            "pickup_name": req.pickup_name or "Pickup",
            "pickup_lat": p_geom.y if p_geom else None,
            "pickup_lng": p_geom.x if p_geom else None,
            "dropoff_name": req.dropoff_name or "Dropoff",
            "dropoff_lat": d_geom.y if d_geom else None,
            "dropoff_lng": d_geom.x if d_geom else None,
            "_rp_obj": rp
        })

    if not passengers:
        ride.route_polyline = "[]"
        await db.commit()
        return None

    # 1. Sequence the stops
    ordered_stops = sequence_stops(driver_lat, driver_lng, passengers)

    # 2. Update pickup_order and dropoff_order in DB
    order_map = {}
    for s in ordered_stops:
        req_id = s["request_id"]
        if req_id not in order_map:
            order_map[req_id] = {}
        order_map[req_id][s["type"]] = s["order"]

    for p in passengers:
        rp = p["_rp_obj"]
        req_id = p["request_id"]
        if req_id in order_map:
            if "pickup" in order_map[req_id]:
                rp.pickup_order = order_map[req_id]["pickup"]
            if "dropoff" in order_map[req_id]:
                rp.dropoff_order = order_map[req_id]["dropoff"]

    # 3. Calculate road polyline
    route_waypoints = [(driver_lat, driver_lng)] + [(s["lat"], s["lng"]) for s in ordered_stops]
    route_data = await fetch_road_route(route_waypoints)

    # Ensure route origin connects seamlessly to driver's exact coordinates
    if route_data.get("polyline") and len(route_data["polyline"]) > 0:
        poly = route_data["polyline"]
        if abs(poly[0][0] - driver_lat) > 1e-6 or abs(poly[0][1] - driver_lng) > 1e-6:
            poly.insert(0, [driver_lat, driver_lng])

    # 4. Save polyline to rides table
    ride.route_polyline = json.dumps(route_data["polyline"])
    await db.commit()

    # 5. Clean up serialized stops (remove private fields)
    clean_stops = []
    for s in ordered_stops:
        clean_stops.append({
            "type": s["type"],
            "request_id": s["request_id"],
            "passenger_name": s["passenger_name"],
            "name": s["name"],
            "lat": s["lat"],
            "lng": s["lng"],
            "order": s["order"]
        })

    result_payload = {
        "ride_id": str(ride.id),
        "stops": clean_stops,
        "route_polyline": route_data["polyline"],
        "distance_meters": route_data["distance_meters"],
        "duration_seconds": route_data["duration_seconds"],
        "next_stop": clean_stops[0] if clean_stops else None
    }

    # 6. Broadcast route_updated to driver
    await manager.send_to_user(
        str(rick.driver_id),
        {
            "type": "route_updated",
            "data": result_payload
        }
    )

    # 7. Broadcast route_updated to all active passengers in this ride
    for p in passengers:
        p_id = p.get("passenger_id")
        if p_id:
            await manager.send_to_user(
                p_id,
                {
                    "type": "route_updated",
                    "data": {
                        "ride_id": str(ride.id),
                        "driver_lat": driver_lat,
                        "driver_lng": driver_lng,
                        "passenger_status": p["status"],
                        "route_polyline": route_data["polyline"],
                        "distance_meters": route_data["distance_meters"],
                        "duration_seconds": route_data["duration_seconds"],
                        "next_stop": clean_stops[0] if clean_stops else None
                    }
                }
            )

    return result_payload
