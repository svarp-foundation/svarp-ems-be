from sqlalchemy import Column, String, Boolean
from app.core.database import Base
from app.models.base import BaseModelMixin


class Stream(Base, BaseModelMixin):
    """Subject streams for organizing questions (e.g., Mathematics, Physics)."""

    __tablename__ = "streams"

    name = Column(String, unique=True, nullable=False, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    description = Column(String)
    is_active = Column(Boolean, default=True, nullable=False)
