from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
from app.models.base import BaseModelMixin


class AuditLog(Base, BaseModelMixin):
    """Append-only log of administrative and security-relevant actions."""

    __tablename__ = "audit_logs"

    actor_id = Column(UUID(as_uuid=True), index=True)  # from portal-user
    actor_email = Column(String)
    action = Column(String, nullable=False, index=True)
    target_type = Column(String, index=True)  # 'stream', 'exam', 'attempt', etc.
    target_id = Column(UUID(as_uuid=True))
    details = Column(JSONB)
    ip_address = Column(String)


class ExamEvent(Base, BaseModelMixin):
    """
    Security and monitoring events during an exam attempt.
    Tab switches, fullscreen exits, heartbeat losses, etc.
    """

    __tablename__ = "exam_events"

    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    # e.g., 'tab_switch', 'fullscreen_exit', 'heartbeat_loss', 'reconnect'
    details = Column(JSONB)
    ip_address = Column(String)
