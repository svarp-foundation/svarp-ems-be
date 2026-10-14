from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from app.core.database import Base
from app.models.base import BaseModelMixin


class ExamStatus(str, enum.Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DurationMode(str, enum.Enum):
    FIXED = "fixed"  # All students get the same duration
    CALCULATED = "calculated"  # Duration calculated based on question count


class AllocationMode(str, enum.Enum):
    COUNT = "count"  # Exact number of questions
    PERCENTAGE = "percentage"  # Percentage of total questions


class Exam(Base, BaseModelMixin):
    """Exam configuration with scheduling, timing, and marking rules."""

    __tablename__ = "exams"

    title = Column(String, nullable=False)
    description = Column(String)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    entry_cutoff_minutes = Column(Integer, default=0)
    duration_mode = Column(
        SAEnum(DurationMode, name="duration_mode"),
        default=DurationMode.FIXED,
        nullable=False,
    )
    duration_minutes = Column(Integer, nullable=False)
    hard_end_time = Column(DateTime(timezone=True))  # Optional absolute end
    total_questions = Column(Integer, nullable=False)
    status = Column(
        SAEnum(ExamStatus, name="exam_status"),
        default=ExamStatus.DRAFT,
        nullable=False,
    )
    # Default marking rules
    default_marks_per_question = Column(Float, default=1.0, nullable=False)
    default_negative_marks = Column(Float, default=0.0, nullable=False)
    shuffle_questions = Column(Boolean, default=True, nullable=False)
    shuffle_options = Column(Boolean, default=True, nullable=False)
    pass_percentage = Column(Float)  # Optional pass/fail threshold
    instructions = Column(String)
    created_by_id = Column(UUID(as_uuid=True))  # user_id from portal-user

    exam_streams = relationship(
        "ExamStream", back_populates="exam", cascade="all, delete-orphan"
    )
    enrollments = relationship(
        "Enrollment", back_populates="exam", cascade="all, delete-orphan"
    )


class ExamStream(Base, BaseModelMixin):
    """Links an exam to a stream with allocation rules."""

    __tablename__ = "exam_streams"

    exam_id = Column(
        UUID(as_uuid=True),
        ForeignKey("exams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stream_id = Column(
        UUID(as_uuid=True),
        ForeignKey("streams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    allocation_mode = Column(
        SAEnum(AllocationMode, name="allocation_mode"),
        default=AllocationMode.COUNT,
        nullable=False,
    )
    question_count = Column(Integer)  # Used when mode=COUNT
    question_percentage = Column(Float)  # Used when mode=PERCENTAGE
    marks_per_question = Column(Float)  # Overrides exam default if set
    negative_marks = Column(Float)  # Overrides exam default if set

    exam = relationship("Exam", back_populates="exam_streams")
    stream = relationship("Stream")
    difficulty_quotas = relationship(
        "DifficultyQuota", back_populates="exam_stream", cascade="all, delete-orphan"
    )


class DifficultyQuota(Base, BaseModelMixin):
    """Per-stream difficulty distribution for question allocation."""

    __tablename__ = "difficulty_quotas"

    exam_stream_id = Column(
        UUID(as_uuid=True),
        ForeignKey("exam_streams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    difficulty = Column(String, nullable=False)  # easy, medium, hard
    quota_mode = Column(
        SAEnum(AllocationMode, name="allocation_mode", create_type=False),
        default=AllocationMode.COUNT,
        nullable=False,
    )
    quota_count = Column(Integer)
    quota_percentage = Column(Float)

    exam_stream = relationship("ExamStream", back_populates="difficulty_quotas")
