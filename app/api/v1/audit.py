"""Security audit logs, proctoring violation event streams, and session telemetry endpoints."""

from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.middleware.auth import RoleChecker
from app.schemas.auth import UserInfo
from app.models.audit import AuditLog, ExamEvent
from app.models.exam_session import ExamSession
from app.schemas.audit import AuditLogResponse, ExamEventResponse, AuditStatsResponse

router = APIRouter()

require_admin = RoleChecker(["admin", "Super Admin", "Exam Administrator"])


@router.get("/logs", response_model=List[AuditLogResponse])
def get_audit_logs(
    action: Optional[str] = Query(None, description="Filter by action name"),
    actor_email: Optional[str] = Query(None, description="Filter by actor email"),
    target_type: Optional[str] = Query(None, description="Filter by target type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """List administrative and security audit trail logs with filtering."""
    query = db.query(AuditLog)
    if action and action.strip() and action.strip().lower() not in ["undefined", "null", "all"]:
        query = query.filter(AuditLog.action == action.strip())
    if actor_email and actor_email.strip() and actor_email.strip().lower() not in ["undefined", "null"]:
        query = query.filter(AuditLog.actor_email.ilike(f"%{actor_email.strip()}%"))
    if target_type and target_type.strip() and target_type.strip().lower() not in ["undefined", "null", "all"]:
        query = query.filter(AuditLog.target_type == target_type.strip())

    return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/events", response_model=List[ExamEventResponse])
def get_proctoring_events(
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    attempt_id: Optional[UUID] = Query(None, description="Filter by attempt ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """List proctoring telemetry events (tab switches, fullscreen exits, blur)."""
    query = db.query(ExamEvent)
    if event_type and event_type.strip() and event_type.strip().lower() not in ["undefined", "null", "all"]:
        query = query.filter(ExamEvent.event_type == event_type.strip())
    if attempt_id:
        query = query.filter(ExamEvent.attempt_id == attempt_id)

    return query.order_by(ExamEvent.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/stats", response_model=AuditStatsResponse)
def get_audit_stats(
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Aggregate statistics for security audit logs, proctoring events, and active sessions."""
    total_logs = db.query(func.count(AuditLog.id)).scalar() or 0
    total_events = db.query(func.count(ExamEvent.id)).scalar() or 0

    # Group actions
    action_rows = (
        db.query(AuditLog.action, func.count(AuditLog.id))
        .group_by(AuditLog.action)
        .all()
    )
    action_counts = {row[0]: row[1] for row in action_rows}

    # Group violations
    violation_rows = (
        db.query(ExamEvent.event_type, func.count(ExamEvent.id))
        .group_by(ExamEvent.event_type)
        .all()
    )
    violation_counts = {row[0]: row[1] for row in violation_rows}

    # Active single sessions
    active_sessions = (
        db.query(func.count(ExamSession.id))
        .filter(ExamSession.is_active == True)
        .scalar()
        or 0
    )

    return AuditStatsResponse(
        total_audit_logs=total_logs,
        total_proctoring_events=total_events,
        action_counts=action_counts,
        violation_counts=violation_counts,
        active_sessions_count=active_sessions,
    )
