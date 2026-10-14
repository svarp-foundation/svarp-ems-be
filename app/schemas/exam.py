from pydantic import BaseModel, ConfigDict, field_validator, computed_field
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
from app.models.exam import ExamStatus, DurationMode, AllocationMode
from app.models.enrollment import EnrollmentStatus


class DifficultyQuotaCreate(BaseModel):
    difficulty: str  # easy, medium, hard
    quota_mode: AllocationMode = AllocationMode.COUNT
    quota_count: Optional[int] = 0
    quota_percentage: Optional[float] = 0.0


class DifficultyQuotaResponse(BaseModel):
    id: UUID
    difficulty: str
    quota_mode: AllocationMode
    quota_count: Optional[int] = 0
    quota_percentage: Optional[float] = 0.0

    model_config = ConfigDict(from_attributes=True)


class ExamStreamCreate(BaseModel):
    stream_id: UUID
    allocation_mode: AllocationMode = AllocationMode.COUNT
    question_count: Optional[int] = 0
    question_percentage: Optional[float] = 0.0
    marks_per_question: Optional[float] = None
    negative_marks: Optional[float] = None
    difficulty_quotas: List[DifficultyQuotaCreate] = []


class ExamStreamResponse(BaseModel):
    id: UUID
    stream_id: UUID
    stream_name: Optional[str] = None
    stream_code: Optional[str] = None
    allocation_mode: AllocationMode
    question_count: Optional[int] = 0
    question_percentage: Optional[float] = 0.0
    marks_per_question: Optional[float] = None
    negative_marks: Optional[float] = None
    difficulty_quotas: List[DifficultyQuotaResponse] = []

    model_config = ConfigDict(from_attributes=True)


class EnrollmentCreate(BaseModel):
    student_id: Optional[UUID] = None
    student_email: str


class EnrollmentBatchCreate(BaseModel):
    students: List[EnrollmentCreate]


class EnrollmentResponse(BaseModel):
    id: UUID
    exam_id: UUID
    student_id: UUID
    student_email: str
    status: EnrollmentStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExamCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    entry_cutoff_minutes: int = 15
    duration_mode: DurationMode = DurationMode.FIXED
    duration_minutes: int = 60
    hard_end_time: Optional[datetime] = None
    total_questions: int
    default_marks_per_question: float = 1.0
    default_negative_marks: float = 0.0
    shuffle_questions: bool = True
    shuffle_options: bool = True
    pass_percentage: Optional[float] = 40.0
    instructions: Optional[str] = None
    easy_percentage: Optional[float] = 40.0
    medium_percentage: Optional[float] = 40.0
    hard_percentage: Optional[float] = 20.0
    exam_streams: List[ExamStreamCreate] = []

    @field_validator("end_time")
    @classmethod
    def validate_times(cls, end_time: datetime, info) -> datetime:
        start_time = info.data.get("start_time")
        if start_time and end_time <= start_time:
            raise ValueError("Exam end_time must be after start_time")
        return end_time


class ExamUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    entry_cutoff_minutes: Optional[int] = None
    duration_mode: Optional[DurationMode] = None
    duration_minutes: Optional[int] = None
    hard_end_time: Optional[datetime] = None
    total_questions: Optional[int] = None
    default_marks_per_question: Optional[float] = None
    default_negative_marks: Optional[float] = None
    shuffle_questions: Optional[bool] = None
    shuffle_options: Optional[bool] = None
    pass_percentage: Optional[float] = None
    instructions: Optional[str] = None
    exam_streams: Optional[List[ExamStreamCreate]] = None


class ExamResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    entry_cutoff_minutes: int
    duration_mode: DurationMode
    duration_minutes: int
    hard_end_time: Optional[datetime] = None
    total_questions: int
    status: ExamStatus
    default_marks_per_question: float
    default_negative_marks: float
    shuffle_questions: bool
    shuffle_options: bool
    pass_percentage: Optional[float] = None
    instructions: Optional[str] = None
    exam_streams: List[ExamStreamResponse] = []
    enrolled_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QuotaCheckResult(BaseModel):
    stream_code: str
    difficulty: str
    required_count: int
    available_count: int
    is_satisfied: bool

    @computed_field
    @property
    def required(self) -> int:
        return self.required_count

    @computed_field
    @property
    def available(self) -> int:
        return self.available_count

    @computed_field
    @property
    def satisfied(self) -> bool:
        return self.is_satisfied


class ExamValidationReport(BaseModel):
    exam_id: UUID
    is_valid: bool
    total_questions_configured: int
    total_allocated: int
    errors: List[str]
    quotas: List[QuotaCheckResult]

    @computed_field
    @property
    def details(self) -> List[QuotaCheckResult]:
        return self.quotas


class PreviewPaperQuestion(BaseModel):
    sequence_number: int
    stream_code: str
    difficulty: str
    question_text: str
    marks: float
    negative_marks: float
    options: List[Dict[str, Any]]


class PreviewPaperResponse(BaseModel):
    exam_title: str
    total_questions: int
    total_marks: float
    questions: List[PreviewPaperQuestion]
