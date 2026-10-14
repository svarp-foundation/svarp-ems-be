"""
Exam session management — enforces single active session per user.
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.exam_session import ExamSession
from app.core.config import settings


class SessionService:

    def create_session(
        self,
        db: Session,
        user_id: uuid.UUID,
        user_email: str,
        token_jti: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        bypass_single_session: Optional[bool] = None,
    ) -> ExamSession:
        """
        Create an exam session. Enforces single active session per user
        via the unique partial index. If bypass is enabled, revokes existing
        sessions first so the user can log in without 409 conflict.
        """
        should_bypass = (
            bypass_single_session
            if bypass_single_session is not None
            else (settings.BYPASS_SINGLE_SESSION or settings.AUTO_REVOKE_EXISTING_SESSIONS)
        )

        if should_bypass:
            self.force_revoke_user_sessions(db, user_id, reason="new_login_bypass")

        exam_session = ExamSession(
            user_id=user_id,
            user_email=user_email,
            token_jti=token_jti,
            is_active=True,
            ip_address=ip_address,
            user_agent=user_agent,
            last_heartbeat=datetime.now(timezone.utc),
        )
        db.add(exam_session)
        try:
            db.commit()
            db.refresh(exam_session)
            return exam_session
        except IntegrityError:
            db.rollback()
            if should_bypass:
                # Retry once after clearing any remaining active sessions
                self.force_revoke_user_sessions(db, user_id, reason="new_login_bypass")
                exam_session_retry = ExamSession(
                    user_id=user_id,
                    user_email=user_email,
                    token_jti=token_jti,
                    is_active=True,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    last_heartbeat=datetime.now(timezone.utc),
                )
                db.add(exam_session_retry)
                db.commit()
                db.refresh(exam_session_retry)
                return exam_session_retry
            raise ValueError("User already has an active session")

    def get_active_session(
        self, db: Session, user_id: uuid.UUID
    ) -> Optional[ExamSession]:
        """Get the current active session for a user."""
        return (
            db.query(ExamSession)
            .filter(
                ExamSession.user_id == user_id,
                ExamSession.is_active == True,
            )
            .first()
        )

    def get_session_by_jti(
        self, db: Session, token_jti: str
    ) -> Optional[ExamSession]:
        """Look up a session by its JWT JTI."""
        return (
            db.query(ExamSession)
            .filter(ExamSession.token_jti == token_jti)
            .first()
        )

    def validate_session(self, db: Session, token_jti: str) -> Optional[ExamSession]:
        """
        Check if a session is active and not timed out.
        Returns the session if valid, None otherwise.
        """
        session = self.get_session_by_jti(db, token_jti)
        if not session or not session.is_active:
            return None

        now = datetime.now(timezone.utc)

        # Enforce maximum session lifetime expiry
        session_expiry = timedelta(minutes=settings.SESSION_EXPIRY_MINUTES)
        if (now - session.created_at) > session_expiry:
            session.is_active = False
            session.revoked_at = now
            session.revoked_reason = "session_expired"
            db.commit()
            return None

        return session

    def update_heartbeat(self, db: Session, token_jti: str, throttle_seconds: int = 20) -> bool:
        """Update the last_heartbeat timestamp for a session, throttled to prevent high-frequency write churn."""
        session = self.get_session_by_jti(db, token_jti)
        if not session or not session.is_active:
            return False

        now = datetime.now(timezone.utc)
        if session.last_heartbeat and throttle_seconds > 0:
            last = session.last_heartbeat if session.last_heartbeat.tzinfo else session.last_heartbeat.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < throttle_seconds:
                return True

        session.last_heartbeat = now
        db.commit()
        return True

    def revoke_session(
        self, db: Session, token_jti: str, reason: str = "logout"
    ) -> bool:
        """Revoke a session (logout, admin action, timeout, etc.)."""
        session = self.get_session_by_jti(db, token_jti)
        if not session or not session.is_active:
            return False

        session.is_active = False
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = reason
        db.commit()
        return True

    def force_revoke_user_sessions(
        self, db: Session, user_id: uuid.UUID, reason: str = "admin"
    ) -> int:
        """Revoke all active sessions for a user (admin action)."""
        now = datetime.now(timezone.utc)
        count = (
            db.query(ExamSession)
            .filter(
                ExamSession.user_id == user_id,
                ExamSession.is_active == True,
            )
            .update(
                {
                    "is_active": False,
                    "revoked_at": now,
                    "revoked_reason": reason,
                }
            )
        )
        db.commit()
        return count


session_service = SessionService()
