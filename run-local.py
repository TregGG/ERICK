"""Start the FastAPI backend (API, WebSocket, dispatch loop and all three web apps).

Usage: .venv313\Scripts\python run-local.py
Host and port come from backend/.env (HOST, PORT; default 127.0.0.1:8091).
"""
import uvicorn

from backend.config import settings

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, log_level="info",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
