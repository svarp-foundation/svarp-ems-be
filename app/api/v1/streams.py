"""Stream management endpoints."""

from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.middleware.auth import get_current_user, RoleChecker
from app.schemas.auth import UserInfo
from app.schemas.stream import StreamCreate, StreamUpdate, StreamResponse
from app.services.stream_service import stream_service
from app.utils.audit import log_audit

router = APIRouter()

# Role dependencies
require_admin = RoleChecker(["admin", "Super Admin", "Exam Administrator"])


@router.get("", response_model=List[StreamResponse], include_in_schema=False)
@router.get("/", response_model=List[StreamResponse])
def list_streams(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """List all streams. Any authenticated user can view."""
    return stream_service.get_streams(db, active_only=active_only)


@router.post("", response_model=StreamResponse, include_in_schema=False)
@router.post("/", response_model=StreamResponse)
def create_stream(
    data: StreamCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Create a new stream. Requires admin role."""
    try:
        stream = stream_service.create_stream(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_audit(
        db=db,
        action="CREATE_STREAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="stream",
        target_id=stream.id,
        details={"name": stream.name, "code": stream.code},
        ip_address=request.client.host if request.client else None,
    )

    return stream


@router.get("/{stream_id}", response_model=StreamResponse)
def get_stream(
    stream_id: UUID,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Get a stream by ID."""
    stream = stream_service.get_stream(db, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    return stream


@router.patch("/{stream_id}", response_model=StreamResponse)
def update_stream(
    stream_id: UUID,
    data: StreamUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Update a stream. Requires admin role."""
    try:
        stream = stream_service.update_stream(db, stream_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    log_audit(
        db=db,
        action="UPDATE_STREAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="stream",
        target_id=stream.id,
        details=data.model_dump(exclude_unset=True),
        ip_address=request.client.host if request.client else None,
    )

    return stream


@router.delete("/{stream_id}")
def delete_stream(
    stream_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_admin),
):
    """Soft-delete a stream. Requires admin role."""
    success = stream_service.delete_stream(db, stream_id)
    if not success:
        raise HTTPException(status_code=404, detail="Stream not found")

    log_audit(
        db=db,
        action="DELETE_STREAM",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        target_type="stream",
        target_id=stream_id,
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Stream deleted successfully"}
