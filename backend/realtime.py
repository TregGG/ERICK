"""In-process WebSocket fan-out.

Single-instance only: every connection lives in this process's memory. Running
more than one backend instance requires a shared pub/sub layer (e.g. Redis) so
an event produced on one instance reaches sockets held by another.

Events are queued on the database session (`queue_event`) and delivered by
`commit_and_publish` only after the transaction commits, so a client is never
told about a state change that did not persist.
"""
import asyncio
import itertools
import logging
import uuid
from collections import defaultdict
from typing import Dict, Set

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from .db import utcnow

log = logging.getLogger("erick.realtime")


class ConnectionManager:
    def __init__(self):
        self._sockets: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._seq = itertools.count(1)

    def add(self, user_id, ws: WebSocket) -> None:
        self._sockets[str(user_id)].add(ws)

    def remove(self, user_id, ws: WebSocket) -> None:
        sockets = self._sockets.get(str(user_id))
        if sockets:
            sockets.discard(ws)
            if not sockets:
                self._sockets.pop(str(user_id), None)

    def is_connected(self, user_id) -> bool:
        return bool(self._sockets.get(str(user_id)))

    def connection_count(self) -> int:
        return sum(len(s) for s in self._sockets.values())

    def envelope(self, event_type: str, data: dict) -> dict:
        return {"type": event_type, "data": data, "seq": next(self._seq), "sent_at": utcnow().isoformat()}

    async def send(self, user_id, event_type: str, data: dict) -> None:
        message = self.envelope(event_type, data)
        for ws in list(self._sockets.get(str(user_id), ())):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=5)
            except Exception:  # a dead socket must never break the caller's flow
                self.remove(user_id, ws)

    async def close_user(self, user_id, code: int, reason: str) -> None:
        for ws in list(self._sockets.get(str(user_id), ())):
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass
            self.remove(user_id, ws)


manager = ConnectionManager()


def queue_event(db: AsyncSession, user_id: uuid.UUID, event_type: str, data: dict) -> None:
    db.info.setdefault("outbox", []).append((user_id, event_type, data))


async def commit_and_publish(db: AsyncSession) -> None:
    await db.commit()
    await flush_events(db)


async def flush_events(db: AsyncSession) -> None:
    outbox = db.info.pop("outbox", [])
    for user_id, event_type, data in outbox:
        await manager.send(user_id, event_type, data)


def discard_events(db: AsyncSession) -> None:
    db.info.pop("outbox", None)
