import json
from .manager import manager
from ..database import async_session_factory
from ..models.rickshaw import Rickshaw
from ..models.ride import Ride, RideRequest, RidePassenger
from ..services.routing import update_ride_route
from sqlalchemy import select
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

async def handle_message(user_id: str, role: str, message_text: str):
    try:
        data = json.loads(message_text)
        msg_type = data.get("type") or data.get("action")
        payload = data.get("data", {})

        if role == "driver" and msg_type == "location_update":
            lat = payload.get("lat")
            lng = payload.get("lng")
            heading = payload.get("heading", 0)

            if lat is not None and lng is not None:
                async with async_session_factory() as db:
                    import uuid
                    try:
                        u_uuid = uuid.UUID(str(user_id))
                    except Exception:
                        u_uuid = user_id
                    rick_res = await db.execute(select(Rickshaw).filter(Rickshaw.driver_id == u_uuid))
                    rick = rick_res.scalars().first()
                    if rick:
                        rick.current_location = from_shape(Point(lng, lat), srid=4326)
                        rick.heading = heading
                        await db.commit()

                        # Check for active ride
                        ride_res = await db.execute(select(Ride).filter(Ride.rickshaw_id == rick.id, Ride.status == "active"))
                        active_ride = ride_res.scalars().first()
                        if active_ride:
                            # Recalculate route and stop sequence starting from new driver location
                            await update_ride_route(active_ride.id, lat, lng, db)

                            # Stream live location to active passengers
                            pass_res = await db.execute(
                                select(RideRequest.passenger_id)
                                .join(RidePassenger, RidePassenger.request_id == RideRequest.id)
                                .filter(RidePassenger.ride_id == active_ride.id, RidePassenger.status != "dropped_off")
                            )
                            for pass_id in pass_res.scalars().all():
                                await manager.send_to_user(
                                    str(pass_id),
                                    {
                                        "type": "driver_location",
                                        "data": {
                                            "lat": lat,
                                            "lng": lng,
                                            "heading": heading,
                                            "ride_id": str(active_ride.id)
                                        }
                                    }
                                )

        elif msg_type == "ping":
            await manager.send_to_user(user_id, {"type": "pong"})

    except Exception as e:
        print(f"Error handling WS message: {e}")

