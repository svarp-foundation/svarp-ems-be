"""
Authentication dependencies for FastAPI route protection.
Validates JWT via portal-user AND checks exam session is active.
"""

from typing import List
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.portal_user_client import portal_user_client
from app.services.session_service import session_service
from app.schemas.auth import UserInfo


def _extract_token(request: Request) -> str:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    return auth_header.split(" ", 1)[1]


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> UserInfo:
    """
    Dependency that:
    1. Extracts JWT from Authorization header
    2. Validates JWT via portal-user /external/validate-token
    3. Checks our exam_sessions table that this session is active
    4. Updates heartbeat
    5. Returns user info
    """
    token = _extract_token(request)

    # Step 1: Validate token with portal-user
    token_info = portal_user_client.validate_token(token)
    if not token_info or not token_info.get("is_valid"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = token_info.get("user_id")
    session_id = token_info.get("session_id")  # This is the JTI

    if not user_id or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Step 2: Check exam session is active in our DB
    exam_session = session_service.validate_session(db, session_id)
    if not exam_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalidated or not found in exam portal",
        )

    # Step 3: Update heartbeat
    session_service.update_heartbeat(db, session_id)

    # Step 4: Get user details from portal-user
    user_data = portal_user_client.get_user(user_id=user_id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return UserInfo(
        user_id=user_data["user_id"],
        email=user_data["email"],
        full_name=user_data.get("full_name"),
        is_active=user_data["is_active"],
        roles=user_data.get("roles", []),
    )


class RoleChecker:
    """Dependency that checks if the current user has one of the allowed roles."""

    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
        user_roles_normalized = [str(r).lower().strip() for r in (current_user.roles or [])]
        allowed_roles_normalized = [str(r).lower().strip() for r in self.allowed_roles]

        for role in user_roles_normalized:
            if role in allowed_roles_normalized:
                return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions. Required roles: {self.allowed_roles}",
        )
