import json
from .manager import manager

async def handle_message(user_id: str, role: str, message_text: str):
    try:
        data = json.loads(message_text)
        action = data.get("action")
        
        if role == "driver" and action == "location_update":
            # Broadcast to relevant passengers
            pass
            
        elif action == "ping":
            await manager.send_to_user(user_id, {"action": "pong"})
            
    except json.JSONDecodeError:
        pass
