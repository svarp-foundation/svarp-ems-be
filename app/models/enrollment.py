from sqlalchemy import Column, String, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import enum
from app.core.database import Base
from app.models.base import BaseModelMixin


class EnrollmentStatus(str, enum.Enum):
    ENROLLED = "enrolled"
    WITHDRAWN = "withdrawn"


class Enrollment(Base, BaseModelMixin):
    """Links a student (from portal-user) to an exam."""

    __tablename__ = "enrollments"

    exam_id = Column(
        UUID(as_uuid=True),
        ForeignKey("exams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(UUID(as_uuid=True), nullable=False, index=True)  # from portal-user
    student_email = Column(String, nullable=False)  # cached for display
    status = Column(
        SAEnum(EnrollmentStatus, name="enrollment_status"),
        default=EnrollmentStatus.ENROLLED,
        nullable=False,
    )

    exam = relationship("Exam", back_populates="enrollments")
