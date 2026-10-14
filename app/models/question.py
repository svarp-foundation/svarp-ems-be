from sqlalchemy import Column, String, Boolean, Integer, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from app.core.database import Base
from app.models.base import BaseModelMixin


class QuestionStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class DifficultyLevel(str, enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Question(Base, BaseModelMixin):
    """
    Master question record. Actual content lives in question_versions.
    current_version_id points to the latest approved version.
    """

    __tablename__ = "questions"

    stream_id = Column(
        UUID(as_uuid=True),
        ForeignKey("streams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = Column(
        SAEnum(QuestionStatus, name="question_status"),
        default=QuestionStatus.DRAFT,
        nullable=False,
    )
    difficulty = Column(
        SAEnum(DifficultyLevel, name="difficulty_level"),
        nullable=False,
    )
    tags = Column(JSONB, default=list)
    current_version_id = Column(UUID(as_uuid=True), nullable=True)

    stream = relationship("Stream")
    versions = relationship(
        "QuestionVersion", back_populates="question", cascade="all, delete-orphan"
    )


class QuestionVersion(Base, BaseModelMixin):
    """
    Immutable snapshot of question content.
    Once created, never edited — ensures exam integrity.
    """

    __tablename__ = "question_versions"

    question_id = Column(
        UUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False, default=1)
    question_text = Column(Text, nullable=False)
    explanation = Column(Text)

    question = relationship("Question", back_populates="versions")
    options = relationship(
        "QuestionOption", back_populates="version", cascade="all, delete-orphan"
    )


class QuestionOption(Base, BaseModelMixin):
    """Individual MCQ option belonging to a question version."""

    __tablename__ = "question_options"

    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("question_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    option_label = Column(String, nullable=False)  # A, B, C, D
    option_text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=False, nullable=False)

    version = relationship("QuestionVersion", back_populates="options")
