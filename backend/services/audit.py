from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import AuditLog


def audit(db: AsyncSession, actor, action: str, target_type: Optional[str] = None,
          target_id=None, details: Optional[dict] = None, ip: Optional[str] = None) -> None:
    """Record a privileged or security-relevant action. Never pass OTPs, tokens or precise locations."""
    db.add(AuditLog(
        actor_user_id=getattr(actor, "id", None), actor_role=getattr(actor, "role", "system"),
        action=action, target_type=target_type, target_id=str(target_id) if target_id is not None else None,
        details=details, ip=ip, created_at=utcnow(),
    ))
