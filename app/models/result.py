from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from app.models.base import BaseModelMixin


class Result(Base, BaseModelMixin):
    """Aggregated result for a student's exam attempt."""

    __tablename__ = "results"

    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attempts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    student_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    exam_id = Column(
        UUID(as_uuid=True),
        ForeignKey("exams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    total_marks = Column(Float, nullable=False, default=0.0)
    total_obtained = Column(Float, nullable=False, default=0.0)
    total_questions = Column(Integer, nullable=False, default=0)
    correct_count = Column(Integer, nullable=False, default=0)
    incorrect_count = Column(Integer, nullable=False, default=0)
    unanswered_count = Column(Integer, nullable=False, default=0)
    percentage = Column(Float, nullable=False, default=0.0)
    passed = Column(Boolean)  # None if no pass/fail configured

    attempt = relationship("Attempt")
    details = relationship(
        "ResultDetail", back_populates="result", cascade="all, delete-orphan"
    )


class ResultDetail(Base, BaseModelMixin):
    """Per-stream score breakdown within a result."""

    __tablename__ = "result_details"

    result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stream_id = Column(UUID(as_uuid=True), ForeignKey("streams.id"), nullable=False)
    total_marks = Column(Float, nullable=False, default=0.0)
    obtained_marks = Column(Float, nullable=False, default=0.0)
    correct_count = Column(Integer, nullable=False, default=0)
    incorrect_count = Column(Integer, nullable=False, default=0)
    unanswered_count = Column(Integer, nullable=False, default=0)

    result = relationship("Result", back_populates="details")
    stream = relationship("Stream")


class ResultPublication(Base, BaseModelMixin):
    """Controls when and by whom results are made visible to students."""

    __tablename__ = "result_publications"

    exam_id = Column(
        UUID(as_uuid=True),
        ForeignKey("exams.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    is_published = Column(Boolean, default=False, nullable=False)
    published_by_id = Column(UUID(as_uuid=True))  # from portal-user
    published_at = Column(DateTime(timezone=True))
    show_answers = Column(Boolean, default=False, nullable=False)
    show_explanations = Column(Boolean, default=False, nullable=False)

    exam = relationship("Exam")
