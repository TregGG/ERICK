from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .api.auth_routes import router as auth_router
from .api.passenger_routes import router as passenger_router
from .api.driver_routes import router as driver_router
from .api.location_routes import router as location_router
from .websocket.manager import manager
from .websocket.handlers import handle_message
from .auth.jwt import verify_token
from .config import settings

app = FastAPI(title="Campus E-Rickshaw Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(passenger_router)
app.include_router(driver_router)
app.include_router(location_router)

@app.on_event("startup")
async def startup_event():
    print(f"Starting Campus E-Rickshaw Backend. DB URL: {settings.DATABASE_URL}")

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    try:
        payload = verify_token(token)
        user_id = payload["user_id"]
        role = payload["role"]
    except Exception:
        await websocket.close(code=1008)
        return
        
    await manager.connect(websocket, user_id, role)
    try:
        while True:
            data = await websocket.receive_text()
            await handle_message(user_id, role, data)
    except WebSocketDisconnect:
        manager.disconnect(user_id)
