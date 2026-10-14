from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Enum as SAEnum,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import enum
from app.core.database import Base
from app.models.base import BaseModelMixin


class AttemptState(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    INTERRUPTED = "interrupted"
    SUBMITTED = "submitted"
    AUTO_SUBMITTED = "auto_submitted"


class Attempt(Base, BaseModelMixin):
    """
    A student's attempt at an exam. Contains timing and state.
    One active attempt per student per exam is enforced.
    """

    __tablename__ = "attempts"

    student_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    exam_id = Column(
        UUID(as_uuid=True),
        ForeignKey("exams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    state = Column(
        SAEnum(AttemptState, name="attempt_state"),
        default=AttemptState.IN_PROGRESS,
        nullable=False,
    )
    start_time = Column(DateTime(timezone=True), nullable=False)
    deadline = Column(DateTime(timezone=True), nullable=False)
    submitted_at = Column(DateTime(timezone=True))
    last_heartbeat = Column(DateTime(timezone=True))
    is_locked = Column(Boolean, default=False, nullable=False)
    lock_reason = Column(String)

    exam = relationship("Exam")
    attempt_questions = relationship(
        "AttemptQuestion", back_populates="attempt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One active attempt per student per exam
        Index(
            "uq_active_attempt_per_student_exam",
            "student_id",
            "exam_id",
            unique=True,
            postgresql_where=(state.in_([AttemptState.IN_PROGRESS, AttemptState.INTERRUPTED])),
        ),
    )


class AttemptQuestion(Base, BaseModelMixin):
    """
    A question allocated to a specific attempt.
    Stores the exact version and marks — immutable once created.
    """

    __tablename__ = "attempt_questions"

    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stream_id = Column(UUID(as_uuid=True), ForeignKey("streams.id"), nullable=False)
    sequence_number = Column(Integer, nullable=False)
    marks = Column(Float, nullable=False)
    negative_marks = Column(Float, default=0.0, nullable=False)

    attempt = relationship("Attempt", back_populates="attempt_questions")
    question_version = relationship("QuestionVersion")
    options = relationship(
        "AttemptOption", back_populates="attempt_question", cascade="all, delete-orphan"
    )
    answer = relationship(
        "Answer", back_populates="attempt_question", uselist=False, cascade="all, delete-orphan"
    )


class AttemptOption(Base, BaseModelMixin):
    """
    Per-attempt option ordering.
    Persists the shuffled option mapping so a paper is reproducible.
    """

    __tablename__ = "attempt_options"

    attempt_question_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attempt_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_option_id = Column(
        UUID(as_uuid=True),
        ForeignKey("question_options.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_label = Column(String, nullable=False)  # A, B, C, D (shuffled order)
    display_order = Column(Integer, nullable=False)

    attempt_question = relationship("AttemptQuestion", back_populates="options")
    original_option = relationship("QuestionOption")


class Answer(Base, BaseModelMixin):
    """
    Student's saved answer for a specific question in their attempt.
    One answer per attempt-question (upsert pattern).
    """

    __tablename__ = "answers"

    attempt_question_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attempt_questions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    selected_option_id = Column(
        UUID(as_uuid=True),
        ForeignKey("question_options.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_marked_for_review = Column(Boolean, default=False, nullable=False)
    save_count = Column(Integer, default=1, nullable=False)

    attempt_question = relationship("AttemptQuestion", back_populates="answer")
    selected_option = relationship("QuestionOption")


class ResumeRequest(Base, BaseModelMixin):
    """Admin-approved resumption of a locked/interrupted attempt."""

    __tablename__ = "resume_requests"

    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason = Column(String, nullable=False)
    requested_at = Column(DateTime(timezone=True), nullable=False)
    reviewer_id = Column(UUID(as_uuid=True))  # from portal-user
    decision = Column(String)  # 'approved', 'rejected'
    decided_at = Column(DateTime(timezone=True))
