from fastapi import WebSocket
from typing import Dict, List

class ConnectionManager:
    def __init__(self):
        # Maps user_id -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # Could also maintain role-based lists if needed

    async def connect(self, websocket: WebSocket, user_id: str, role: str):
        await websocket.accept()
        # Handle reconnection
        if user_id in self.active_connections:
            await self.active_connections[user_id].close()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_json(message)

    async def broadcast_to_all(self, message: dict):
        for connection in self.active_connections.values():
            await connection.send_json(message)

manager = ConnectionManager()
