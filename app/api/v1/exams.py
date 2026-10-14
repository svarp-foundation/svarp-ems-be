"""Exam builder, scheduling, quota validation, and enrollment endpoints."""

from uuid import UUID
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.middleware.auth import get_current_user, RoleChecker
from app.models.exam import ExamStatus
from app.schemas.auth import UserInfo
from app.schemas.exam import (
    ExamCreate,
    ExamUpdate,
    ExamResponse,
    ExamValidationReport,
    EnrollmentBatchCreate,
    EnrollmentResponse,
    EnrollmentFileValidationResponse,
    PreviewPaperResponse,
)
from app.services.exam_service import exam_service
from app.utils.audit import log_audit

router = APIRouter()

require_admin = RoleChecker(["admin", "Super Admin", "Exam Administrator"])


@router.get("", response_model=List[ExamResponse], include_in_schema=False)
@router.get("/", response_model=List[ExamResponse])
def list_exams(
    status: Optional[ExamStatus] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """List exams with filtering and enrolled student counts."""
    items, _ = exam_service.get_exams(db, status=status, search=search, skip=skip, limit=limit)
    return items


@router.post("", response_model=ExamResponse, include_in_schema=False)
@router.post("/", response_model=ExamResponse)
def create_exam(
    data: ExamCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Create a new exam in DRAFT status."""
    try:
        exam = exam_service.create_exam(db, data, created_by_id=current_user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_audit(
        db=db,
        action="CREATE_EXAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=exam["id"],
        details={"title": data.title, "total_questions": data.total_questions},
        ip_address=request.client.host if request and request.client else None,
    )

    return exam


@router.get("/{exam_id}", response_model=ExamResponse)
def get_exam(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Retrieve exam details, stream rules, quotas, and timing."""
    exam = exam_service.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.patch("/{exam_id}", response_model=ExamResponse)
def update_exam(
    exam_id: UUID,
    data: ExamUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Update exam configuration. Blocked if exam is published or in progress."""
    try:
        updated = exam_service.update_exam(db, exam_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="Exam not found")

    log_audit(
        db=db,
        action="UPDATE_EXAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=exam_id,
        ip_address=request.client.host if request and request.client else None,
    )

    return updated


@router.delete("/{exam_id}")
def delete_exam(
    exam_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Soft delete draft exam."""
    try:
        deleted = exam_service.delete_exam(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="Exam not found")

    log_audit(
        db=db,
        action="DELETE_EXAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=exam_id,
        ip_address=request.client.host if request and request.client else None,
    )

    return {"message": "Exam deleted successfully"}


@router.post("/{exam_id}/validate", response_model=ExamValidationReport)
def validate_exam_quotas(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """
    Validate question availability across all stream quotas against the Question Bank.
    If valid, advances exam status to VALIDATED.
    """
    try:
        report = exam_service.validate_exam_quotas(db, exam_id)
        return report
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{exam_id}/publish", response_model=ExamResponse)
def publish_exam(
    exam_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """
    Publish exam and lock configuration.
    Strict gatekeeper: automatically revalidates question bank availability before publishing.
    """
    try:
        published = exam_service.publish_exam(db, exam_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_audit(
        db=db,
        action="PUBLISH_EXAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=exam_id,
        ip_address=request.client.host if request and request.client else None,
    )

    return published


@router.get("/{exam_id}/enrollments", response_model=List[EnrollmentResponse])
def get_enrollments(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """List enrolled students for an exam."""
    enrollments = exam_service.get_enrollments(db, exam_id)
    return enrollments


@router.get("/enroll/template")
@router.get("/{exam_id}/enroll/template")
def download_candidate_template(
    current_user: UserInfo = Depends(require_admin),
):
    """Download a CSV sample template for candidate batch enrollment."""
    csv_content = "email\nstudent1@svarp.org\nstudent2@svarp.org\nintern.candidate@svarp.org\n"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=candidate_enrollment_template.csv"},
    )


@router.post("/{exam_id}/enroll/validate-file", response_model=EnrollmentFileValidationResponse)
async def validate_candidate_enrollment_file(
    exam_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Validate candidates in an uploaded CSV or Excel file against SVARP User Portal before enrollment."""
    filename = file.filename or ""
    if not (filename.lower().endswith(".csv") or filename.lower().endswith(".xlsx") or filename.lower().endswith(".xls")):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload a CSV (.csv) or Excel (.xlsx, .xls) file.",
        )
    file_bytes = await file.read()
    try:
        report = exam_service.validate_candidate_file(db, exam_id, file_bytes, filename)
        return report
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process candidate file: {str(e)}")


@router.post("/{exam_id}/enroll")
def enroll_students(
    exam_id: UUID,
    batch: EnrollmentBatchCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Enroll candidates to an exam."""
    student_tuples = [(s.student_id, s.student_email) for s in batch.students]
    try:
        count = exam_service.enroll_students(db, exam_id, student_tuples)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_audit(
        db=db,
        action="ENROLL_STUDENTS",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=exam_id,
        details={"enrolled_count": count},
        ip_address=request.client.host if request and request.client else None,
    )

    return {"message": f"Successfully enrolled {count} candidate(s)"}


@router.delete("/{exam_id}/enroll/{student_id}")
def withdraw_student(
    exam_id: UUID,
    student_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Withdraw student enrollment from an exam."""
    success = exam_service.withdraw_student(db, exam_id, student_id)
    if not success:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    log_audit(
        db=db,
        action="WITHDRAW_STUDENT",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="exam",
        target_id=exam_id,
        details={"student_id": str(student_id)},
        ip_address=request.client.host if request and request.client else None,
    )

    return {"message": "Candidate enrollment withdrawn"}


@router.post("/{exam_id}/preview-paper", response_model=PreviewPaperResponse)
def preview_paper(
    exam_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Simulate candidate paper allocation to inspect questions and quotas."""
    try:
        preview = exam_service.preview_paper(db, exam_id)
        return preview
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
