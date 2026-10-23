from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .api.auth_routes import router as auth_router
from .api.passenger_routes import router as passenger_router
from .api.driver_routes import router as driver_router
from .api.location_routes import router as location_router
from .websocket.manager import manager
from .websocket.handlers import handle_message
from .auth.jwt import verify_token
from .database import create_all_tables
from .config import settings

app = FastAPI(title="Campus E-Rickshaw Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from pathlib import Path
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
PASSENGER_DIR = BASE_DIR / "passenger-app"
DRIVER_DIR = BASE_DIR / "driver-app"

app.include_router(auth_router)
app.include_router(passenger_router)
app.include_router(driver_router)
app.include_router(location_router)

# Mount frontend apps
if PASSENGER_DIR.exists():
    app.mount("/passenger", StaticFiles(directory=str(PASSENGER_DIR), html=True), name="passenger")
if DRIVER_DIR.exists():
    app.mount("/driver", StaticFiles(directory=str(DRIVER_DIR), html=True), name="driver")

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Campus E-Rickshaw System</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: system-ui, -apple-system, sans-serif; background: #f0f2f5; margin: 0; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; }
            .card { background: white; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); padding: 24px; max-width: 480px; width: 100%; text-align: center; margin-bottom: 20px; }
            h1 { color: #1e293b; margin-top: 0; font-size: 24px; }
            p { color: #64748b; font-size: 15px; }
            .links { display: flex; flex-direction: column; gap: 12px; margin-top: 24px; }
            .btn { display: block; padding: 14px 20px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px; transition: transform 0.1s; }
            .btn-green { background: #2E7D32; color: white; }
            .btn-blue { background: #1565C0; color: white; }
            .btn-gray { background: #475569; color: white; }
            .btn:hover { opacity: 0.95; transform: translateY(-1px); }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🛺 Campus E-Rickshaw</h1>
            <p>Shared Ride Matching and Real-time Tracking System</p>
            <div class="links">
                <a href="/passenger/" class="btn btn-green">👤 Open Passenger App</a>
                <a href="/driver/" class="btn btn-blue">🚗 Open Driver App</a>
                <a href="/docs" class="btn btn-gray">📚 API Documentation (Swagger)</a>
            </div>
        </div>
    </body>
    </html>
    """

@app.on_event("startup")
async def startup_event():
    print(f"Starting Campus E-Rickshaw Backend. DB URL: {settings.DATABASE_URL}")
    await create_all_tables()
    print("Database tables created.")

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
