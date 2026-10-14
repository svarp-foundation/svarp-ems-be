"""Audit logging utility."""

from typing import Optional, Any
from uuid import UUID
from sqlalchemy.orm import Session
from app.models.audit import AuditLog


def log_audit(
    db: Session,
    action: str,
    actor_id: Optional[UUID] = None,
    actor_email: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[UUID] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
    auto_commit: bool = True,
) -> AuditLog:
    """Create an audit log entry."""
    if target_id is not None and isinstance(target_id, str):
        try:
            target_id = UUID(target_id)
        except ValueError:
            target_id = None

    entry = AuditLog(
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)
    if auto_commit:
        db.commit()
        db.refresh(entry)
    else:
        db.flush()
    return entry
