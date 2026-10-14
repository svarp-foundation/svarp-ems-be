"""Schemas for Exam Results, Scoring, Analytics, and Publication Controls."""

from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime


class ResultDetailResponse(BaseModel):
    stream_id: UUID
    stream_code: str
    stream_name: str
    total_marks: float
    obtained_marks: float
    correct_count: int
    incorrect_count: int
    unanswered_count: int

    model_config = ConfigDict(from_attributes=True)


class QuestionReviewResponse(BaseModel):
    sequence_number: int
    question_text: str
    stream_name: str
    marks: float
    negative_marks: float
    selected_option_label: Optional[str] = None
    selected_option_text: Optional[str] = None
    correct_option_label: Optional[str] = None
    correct_option_text: Optional[str] = None
    is_correct: Optional[bool] = None
    explanation: Optional[str] = None


class CandidateResultResponse(BaseModel):
    result_id: UUID
    attempt_id: UUID
    student_id: UUID
    student_email: Optional[str] = None
    exam_id: UUID
    exam_title: Optional[str] = None
    rank: Optional[int] = None
    total_marks: float
    total_obtained: float
    percentage: float
    passed: Optional[bool] = None
    total_questions: int
    correct_count: int
    incorrect_count: int
    unanswered_count: int
    stream_details: List[ResultDetailResponse] = []
    questions: Optional[List[QuestionReviewResponse]] = None

    model_config = ConfigDict(from_attributes=True)


class PublicationConfigRequest(BaseModel):
    is_published: bool
    show_answers: bool = False
    show_explanations: bool = False


class PublicationConfigResponse(BaseModel):
    exam_id: UUID
    is_published: bool
    published_at: Optional[datetime] = None
    published_by_id: Optional[UUID] = None
    show_answers: bool
    show_explanations: bool

    model_config = ConfigDict(from_attributes=True)


class StreamAverageStats(BaseModel):
    stream_id: UUID
    stream_code: str
    stream_name: str
    average_score: float
    max_marks: float
    average_percentage: float


class ExamAnalyticsResponse(BaseModel):
    exam_id: UUID
    exam_title: str
    total_candidates: int
    average_score: float
    highest_score: float
    lowest_score: float
    average_percentage: float
    pass_percentage: Optional[float] = None
    pass_rate: float
    stream_averages: List[StreamAverageStats] = []
    score_brackets: Dict[str, int] = {}
