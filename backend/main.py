"""FastAPI application: REST API, WebSocket channel, dispatch loop and the three web apps."""
import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from .api import admin, auth, driver, passenger, public, ws
from .config import REPO_DIR, settings
from .db import SessionLocal, engine
from .services import dispatch, routing
from .services.geo import load_campus
from .services.rides import ServiceError

# Windows can map .js to text/plain via the registry, which breaks ES module loading.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("erick")

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://unpkg.com",
    "img-src 'self' data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://unpkg.com",
    "connect-src 'self' ws: wss:",
    "font-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_runtime()
    async with SessionLocal() as db:
        await load_campus(db)
    stop = asyncio.Event()
    task = None
    if settings.DISPATCH_ENABLED:
        task = asyncio.create_task(dispatch.run_forever(stop))
        public.dispatch_state["running"] = True
    log.info("Campus Rick backend ready (%s, %s)", settings.APP_ENV, "sqlite" if settings.is_sqlite else "postgresql")
    try:
        yield
    finally:
        stop.set()
        if task:
            await task
        public.dispatch_state["running"] = False
        await routing.close()
        await engine.dispose()


app = FastAPI(title="IIT Roorkee Campus Rick API", version="2.0.0", lifespan=lifespan,
              docs_url=None if settings.is_production else "/docs", redoc_url=None)

if settings.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # OpenStreetMap's tile usage policy requires a Referer; send only the origin to other sites.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
    if not request.url.path.startswith("/docs"):
        response.headers.setdefault("Content-Security-Policy", CSP)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif not settings.is_production:
        response.headers.setdefault("Cache-Control", "no-cache")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.exception_handler(ServiceError)
async def service_error(_: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code, **exc.extra})


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError):
    log.warning("integrity conflict: %s", exc.orig)
    return JSONResponse(status_code=409, content={"detail": "This change conflicts with the latest data. Refresh and try again.",
                                                  "code": "conflict"})


for module in (auth, public, passenger, driver, admin, ws):
    app.include_router(module.router)

for mount, folder in (("/passenger", "passenger-app"), ("/driver", "driver-app"), ("/admin", "admin-app"), ("/shared", "shared")):
    path = REPO_DIR / folder
    if path.exists():
        app.mount(mount, StaticFiles(directory=path, html=True), name=folder)


@app.get("/", include_in_schema=False)
async def landing():
    return FileResponse(REPO_DIR / "shared" / "index.html")
