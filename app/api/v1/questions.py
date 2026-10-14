"""Question bank management and bulk import endpoints."""

from uuid import UUID
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.middleware.auth import get_current_user, RoleChecker
from app.models.question import DifficultyLevel, QuestionStatus
from app.schemas.auth import UserInfo
from app.schemas.question import (
    QuestionCreate,
    QuestionUpdate,
    QuestionResponse,
    QuestionListResponse,
    QuestionVersionResponse,
    ImportResultResponse,
)
from app.services.question_service import question_service
from app.utils.audit import log_audit

router = APIRouter()

require_admin = RoleChecker(["admin", "Super Admin", "Exam Administrator"])


@router.get("", response_model=QuestionListResponse, include_in_schema=False)
@router.get("/", response_model=QuestionListResponse)
def list_questions(
    stream_id: Optional[UUID] = None,
    difficulty: Optional[DifficultyLevel] = None,
    status: Optional[QuestionStatus] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """List questions with filters, search, and pagination."""
    items, total = question_service.get_questions(
        db=db,
        stream_id=stream_id,
        difficulty=difficulty,
        status=status,
        search=search,
        skip=skip,
        limit=limit,
    )
    return QuestionListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/stream-stats")
def get_stream_question_stats(
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Aggregated question counts and difficulty breakdowns by stream."""
    return question_service.get_stream_question_stats(db)


@router.get("/template")
def download_csv_template(current_user: UserInfo = Depends(get_current_user)):
    """Download a standardized CSV template for bulk question import."""
    csv_content = (
        "stream_code,difficulty,question_text,option_a,option_b,option_c,option_d,correct_option,explanation,tags\n"
        "CS,easy,Which data structure uses LIFO (Last In First Out)?,Queue,Stack,Array,Linked List,B,Stack follows Last In First Out order,data-structures;stack\n"
        "CS,medium,What is the worst-case time complexity of QuickSort?,O(n),O(n log n),O(n^2),O(log n),C,Worst-case occurs when the pivot divides poorly leading to O(n^2),algorithms;sorting\n"
        "MATH,easy,What is the derivative of sin(x) with respect to x?,cos(x),-cos(x),tan(x),sec^2(x),A,Standard trigonometric derivative rule,calculus;trigonometry\n"
    )
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="question_bank_template.csv"'},
    )


@router.post("/import", response_model=ImportResultResponse)
async def import_questions(
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """
    Bulk import questions from CSV or XLSX.
    Strict pre-flight check: if any row fails validation, no changes are committed.
    """
    if not (file.filename.endswith(".csv") or file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload a .csv or .xlsx file.",
        )

    file_bytes = await file.read()
    result = question_service.import_questions(db, file_bytes=file_bytes, filename=file.filename)

    if result.success:
        log_audit(
            db=db,
            action="BULK_IMPORT_QUESTIONS",
            actor_id=current_user.user_id,
            actor_email=current_user.email,
            target_type="question_bank",
            details={"filename": file.filename, "imported_count": result.imported_count},
            ip_address=request.client.host if request and request.client else None,
        )

    return result


@router.post("/", response_model=QuestionResponse)
def create_question(
    data: QuestionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Create a new MCQ with immutable version 1 and 4 options."""
    try:
        question = question_service.create_question(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_audit(
        db=db,
        action="CREATE_QUESTION",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="question",
        target_id=question["id"],
        details={"difficulty": data.difficulty, "stream_id": str(data.stream_id)},
        ip_address=request.client.host if request.client else None,
    )

    return question


@router.get("/{question_id}", response_model=QuestionResponse)
def get_question(
    question_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Retrieve question with current version and options."""
    question = question_service.get_question(db, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


@router.patch("/{question_id}", response_model=QuestionResponse)
def update_question(
    question_id: UUID,
    data: QuestionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """
    Update question.
    Modifying question text, explanation, or options automatically creates a new immutable version.
    """
    try:
        updated = question_service.update_question(db, question_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="Question not found")

    log_audit(
        db=db,
        action="UPDATE_QUESTION",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="question",
        target_id=question_id,
        details={"version": updated.get("total_versions")},
        ip_address=request.client.host if request.client else None,
    )

    return updated


@router.delete("/{question_id}")
def delete_question(
    question_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Soft delete question and mark it archived."""
    deleted = question_service.delete_question(db, question_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Question not found")

    log_audit(
        db=db,
        action="DELETE_QUESTION",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="question",
        target_id=question_id,
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Question archived successfully"}


@router.get("/{question_id}/versions", response_model=List[QuestionVersionResponse])
def get_question_versions(
    question_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Retrieve full immutable version history of a question."""
    versions = question_service.get_question_versions(db, question_id)
    return versions
