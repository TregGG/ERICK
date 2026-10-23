from fastapi import WebSocket
from typing import Dict, List, Optional
import json

class ConnectionManager:
    def __init__(self):
        # Maps user_id (str) -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # Maps user_id (str) -> role (str)
        self.user_roles: Dict[str, str] = {}

    async def connect(self, websocket: WebSocket, user_id: str, role: str):
        await websocket.accept()
        # Handle reconnection
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].close()
            except Exception:
                pass
        self.active_connections[user_id] = websocket
        self.user_roles[user_id] = role

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_roles:
            del self.user_roles[user_id]

    async def send_to_user(self, user_id: str, message: dict) -> bool:
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].send_json(message)
                return True
            except Exception as e:
                print(f"Error sending message to user {user_id}: {e}")
                self.disconnect(user_id)
        return False

    async def broadcast_to_role(self, role: str, message: dict):
        for uid, r in list(self.user_roles.items()):
            if r == role and uid in self.active_connections:
                try:
                    await self.active_connections[uid].send_json(message)
                except Exception:
                    self.disconnect(uid)

    async def broadcast_to_all(self, message: dict):
        for uid, connection in list(self.active_connections.items()):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(uid)

manager = ConnectionManager()
