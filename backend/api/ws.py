"""Authenticated WebSocket channel.

Protocol (JSON messages):
  client -> {"type": "auth", "token": "<access token>"}        must be the first message, within 10 s
  server -> {"type": "ready", ...} then {"type": "sync", "data": <full current state>}
  client -> {"type": "location", "id": n, "data": {...}}        drivers only; answered by location.ack / location.rejected
  client -> {"type": "sync"} | {"type": "ping"}
The token travels in the first message rather than the URL so it never lands in access logs.
Close codes: 4401 unauthenticated/expired, 4403 forbidden/suspended, 4408 too many messages, 4400 protocol error.
"""
import asyncio
import logging
import time
from collections import deque

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..db import SessionLocal, utcnow
from ..models import AuthSession
from ..realtime import commit_and_publish, discard_events, manager
from ..security import AuthError, Principal, resolve_token
from ..services import locations
from ..services.rides import ServiceError
from .driver import driver_state
from .passenger import passenger_state

router = APIRouter()
log = logging.getLogger("erick.ws")
AUTH_TIMEOUT_S = 10
REVALIDATE_EVERY_S = 30
MAX_MESSAGES, MESSAGE_WINDOW_S = 30, 10


async def _snapshot(principal: Principal) -> dict:
    async with SessionLocal() as db:
        user = await db.get(type(principal.user), principal.id)
        fresh = Principal(user=user, session_id=principal.session_id)
        if user.role == "passenger":
            return {"role": "passenger", **(await passenger_state(db, fresh))}
        if user.role == "driver":
            return {"role": "driver", **(await driver_state(db, fresh))}
        return {"role": user.role, "server_time": utcnow().isoformat()}


async def _authenticate(websocket: WebSocket):
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_S)
    except (asyncio.TimeoutError, ValueError, KeyError):
        await websocket.close(code=4401, reason="Authenticate first")
        return None, None
    if not isinstance(first, dict) or first.get("type") != "auth" or not isinstance(first.get("token"), str):
        await websocket.close(code=4401, reason="Authenticate first")
        return None, None
    async with SessionLocal() as db:
        try:
            principal = await resolve_token(first["token"], db)
        except AuthError as exc:
            await websocket.close(code=exc.code, reason=str(exc)[:120])
            return None, None
        session = await db.get(AuthSession, principal.session_id)
        return principal, session.expires_at


async def _watch_session(websocket: WebSocket, principal: Principal, expires_at) -> None:
    """Close the socket when the session expires, is revoked, or the account is suspended."""
    while True:
        remaining = (expires_at - utcnow()).total_seconds()
        await asyncio.sleep(max(0.0, min(REVALIDATE_EVERY_S, remaining)))
        if utcnow() >= expires_at:
            await websocket.close(code=4401, reason="Session expired")
            return
        async with SessionLocal() as db:
            session = await db.get(AuthSession, principal.session_id)
            user = await db.get(type(principal.user), principal.id)
            if session is None or session.revoked_at is not None:
                await websocket.close(code=4401, reason="Signed out")
                return
            if user is None or user.status != "active":
                await websocket.close(code=4403, reason="Account suspended")
                return


async def _handle_location(principal: Principal, message: dict) -> dict:
    try:
        report = locations.LocationReport.model_validate(message.get("data") or {})
    except ValidationError as exc:
        return {"type": "location.rejected", "id": message.get("id"),
                "data": {"code": "invalid", "detail": exc.errors(include_url=False, include_input=False)[0]["msg"]}}
    async with SessionLocal() as db:
        try:
            user = await db.get(type(principal.user), principal.id)
            ack = await locations.record(db, user, report)
            await commit_and_publish(db)
        except ServiceError as exc:
            await db.rollback()
            discard_events(db)
            return {"type": "location.rejected", "id": message.get("id"), "data": {"code": exc.code, "detail": exc.detail}}
    return {"type": "location.ack", "id": message.get("id"), "data": ack}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    principal, expires_at = await _authenticate(websocket)
    if principal is None:
        return
    manager.add(principal.id, websocket)
    watcher = asyncio.create_task(_watch_session(websocket, principal, expires_at))
    recent = deque()
    try:
        await websocket.send_json(manager.envelope("ready", {
            "user_id": str(principal.id), "role": principal.role, "session_expires_at": expires_at.isoformat()}))
        await websocket.send_json(manager.envelope("sync", await _snapshot(principal)))
        while True:
            message = await websocket.receive_json()
            now = time.monotonic()
            recent.append(now)
            while recent and now - recent[0] > MESSAGE_WINDOW_S:
                recent.popleft()
            if len(recent) > MAX_MESSAGES:
                await websocket.close(code=4408, reason="Too many messages")
                break
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "ping":
                await websocket.send_json(manager.envelope("pong", {"server_time": utcnow().isoformat()}))
            elif kind == "sync":
                await websocket.send_json(manager.envelope("sync", await _snapshot(principal)))
            elif kind == "location":
                if principal.role != "driver":
                    await websocket.send_json(manager.envelope("error", {"detail": "Only drivers share location"}))
                    continue
                reply = await _handle_location(principal, message)
                await websocket.send_json({**manager.envelope(reply["type"], reply["data"]), "id": reply["id"]})
            else:
                await websocket.send_json(manager.envelope("error", {"detail": "Unknown message type"}))
    except WebSocketDisconnect:
        pass
    except (ValueError, KeyError):
        try:
            await websocket.close(code=4400, reason="Malformed message")
        except RuntimeError:
            pass
    except RuntimeError:
        pass  # socket already closed by the session watcher
    finally:
        watcher.cancel()
        manager.remove(principal.id, websocket)
