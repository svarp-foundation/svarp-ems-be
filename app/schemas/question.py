from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
from app.models.question import QuestionStatus, DifficultyLevel


class OptionCreate(BaseModel):
    option_label: str  # A, B, C, D
    option_text: str
    is_correct: bool = False

    @field_validator("option_label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        label = v.strip().upper()
        if label not in ["A", "B", "C", "D"]:
            raise ValueError(f"Option label must be one of A, B, C, D; received: {v}")
        return label


class OptionResponse(BaseModel):
    id: UUID
    option_label: str
    option_text: str
    is_correct: bool

    model_config = ConfigDict(from_attributes=True)


class QuestionVersionResponse(BaseModel):
    id: UUID
    version_number: int
    question_text: str
    explanation: Optional[str] = None
    options: List[OptionResponse]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QuestionCreate(BaseModel):
    stream_id: UUID
    difficulty: DifficultyLevel
    status: QuestionStatus = QuestionStatus.ACTIVE
    tags: Optional[List[str]] = []
    question_text: str
    explanation: Optional[str] = None
    options: List[OptionCreate]

    @field_validator("options")
    @classmethod
    def validate_options(cls, options: List[OptionCreate]) -> List[OptionCreate]:
        if len(options) != 4:
            raise ValueError("Exactly 4 options (A, B, C, D) are required for an MCQ")
        labels = [opt.option_label for opt in options]
        if set(labels) != {"A", "B", "C", "D"}:
            raise ValueError("Options must have labels A, B, C, and D")
        correct_count = sum(1 for opt in options if opt.is_correct)
        if correct_count != 1:
            raise ValueError("Exactly one option must be marked as correct")
        return options


class QuestionUpdate(BaseModel):
    stream_id: Optional[UUID] = None
    difficulty: Optional[DifficultyLevel] = None
    status: Optional[QuestionStatus] = None
    tags: Optional[List[str]] = None
    question_text: Optional[str] = None
    explanation: Optional[str] = None
    options: Optional[List[OptionCreate]] = None

    @field_validator("options")
    @classmethod
    def validate_options(cls, options: Optional[List[OptionCreate]]) -> Optional[List[OptionCreate]]:
        if options is not None:
            if len(options) != 4:
                raise ValueError("Exactly 4 options (A, B, C, D) are required for an MCQ")
            labels = [opt.option_label for opt in options]
            if set(labels) != {"A", "B", "C", "D"}:
                raise ValueError("Options must have labels A, B, C, and D")
            correct_count = sum(1 for opt in options if opt.is_correct)
            if correct_count != 1:
                raise ValueError("Exactly one option must be marked as correct")
        return options


class QuestionResponse(BaseModel):
    id: UUID
    stream_id: UUID
    stream_code: Optional[str] = None
    stream_name: Optional[str] = None
    status: QuestionStatus
    difficulty: DifficultyLevel
    tags: List[str]
    current_version: Optional[QuestionVersionResponse] = None
    total_versions: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QuestionListResponse(BaseModel):
    items: List[QuestionResponse]
    total: int
    skip: int
    limit: int


class RowError(BaseModel):
    row_number: int
    field: str
    message: str


class ImportResultResponse(BaseModel):
    success: bool
    total_rows: int
    imported_count: int
    error_count: int
    errors: List[RowError]
