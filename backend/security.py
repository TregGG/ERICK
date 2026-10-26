"""Authentication, authorization and abuse protection.

* OTP codes are stored only as HMAC digests, expire, and lock after a few wrong attempts.
* Access tokens are short JWTs bound to a server-side session row, so logout and
  admin suspension take effect immediately.
* Every protected route resolves the caller from the database; client-supplied
  user or driver ids are never trusted for authorization.
"""
import hashlib
import hmac
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db, utcnow
from .models import AuthSession, User

ALGORITHM = "HS256"
_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str) -> str:
    """Return an E.164 phone number. Bare 10-digit numbers are treated as Indian (+91)."""
    cleaned = re.sub(r"[\s\-()]", "", raw or "")
    if re.fullmatch(r"\d{10}", cleaned):
        cleaned = "+91" + cleaned
    elif re.fullmatch(r"0\d{10}", cleaned):
        cleaned = "+91" + cleaned[1:]
    elif re.fullmatch(r"91\d{10}", cleaned):
        cleaned = "+" + cleaned
    if not _PHONE_RE.fullmatch(cleaned):
        raise ValueError("Enter a valid mobile number, e.g. 98765 43210")
    return cleaned


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}******{phone[-4:]}" if len(phone) > 7 else "***"


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(phone: str, code: str) -> str:
    return hmac.new(settings.JWT_SECRET.encode(), f"otp:{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def otp_matches(phone: str, code: str, digest: str) -> bool:
    return hmac.compare_digest(hash_otp(phone, code), digest)


def create_access_token(user: User, session: AuthSession) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "sid": str(session.id),
        "iat": int(session.created_at.timestamp()),
        "exp": int(session.expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def new_session(user: User, user_agent: Optional[str]) -> AuthSession:
    now = utcnow()
    return AuthSession(
        id=uuid.uuid4(), user_id=user.id, created_at=now,
        expires_at=now + timedelta(hours=settings.JWT_EXPIRY_HOURS),
        user_agent=(user_agent or "")[:200] or None,
    )


@dataclass
class Principal:
    user: User
    session_id: uuid.UUID

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role


class AuthError(Exception):
    def __init__(self, message: str, code: int = 4401):
        super().__init__(message)
        self.code = code


async def resolve_token(token: str, db: AsyncSession) -> Principal:
    """Validate a bearer token against its server-side session and the user's status."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM], options={"require": ["exp", "sub", "sid"]})
        user_id, session_id = uuid.UUID(payload["sub"]), uuid.UUID(payload["sid"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise AuthError("Your session is invalid. Please sign in again.")
    session = await db.get(AuthSession, session_id)
    if session is None or session.user_id != user_id or session.revoked_at is not None or session.expires_at <= utcnow():
        raise AuthError("Your session has expired. Please sign in again.")
    user = await db.get(User, user_id)
    if user is None:
        raise AuthError("Account not found.")
    if user.status != "active":
        raise AuthError("This account is suspended. Contact the campus transport office.", code=4403)
    return Principal(user=user, session_id=session_id)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


async def current_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    try:
        return await resolve_token(_bearer(request), db)
    except AuthError as exc:
        status_code = status.HTTP_403_FORBIDDEN if exc.code == 4403 else status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code, str(exc), headers={"WWW-Authenticate": "Bearer"})


def require_role(*roles: str):
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this action")
        return principal
    return dependency


require_passenger = require_role("passenger")
require_driver = require_role("driver")
require_admin = require_role("admin")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """In-process sliding-window limiter. Replace with Redis when running more than one instance."""

    def __init__(self):
        self._hits = defaultdict(deque)

    def check(self, bucket: str, key: str, limit: int, window_s: float) -> None:
        now = time.monotonic()
        hits = self._hits[(bucket, key)]
        while hits and now - hits[0] > window_s:
            hits.popleft()
        if len(hits) >= limit:
            retry = int(window_s - (now - hits[0])) + 1
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many attempts. Try again in {retry} seconds.",
                                headers={"Retry-After": str(retry)})
        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = RateLimiter()
