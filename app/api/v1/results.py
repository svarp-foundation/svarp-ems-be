"""Exam scoring, results, publication, and analytics endpoints."""

from uuid import UUID
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.middleware.auth import get_current_user, RoleChecker
from app.schemas.auth import UserInfo
from app.schemas.result import (
    CandidateResultResponse,
    PublicationConfigRequest,
    PublicationConfigResponse,
    ExamAnalyticsResponse,
)
from app.services.result_service import result_service
from app.utils.audit import log_audit

router = APIRouter()

require_admin = RoleChecker(["admin", "Super Admin", "Exam Administrator"])


@router.get("/exam/{exam_id}", response_model=List[CandidateResultResponse])
def get_exam_results(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Admin view of all ranked candidate results for an exam."""
    try:
        return result_service.get_exam_results(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/exam/{exam_id}/analytics", response_model=ExamAnalyticsResponse)
def get_exam_analytics(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Aggregate statistics, pass rates, stream performance, and score distribution."""
    try:
        return result_service.get_exam_analytics(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/exam/{exam_id}/export")
def export_exam_results(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Download exam results as CSV with stream-wise columns."""
    try:
        csv_data = result_service.generate_csv_export(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=exam_{exam_id}_results.csv"},
    )


@router.get("/exam/{exam_id}/export-excel")
def export_exam_results_excel(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Download exam results as professional Excel (.xlsx) workbook with stream-wise columns."""
    try:
        excel_bytes = result_service.generate_excel_export(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=exam_{exam_id}_results.xlsx"},
    )


@router.post("/score-exam/{exam_id}")
def trigger_scoring(
    exam_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Calculate scores for all submitted exam attempts."""
    try:
        scored = result_service.score_exam(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        db=db,
        action="SCORE_EXAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=str(exam_id),
        details={"scored_attempts": scored},
        ip_address=client_ip,
    )
    return {"success": True, "scored_attempts": scored}


@router.get("/publication/{exam_id}", response_model=PublicationConfigResponse)
def get_publication_status(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Fetch current result publication controls for an exam."""
    return result_service.get_or_create_publication(db, exam_id)


@router.post("/publication/{exam_id}", response_model=PublicationConfigResponse)
def update_publication_status(
    exam_id: UUID,
    config: PublicationConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Control when and how results, answers, and explanations are released."""
    try:
        pub = result_service.update_publication(
            db=db,
            exam_id=exam_id,
            config=config,
            published_by_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        db=db,
        action="UPDATE_RESULT_PUBLICATION",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="result_publication",
        target_id=str(exam_id),
        details=config.model_dump(),
        ip_address=client_ip,
    )
    return pub


@router.get("/candidate/{exam_id}/{student_id}", response_model=CandidateResultResponse)
def get_admin_candidate_result(
    exam_id: UUID,
    student_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Admin inspects individual candidate's paper, responses, and score breakdown."""
    try:
        return result_service.get_candidate_result(
            db=db,
            exam_id=exam_id,
            student_id=student_id,
            is_admin=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/my-result/{exam_id}", response_model=CandidateResultResponse)
def get_my_result(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Student views own result.
    Enforces publication gatekeeper (returns 403 if results are unreleased).
    """
    try:
        return result_service.get_candidate_result(
            db=db,
            exam_id=exam_id,
            student_id=current_user.user_id,
            is_admin=False,
        )
    except ValueError as e:
        msg = str(e)
        if "not been published" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
