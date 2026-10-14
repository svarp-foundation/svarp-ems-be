from pydantic import BaseModel
from typing import Optional, List, Any


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    skip: int
    limit: int


class ErrorResponse(BaseModel):
    detail: str


class MessageResponse(BaseModel):
    message: str
