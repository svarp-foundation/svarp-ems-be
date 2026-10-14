from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
from app.models.attempt import AttemptState


class OptionArenaResponse(BaseModel):
    id: UUID
    display_label: str  # A, B, C, D
    option_text: str


class QuestionArenaResponse(BaseModel):
    attempt_question_id: UUID
    sequence_number: int
    stream_code: str
    stream_name: str
    marks: float
    negative_marks: float
    question_text: str
    options: List[OptionArenaResponse]
    selected_option_id: Optional[UUID] = None
    is_marked_for_review: bool = False
    is_answered: bool = False


class AvailableExamResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    total_questions: int
    instructions: Optional[str] = None
    entry_cutoff_minutes: int = 0
    is_enrolled: bool
    has_active_attempt: bool
    attempt_id: Optional[UUID] = None
    attempt_state: Optional[AttemptState] = None
    is_result_published: bool = False
    default_marks_per_question: Optional[float] = 1.0
    default_negative_marks: Optional[float] = 0.0
    pass_percentage: Optional[float] = None


class StreamInstructionResponse(BaseModel):
    stream_id: str
    name: str
    code: str
    question_count: int
    marks_per_question: float
    negative_marks: float


class ExamInstructionResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    duration_minutes: int
    total_questions: int
    default_marks_per_question: float = 1.0
    default_negative_marks: float = 0.0
    pass_percentage: Optional[float] = None
    entry_cutoff_minutes: int = 0
    start_time: datetime
    end_time: datetime
    has_active_attempt: bool = False
    attempt_id: Optional[UUID] = None
    attempt_state: Optional[AttemptState] = None
    streams: List[StreamInstructionResponse] = []
    total_marks: float


class AttemptStartResponse(BaseModel):
    attempt_id: UUID
    exam_id: UUID
    exam_title: str
    instructions: Optional[str] = None
    duration_minutes: int
    start_time: datetime
    deadline: datetime
    remaining_seconds: int
    state: AttemptState
    is_locked: bool
    lock_reason: Optional[str] = None
    total_questions: int
    questions: List[QuestionArenaResponse]


class SaveAnswerRequest(BaseModel):
    attempt_question_id: UUID
    selected_option_id: Optional[UUID] = None  # None clears answer
    is_marked_for_review: bool = False


class SaveAnswerResponse(BaseModel):
    success: bool
    attempt_question_id: UUID
    selected_option_id: Optional[UUID] = None
    is_marked_for_review: bool
    save_count: int
    updated_at: datetime


class HeartbeatResponse(BaseModel):
    remaining_seconds: int
    state: AttemptState
    is_locked: bool
    lock_reason: Optional[str] = None


class LogEventRequest(BaseModel):
    event_type: str  # 'tab_switch', 'fullscreen_exit', 'focus_loss'
    details: Optional[Dict[str, Any]] = None


class ResumeRequestCreate(BaseModel):
    reason: str


class ResumeRequestResponse(BaseModel):
    id: UUID
    attempt_id: UUID
    reason: str
    requested_at: datetime
    decision: Optional[str] = None
    decided_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AdminAttemptResponse(BaseModel):
    attempt_id: UUID
    exam_id: UUID
    exam_title: str
    student_id: UUID
    student_email: Optional[str] = None
    state: AttemptState
    start_time: datetime
    deadline: datetime
    submitted_at: Optional[datetime] = None
    is_locked: bool
    lock_reason: Optional[str] = None
    answered_count: int = 0
    total_questions: int = 0
    pending_resume_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
