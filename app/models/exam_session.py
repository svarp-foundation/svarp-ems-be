import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from app.models.base import BaseModelMixin


class ExamSession(Base, BaseModelMixin):
    """
    Tracks active sessions for the exam portal.
    Single active session per user is enforced via a unique partial index.
    """

    __tablename__ = "exam_sessions"

    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_email = Column(String, nullable=False)
    token_jti = Column(String, unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    ip_address = Column(String)
    user_agent = Column(String)
    last_heartbeat = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    revoked_at = Column(DateTime(timezone=True))
    revoked_reason = Column(String)  # 'logout', 'new_login', 'admin', 'timeout'

    __table_args__ = (
        # CRITICAL: Only one active session per user
        Index(
            "uq_active_session_per_user",
            "user_id",
            unique=True,
            postgresql_where=(is_active == True),
        ),
    )
