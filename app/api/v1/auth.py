"""Authentication endpoints — proxies to portal-user, manages exam sessions."""

from uuid import UUID
import secrets
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.portal_user_client import portal_user_client
from app.services.session_service import session_service
from app.middleware.auth import get_current_user, _extract_token
from app.schemas.auth import LoginRequest, LoginResponse, UserInfo, SessionInfo
from app.core.config import settings
from app.utils.audit import log_audit
import httpx

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Login flow:
    1. Authenticate via portal-user /external/login
    2. Get user info from portal-user
    3. Check for existing active exam session → block if exists
    4. Create exam session in our DB
    5. Return JWT + user info
    """
    # Step 1: Authenticate with portal-user
    try:
        token_data = portal_user_client.login(data.email, data.password)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Authentication service error",
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Authentication service unavailable",
        )

    access_token = token_data["access_token"]

    # Step 2: Validate token to get JTI and user_id
    token_info = portal_user_client.validate_token(access_token)
    if not token_info or not token_info.get("is_valid"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Token validation failed",
        )

    user_id = token_info["user_id"]
    token_jti = token_info["session_id"]

    # Step 3: Get user details
    user_data = portal_user_client.get_user(user_id=user_id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch user details",
        )

    # Step 4: Create exam session (enforces single active session, or bypasses if configured)
    provided_bypass_key = (
        data.bypass_key.strip()
        if (data.bypass_key and data.bypass_key.strip())
        else (
            request.headers.get("X-Session-Bypass-Key", "").strip()
            or request.query_params.get("bypass_key", "").strip()
            or None
        )
    )

    if provided_bypass_key:
        configured_key = (settings.SESSION_BYPASS_KEY or "").strip()
        if not configured_key or not secrets.compare_digest(provided_bypass_key, configured_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid session bypass key. Please verify the key with your exam administrator.",
            )
        bypass_key_matched = True
    else:
        bypass_key_matched = False

    user_roles = user_data.get("roles") or []
    is_admin = any(r in ["admin", "Super Admin", "Exam Administrator"] for r in user_roles)

    should_bypass = bool(
        settings.BYPASS_SINGLE_SESSION
        or settings.AUTO_REVOKE_EXISTING_SESSIONS
        or bypass_key_matched
        or is_admin
    )

    try:
        exam_session = session_service.create_session(
            db=db,
            user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
            user_email=user_data["email"],
            token_jti=token_jti,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            bypass_single_session=should_bypass,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active session detected on another device or window. Single-session enforcement requires logging out of the existing session first.",
        )

    # Step 5: Audit log
    log_audit(
        db=db,
        action="LOGIN",
        actor_id=exam_session.user_id,
        actor_email=user_data["email"],
        target_type="exam_session",
        target_id=exam_session.id,
        ip_address=request.client.host if request.client else None,
    )

    return LoginResponse(
        access_token=access_token,
        user=UserInfo(
            user_id=user_data["user_id"],
            email=user_data["email"],
            full_name=user_data.get("full_name"),
            is_active=user_data["is_active"],
            roles=user_data.get("roles", []),
        ),
    )


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Logout flow:
    1. Revoke exam session in our DB
    2. Audit log
    """
    token = _extract_token(request)
    token_info = portal_user_client.validate_token(token)

    if token_info and token_info.get("session_id"):
        session_service.revoke_session(db, token_info["session_id"], reason="logout")

    portal_user_client.invalidate_token(token)

    log_audit(
        db=db,
        action="LOGOUT",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Logged out successfully"}


@router.get("/session", response_model=SessionInfo)
def get_session(
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Return current user info and session status."""
    token = _extract_token(request)
    token_info = portal_user_client.validate_token(token)

    session_id = None
    is_active = False
    if token_info and token_info.get("session_id"):
        exam_session = session_service.get_session_by_jti(
            db, token_info["session_id"]
        )
        if exam_session:
            session_id = exam_session.id
            is_active = exam_session.is_active

    return SessionInfo(
        user=current_user,
        session_id=session_id,
        is_active=is_active,
    )


@router.post("/heartbeat")
def heartbeat(
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserInfo = Depends(get_current_user),
):
    """Update session heartbeat. Called periodically by the frontend."""
    # get_current_user already validates session and updates the heartbeat if needed
    return {"status": "ok"}
