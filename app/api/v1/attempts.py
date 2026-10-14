"""Student examination attempt, answer autosave, proctoring, and resumption endpoints."""

from uuid import UUID
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.middleware.auth import get_current_user, RoleChecker
from app.schemas.auth import UserInfo
from app.schemas.attempt import (
    AvailableExamResponse,
    ExamInstructionResponse,
    AttemptStartResponse,
    SaveAnswerRequest,
    SaveAnswerResponse,
    HeartbeatResponse,
    LogEventRequest,
    ResumeRequestCreate,
    ResumeRequestResponse,
    AdminAttemptResponse,
)
from app.services.attempt_service import attempt_service
from app.utils.audit import log_audit

router = APIRouter()

require_admin = RoleChecker(["admin", "Super Admin", "Exam Administrator"])


@router.get("/available", response_model=List[AvailableExamResponse])
def get_available_exams(
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """List exams available for candidate enrollment / taking."""
    return attempt_service.get_available_exams(
        db=db,
        student_id=current_user.user_id,
        student_email=current_user.email,
    )


@router.get("/instructions/{exam_id}", response_model=ExamInstructionResponse)
def get_exam_instructions(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Fetch complete examination details, marking scheme, and stream breakdown for instruction page."""
    try:
        return attempt_service.get_exam_instructions(
            db=db,
            exam_id=exam_id,
            student_id=current_user.user_id,
            student_email=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/start/{exam_id}", response_model=AttemptStartResponse)
def start_or_resume_attempt(
    exam_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Start a new attempt or resume an active one for the given exam."""
    try:
        data = attempt_service.start_or_resume_attempt(
            db=db,
            exam_id=exam_id,
            student_id=current_user.user_id,
            student_email=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        db=db,
        action="START_OR_RESUME_ATTEMPT",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="attempt",
        target_id=str(data["attempt_id"]),
        details={"exam_id": str(exam_id)},
        ip_address=client_ip,
    )
    return data


@router.post("/{attempt_id}/save-answer", response_model=SaveAnswerResponse)
def save_answer(
    attempt_id: UUID,
    data: SaveAnswerRequest,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Idempotently save or clear an answer and review flag."""
    try:
        return attempt_service.save_answer(
            db=db,
            attempt_id=attempt_id,
            student_id=current_user.user_id,
            attempt_question_id=data.attempt_question_id,
            selected_option_id=data.selected_option_id,
            is_marked_for_review=data.is_marked_for_review,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{attempt_id}/heartbeat", response_model=HeartbeatResponse)
def send_heartbeat(
    attempt_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Update attempt heartbeat and check deadline/lock status."""
    try:
        return attempt_service.heartbeat(
            db=db,
            attempt_id=attempt_id,
            student_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{attempt_id}/log-event")
def log_event(
    attempt_id: UUID,
    data: LogEventRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Log proctoring events (tab switch, fullscreen exit, blur)."""
    client_ip = request.client.host if request.client else "unknown"
    try:
        return attempt_service.log_event(
            db=db,
            attempt_id=attempt_id,
            student_id=current_user.user_id,
            event_type=data.event_type,
            details=data.details,
            ip_address=client_ip,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{attempt_id}/submit")
def submit_attempt(
    attempt_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Submit the exam attempt upon candidate completion."""
    try:
        attempt_service.submit_attempt(
            db=db,
            attempt_id=attempt_id,
            student_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        db=db,
        action="SUBMIT_ATTEMPT",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="attempt",
        target_id=str(attempt_id),
        details={"status": "SUBMITTED"},
        ip_address=client_ip,
    )
    return {"success": True, "message": "Attempt submitted successfully"}


@router.post("/{attempt_id}/request-resume", response_model=ResumeRequestResponse)
def request_resume(
    attempt_id: UUID,
    data: ResumeRequestCreate,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Submit a resumption request when locked due to an interruption."""
    try:
        return attempt_service.request_resumption(
            db=db,
            attempt_id=attempt_id,
            student_id=current_user.user_id,
            reason=data.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/all", response_model=List[AdminAttemptResponse])
def get_admin_attempts(
    exam_id: Optional[UUID] = None,
    locked_only: bool = False,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """List attempts for administrative oversight and resumption decisions."""
    return attempt_service.get_admin_attempts(
        db=db,
        exam_id=exam_id,
        locked_only=locked_only,
    )


@router.post("/admin/resume/{attempt_id}")
def approve_resumption(
    attempt_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Approve resumption of an interrupted attempt without altering the original deadline."""
    try:
        attempt_service.approve_resumption(
            db=db,
            attempt_id=attempt_id,
            reviewer_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        db=db,
        action="APPROVE_RESUMPTION",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="attempt",
        target_id=str(attempt_id),
        details={"reviewer_id": str(current_user.user_id)},
        ip_address=client_ip,
    )
    return {"success": True, "message": "Resumption approved successfully"}


@router.post("/admin/reject-resume/{attempt_id}")
def reject_resumption(
    attempt_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Reject resumption of an interrupted attempt."""
    try:
        attempt_service.reject_resumption(
            db=db,
            attempt_id=attempt_id,
            reviewer_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        db=db,
        action="REJECT_RESUMPTION",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="attempt",
        target_id=str(attempt_id),
        details={"reviewer_id": str(current_user.user_id)},
        ip_address=client_ip,
    )
    return {"success": True, "message": "Resumption rejected successfully"}
