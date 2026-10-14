from pydantic import BaseModel, EmailStr
from typing import Optional, List
from uuid import UUID


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    bypass_key: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserInfo"


class UserInfo(BaseModel):
    user_id: UUID
    email: str
    full_name: Optional[str] = None
    is_active: bool
    roles: List[str] = []


class SessionInfo(BaseModel):
    user: UserInfo
    session_id: UUID
    is_active: bool


LoginResponse.model_rebuild()
