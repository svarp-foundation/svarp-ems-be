"""
Background task runner for exam portal integrity and monitoring.
Sweeps:
1. Expired attempts -> AUTO_SUBMITTED (server-side deadline enforcement)
2. Stale heartbeats -> Locks disconnected attempts after threshold grace period
3. Expired sessions -> Revokes stale login sessions
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.attempt import Attempt, AttemptState
from app.models.exam_session import ExamSession
from app.utils.audit import log_audit

logger = logging.getLogger("portal_exam.background")
logger.setLevel(logging.INFO)

# Background monitor state for health inspection
MONITOR_STATS: Dict[str, Any] = {
    "is_running": False,
    "last_run_at": None,
    "cycles_completed": 0,
    "attempts_auto_submitted": 0,
    "attempts_locked_heartbeat": 0,
    "sessions_expired": 0,
    "last_error": None,
}


def sweep_expired_attempts(db: Session) -> int:
    """
    Find IN_PROGRESS attempts whose deadline has passed and auto-submit them.
    Ensures deadlines are strictly enforced even if student closes browser.
    """
    now = datetime.now(timezone.utc)
    expired_attempts = (
        db.query(Attempt)
        .filter(
            Attempt.state == AttemptState.IN_PROGRESS,
            Attempt.deadline <= now,
        )
        .all()
    )

    count = len(expired_attempts)
    for attempt in expired_attempts:
        attempt.state = AttemptState.AUTO_SUBMITTED
        attempt.submitted_at = now
        log_audit(
            db=db,
            action="AUTO_SUBMIT_DEADLINE_EXPIRED",
            actor_id=attempt.student_id,
            target_type="attempt",
            target_id=attempt.id,
            details={
                "deadline": attempt.deadline.isoformat(),
                "submitted_at": now.isoformat(),
                "reason": "background_deadline_sweep",
            },
            auto_commit=False,
        )

    if count > 0:
        db.commit()
        logger.info(f"Background sweep: Auto-submitted {count} expired exam attempt(s).")
    return count


def sweep_stale_heartbeats(db: Session) -> int:
    """
    Find active attempts whose heartbeat has dropped past the grace threshold.
    Locks the attempt to prevent unauthorized offline tampering.
    """
    now = datetime.now(timezone.utc)
    # Grace period: 2x the configured heartbeat timeout (e.g. 120s if timeout is 60s)
    threshold = timedelta(seconds=max(30, settings.HEARTBEAT_TIMEOUT_SECONDS * 2))

    stale_cutoff = now - threshold
    stale_attempts = (
        db.query(Attempt)
        .filter(
            Attempt.state == AttemptState.IN_PROGRESS,
            Attempt.is_locked == False,
            Attempt.last_heartbeat != None,
            Attempt.last_heartbeat < stale_cutoff,
        )
        .all()
    )

    count = len(stale_attempts)
    for attempt in stale_attempts:
        attempt.is_locked = True
        attempt.state = AttemptState.INTERRUPTED
        attempt.lock_reason = "Connectivity Timeout: Heartbeat disconnected beyond threshold"
        log_audit(
            db=db,
            action="ATTEMPT_HEARTBEAT_LOCK",
            actor_id=attempt.student_id,
            target_type="attempt",
            target_id=attempt.id,
            details={
                "last_heartbeat": attempt.last_heartbeat.isoformat() if attempt.last_heartbeat else None,
                "locked_at": now.isoformat(),
                "reason": "background_heartbeat_sweep",
            },
            auto_commit=False,
        )

    if count > 0:
        db.commit()
        logger.info(f"Background sweep: Locked {count} attempt(s) due to lost heartbeat.")
    return count


def sweep_stale_sessions(db: Session) -> int:
    """
    Revoke user sessions that exceeded SESSION_EXPIRY_MINUTES lifetime.
    """
    now = datetime.now(timezone.utc)
    expiry = timedelta(minutes=settings.SESSION_EXPIRY_MINUTES)
    cutoff = now - expiry

    stale_sessions = (
        db.query(ExamSession)
        .filter(
            ExamSession.is_active == True,
            ExamSession.created_at < cutoff,
        )
        .all()
    )

    count = len(stale_sessions)
    for session in stale_sessions:
        session.is_active = False
        session.revoked_at = now
        session.revoked_reason = "session_expired"

    if count > 0:
        db.commit()
        logger.info(f"Background sweep: Expired {count} stale session(s).")
    return count


def run_monitoring_cycle() -> Dict[str, int]:
    """Execute one full background maintenance cycle."""
    db: Session = SessionLocal()
    try:
        auto_sub_count = sweep_expired_attempts(db)
        hb_lock_count = sweep_stale_heartbeats(db)
        session_exp_count = sweep_stale_sessions(db)

        MONITOR_STATS["cycles_completed"] += 1
        MONITOR_STATS["last_run_at"] = datetime.now(timezone.utc).isoformat()
        MONITOR_STATS["attempts_auto_submitted"] += auto_sub_count
        MONITOR_STATS["attempts_locked_heartbeat"] += hb_lock_count
        MONITOR_STATS["sessions_expired"] += session_exp_count
        MONITOR_STATS["last_error"] = None

        return {
            "auto_submitted": auto_sub_count,
            "locked_heartbeat": hb_lock_count,
            "sessions_expired": session_exp_count,
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Error during background monitoring cycle: {exc}", exc_info=True)
        MONITOR_STATS["last_error"] = str(exc)
        return {"auto_submitted": 0, "locked_heartbeat": 0, "sessions_expired": 0}
    finally:
        db.close()


async def background_monitor_loop(interval_seconds: int = 15):
    """
    Asynchronous runner loop for periodic background checks.
    Runs every 15 seconds.
    """
    MONITOR_STATS["is_running"] = True
    logger.info(f"Started exam background monitor (interval: {interval_seconds}s)")
    try:
        while True:
            try:
                await asyncio.to_thread(run_monitoring_cycle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Error executing background cycle: {exc}", exc_info=True)
                MONITOR_STATS["last_error"] = str(exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Exam background monitor received shutdown signal.")
        MONITOR_STATS["is_running"] = False
        raise
    except Exception as exc:
        logger.error(f"Fatal crash in background monitor loop: {exc}", exc_info=True)
        MONITOR_STATS["is_running"] = False
        MONITOR_STATS["last_error"] = str(exc)
