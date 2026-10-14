"""Schemas for Security Audit Logs and Proctoring Telemetry."""

from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime


class AuditLogResponse(BaseModel):
    id: UUID
    actor_id: Optional[UUID] = None
    actor_email: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[UUID] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExamEventResponse(BaseModel):
    id: UUID
    attempt_id: UUID
    student_id: UUID
    event_type: str
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditStatsResponse(BaseModel):
    total_audit_logs: int
    total_proctoring_events: int
    action_counts: Dict[str, int]
    violation_counts: Dict[str, int]
    active_sessions_count: int
