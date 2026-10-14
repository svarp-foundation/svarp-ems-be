"""Exam configuration, difficulty quota resolution, and atomic paper generation service."""

import logging
import random
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from app.models.exam import Exam, ExamStream, DifficultyQuota, ExamStatus, DurationMode, AllocationMode
from app.models.stream import Stream
from app.models.question import Question, QuestionVersion, QuestionOption, QuestionStatus, DifficultyLevel
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.attempt import Attempt, AttemptQuestion, AttemptOption, AttemptState
from app.schemas.exam import (
    ExamCreate,
    ExamUpdate,
    ExamValidationReport,
    QuotaCheckResult,
    PreviewPaperResponse,
    PreviewPaperQuestion,
)
from app.services.portal_user_client import portal_user_client

logger = logging.getLogger("portal_exam.exam_service")


class ExamService:

    def _format_exam_response(self, exam: Exam, db: Session, enrolled_count: int = None) -> Dict[str, Any]:
        """Format an Exam model with streams and enrolled count."""
        if enrolled_count is None:
            enrolled_count = (
                db.query(func.count(Enrollment.id))
                .filter(Enrollment.exam_id == exam.id, Enrollment.status == EnrollmentStatus.ENROLLED)
                .scalar()
                or 0
            )

        formatted_streams = []
        for es in exam.exam_streams:
            stream_obj = es.stream  # Use eagerly-loaded relationship instead of N+1 query
            quotas = [
                {
                    "id": q.id,
                    "difficulty": q.difficulty,
                    "quota_mode": q.quota_mode,
                    "quota_count": q.quota_count,
                    "quota_percentage": q.quota_percentage,
                }
                for q in es.difficulty_quotas
            ]
            formatted_streams.append(
                {
                    "id": es.id,
                    "stream_id": es.stream_id,
                    "stream_name": stream_obj.name if stream_obj else None,
                    "stream_code": stream_obj.code if stream_obj else None,
                    "allocation_mode": es.allocation_mode,
                    "question_count": es.question_count,
                    "question_percentage": es.question_percentage,
                    "marks_per_question": es.marks_per_question,
                    "negative_marks": es.negative_marks,
                    "difficulty_quotas": quotas,
                }
            )

        return {
            "id": exam.id,
            "title": exam.title,
            "description": exam.description,
            "start_time": exam.start_time,
            "end_time": exam.end_time,
            "entry_cutoff_minutes": exam.entry_cutoff_minutes,
            "duration_mode": exam.duration_mode,
            "duration_minutes": exam.duration_minutes,
            "hard_end_time": exam.hard_end_time,
            "total_questions": exam.total_questions,
            "status": exam.status,
            "default_marks_per_question": exam.default_marks_per_question,
            "default_negative_marks": exam.default_negative_marks,
            "shuffle_questions": exam.shuffle_questions,
            "shuffle_options": exam.shuffle_options,
            "pass_percentage": exam.pass_percentage,
            "instructions": exam.instructions,
            "exam_streams": formatted_streams,
            "enrolled_count": enrolled_count,
            "created_at": exam.created_at,
            "updated_at": exam.updated_at,
        }

    def create_exam(
        self, db: Session, data: ExamCreate, created_by_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Create a new exam in DRAFT status with stream allocations and difficulty quotas."""
        exam = Exam(
            title=data.title,
            description=data.description,
            start_time=data.start_time,
            end_time=data.end_time,
            entry_cutoff_minutes=data.entry_cutoff_minutes,
            duration_mode=data.duration_mode,
            duration_minutes=data.duration_minutes,
            hard_end_time=data.hard_end_time,
            total_questions=data.total_questions,
            status=ExamStatus.DRAFT,
            default_marks_per_question=data.default_marks_per_question,
            default_negative_marks=data.default_negative_marks,
            shuffle_questions=data.shuffle_questions,
            shuffle_options=data.shuffle_options,
            pass_percentage=data.pass_percentage,
            instructions=data.instructions,
            created_by_id=created_by_id,
        )
        db.add(exam)
        db.flush()

        for es_in in data.exam_streams:
            es = ExamStream(
                exam_id=exam.id,
                stream_id=es_in.stream_id,
                allocation_mode=es_in.allocation_mode,
                question_count=es_in.question_count,
                question_percentage=es_in.question_percentage,
                marks_per_question=es_in.marks_per_question,
                negative_marks=es_in.negative_marks,
            )
            db.add(es)
            db.flush()

            if not es_in.difficulty_quotas and (es_in.question_count or 0) > 0:
                # Auto-divide questions as per customizable percentages (default 40% easy, 40% med, 20% hard)
                q_count = es_in.question_count or 0
                easy_p = getattr(data, "easy_percentage", 40.0) or 40.0
                med_p = getattr(data, "medium_percentage", 40.0) or 40.0
                hard_p = getattr(data, "hard_percentage", 20.0) or 20.0
                total_p = (easy_p + med_p + hard_p) or 100.0

                easy_c = round((easy_p / total_p) * q_count)
                med_c = round((med_p / total_p) * q_count)
                hard_c = max(0, q_count - easy_c - med_c)

                auto_splits = [
                    ("easy", easy_c, easy_p),
                    ("medium", med_c, med_p),
                    ("hard", hard_c, hard_p),
                ]
                for diff, cnt, pct in auto_splits:
                    dq = DifficultyQuota(
                        exam_stream_id=es.id,
                        difficulty=diff,
                        quota_mode=AllocationMode.COUNT,
                        quota_count=cnt,
                        quota_percentage=pct,
                    )
                    db.add(dq)
            else:
                for dq_in in es_in.difficulty_quotas:
                    dq = DifficultyQuota(
                        exam_stream_id=es.id,
                        difficulty=dq_in.difficulty.lower(),
                        quota_mode=dq_in.quota_mode,
                        quota_count=dq_in.quota_count,
                        quota_percentage=dq_in.quota_percentage,
                    )
                    db.add(dq)

        db.commit()
        db.refresh(exam)
        return self._format_exam_response(exam, db)

    def get_exam(self, db: Session, exam_id: UUID) -> Optional[Dict[str, Any]]:
        """Retrieve exam by ID."""
        exam = (
            db.query(Exam)
            .options(
                joinedload(Exam.exam_streams).joinedload(ExamStream.difficulty_quotas),
                joinedload(Exam.exam_streams).joinedload(ExamStream.stream),
            )
            .filter(Exam.id == exam_id, Exam.is_deleted == False)
            .first()
        )
        if not exam:
            return None
        return self._format_exam_response(exam, db)

    def get_exams(
        self,
        db: Session,
        status: Optional[ExamStatus] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List exams with filtering and pagination."""
        query = (
            db.query(Exam)
            .options(
                joinedload(Exam.exam_streams).joinedload(ExamStream.difficulty_quotas),
                joinedload(Exam.exam_streams).joinedload(ExamStream.stream),
            )
            .filter(Exam.is_deleted == False)
        )

        if status:
            query = query.filter(Exam.status == status)
        if search:
            query = query.filter(Exam.title.ilike(f"%{search.strip()}%"))

        total = query.count()
        exams = query.order_by(Exam.start_time.desc()).offset(skip).limit(limit).all()

        # Batch enrollment counts in a single query instead of N+1
        exam_ids = [e.id for e in exams]
        enrollment_counts = {}
        if exam_ids:
            counts = (
                db.query(Enrollment.exam_id, func.count(Enrollment.id))
                .filter(Enrollment.exam_id.in_(exam_ids), Enrollment.status == EnrollmentStatus.ENROLLED)
                .group_by(Enrollment.exam_id)
                .all()
            )
            enrollment_counts = {row[0]: row[1] for row in counts}

        return [self._format_exam_response(e, db, enrolled_count=enrollment_counts.get(e.id, 0)) for e in exams], total

    def update_exam(
        self, db: Session, exam_id: UUID, data: ExamUpdate
    ) -> Optional[Dict[str, Any]]:
        """Update exam settings if not published or in-progress."""
        exam = (
            db.query(Exam)
            .options(joinedload(Exam.exam_streams).joinedload(ExamStream.difficulty_quotas))
            .filter(Exam.id == exam_id, Exam.is_deleted == False)
            .first()
        )
        if not exam:
            return None

        if exam.status in [ExamStatus.PUBLISHED, ExamStatus.IN_PROGRESS, ExamStatus.COMPLETED]:
            raise ValueError(f"Cannot edit exam in {exam.status} state. Structural settings are locked.")

        # Update basic fields
        update_dict = data.model_dump(exclude_unset=True)
        streams_data = update_dict.pop("exam_streams", None)

        for field, value in update_dict.items():
            setattr(exam, field, value)

        # If stream allocations provided, re-create them
        if streams_data is not None:
            # Delete old exam_streams
            db.query(ExamStream).filter(ExamStream.exam_id == exam.id).delete()
            db.flush()

            for es_in in data.exam_streams or []:
                es = ExamStream(
                    exam_id=exam.id,
                    stream_id=es_in.stream_id,
                    allocation_mode=es_in.allocation_mode,
                    question_count=es_in.question_count,
                    question_percentage=es_in.question_percentage,
                    marks_per_question=es_in.marks_per_question,
                    negative_marks=es_in.negative_marks,
                )
                db.add(es)
                db.flush()

                if not es_in.difficulty_quotas and (es_in.question_count or 0) > 0:
                    q_count = es_in.question_count or 0
                    easy_p = getattr(data, "easy_percentage", 40.0) or 40.0
                    med_p = getattr(data, "medium_percentage", 40.0) or 40.0
                    hard_p = getattr(data, "hard_percentage", 20.0) or 20.0
                    total_p = (easy_p + med_p + hard_p) or 100.0

                    easy_c = round((easy_p / total_p) * q_count)
                    med_c = round((med_p / total_p) * q_count)
                    hard_c = max(0, q_count - easy_c - med_c)

                    auto_splits = [
                        ("easy", easy_c, easy_p),
                        ("medium", med_c, med_p),
                        ("hard", hard_c, hard_p),
                    ]
                    for diff, cnt, pct in auto_splits:
                        dq = DifficultyQuota(
                            exam_stream_id=es.id,
                            difficulty=diff,
                            quota_mode=AllocationMode.COUNT,
                            quota_count=cnt,
                            quota_percentage=pct,
                        )
                        db.add(dq)
                else:
                    for dq_in in (es_in.difficulty_quotas or []):
                        dq = DifficultyQuota(
                            exam_stream_id=es.id,
                            difficulty=dq_in.difficulty.lower(),
                            quota_mode=dq_in.quota_mode,
                            quota_count=dq_in.quota_count,
                            quota_percentage=dq_in.quota_percentage,
                        )
                        db.add(dq)

            # Revert to DRAFT for re-validation
            exam.status = ExamStatus.DRAFT

        db.commit()
        db.refresh(exam)
        return self._format_exam_response(exam, db)

    def delete_exam(self, db: Session, exam_id: UUID) -> bool:
        """Soft delete exam (only allowed for DRAFT or CANCELLED exams)."""
        exam = db.query(Exam).filter(Exam.id == exam_id, Exam.is_deleted == False).first()
        if not exam:
            return False

        if exam.status in [ExamStatus.PUBLISHED, ExamStatus.IN_PROGRESS, ExamStatus.COMPLETED]:
            raise ValueError("Cannot delete an exam that has been published, conducted, or is in progress")

        exam.is_deleted = True
        exam.status = ExamStatus.CANCELLED
        db.commit()
        return True

    def validate_exam_quotas(self, db: Session, exam_id: UUID) -> ExamValidationReport:
        """
        Validate question availability across all configured streams & difficulty quotas.
        If all buckets satisfy the required counts, sets status to VALIDATED.
        """
        exam = (
            db.query(Exam)
            .options(joinedload(Exam.exam_streams).joinedload(ExamStream.difficulty_quotas))
            .filter(Exam.id == exam_id, Exam.is_deleted == False)
            .first()
        )
        if not exam:
            raise ValueError("Exam not found")

        errors: List[str] = []
        quota_checks: List[QuotaCheckResult] = []
        total_allocated = 0

        for es in exam.exam_streams:
            stream_obj = db.query(Stream).filter(Stream.id == es.stream_id).first()
            stream_code = stream_obj.code if stream_obj else "STREAM"

            # Resolve stream question count
            if es.allocation_mode == AllocationMode.PERCENTAGE:
                stream_target_count = round((es.question_percentage / 100.0) * exam.total_questions)
            else:
                stream_target_count = es.question_count or 0

            total_allocated += stream_target_count

            # Difficulty quota sum check or flat stream check
            stream_quota_sum = 0
            if es.difficulty_quotas:
                for dq in es.difficulty_quotas:
                    if dq.quota_mode == AllocationMode.PERCENTAGE:
                        required_dq_count = round((dq.quota_percentage / 100.0) * stream_target_count)
                    else:
                        required_dq_count = dq.quota_count or 0

                    stream_quota_sum += required_dq_count

                    # Check eligible questions in DB
                    available_count = (
                        db.query(func.count(Question.id))
                        .filter(
                            Question.stream_id == es.stream_id,
                            Question.difficulty == dq.difficulty,
                            Question.status == QuestionStatus.ACTIVE,
                            Question.is_deleted == False,
                        )
                        .scalar()
                        or 0
                    )

                    satisfied = available_count >= required_dq_count
                    if not satisfied:
                        errors.append(
                            f"Stream {stream_code} ({dq.difficulty}): Requires {required_dq_count} questions, but only {available_count} active questions exist."
                        )

                    quota_checks.append(
                        QuotaCheckResult(
                            stream_code=stream_code,
                            difficulty=dq.difficulty,
                            required_count=required_dq_count,
                            available_count=available_count,
                            is_satisfied=satisfied,
                        )
                    )

                if stream_quota_sum != stream_target_count:
                    errors.append(
                        f"Stream {stream_code}: Sum of difficulty quotas ({stream_quota_sum}) does not equal stream question allocation ({stream_target_count})."
                    )
            else:
                # Flat stream check
                available_count = (
                    db.query(func.count(Question.id))
                    .filter(
                        Question.stream_id == es.stream_id,
                        Question.status == QuestionStatus.ACTIVE,
                        Question.is_deleted == False,
                    )
                    .scalar()
                    or 0
                )
                satisfied = available_count >= stream_target_count
                if not satisfied:
                    errors.append(
                        f"Stream {stream_code}: Requires {stream_target_count} questions, but only {available_count} active questions exist."
                    )
                quota_checks.append(
                    QuotaCheckResult(
                        stream_code=stream_code,
                        difficulty="all",
                        required_count=stream_target_count,
                        available_count=available_count,
                        is_satisfied=satisfied,
                    )
                )

        if total_allocated != exam.total_questions:
            errors.append(
                f"Sum of stream question allocations ({total_allocated}) does not equal exam total questions ({exam.total_questions})."
            )

        is_valid = len(errors) == 0

        if is_valid and exam.status == ExamStatus.DRAFT:
            exam.status = ExamStatus.VALIDATED
            db.commit()

        return ExamValidationReport(
            exam_id=exam.id,
            is_valid=is_valid,
            total_questions_configured=exam.total_questions,
            total_allocated=total_allocated,
            errors=errors,
            quotas=quota_checks,
        )

    def publish_exam(self, db: Session, exam_id: UUID) -> Dict[str, Any]:
        """Gatekeeper check before publishing: re-validates question availability."""
        exam = db.query(Exam).filter(Exam.id == exam_id, Exam.is_deleted == False).first()
        if not exam:
            raise ValueError("Exam not found")

        report = self.validate_exam_quotas(db, exam_id)
        if not report.is_valid:
            error_details = " | ".join(report.errors)
            raise ValueError(f"Exam publication blocked. Quotas not satisfied: {error_details}")

        exam.status = ExamStatus.PUBLISHED
        db.commit()
        db.refresh(exam)
        return self._format_exam_response(exam, db)

    def enroll_students(
        self, db: Session, exam_id: UUID, students: List[Tuple[Optional[UUID], str]]
    ) -> int:
        """
        Enroll a batch of students to an exam.
        Validates each candidate against SVARP User Portal to ensure:
        1. The user account exists in the central identity directory.
        2. The user account is active.
        3. Real verified user_id and email are bound to the enrollment.
        """
        exam = db.query(Exam).filter(Exam.id == exam_id, Exam.is_deleted == False).first()
        if not exam:
            raise ValueError("Exam not found")

        new_count = 0
        for student_id, student_email in students:
            clean_email = (student_email or "").strip().lower()
            if not clean_email:
                continue

            # Verify against SVARP Central User Portal
            user_data = portal_user_client.get_user(
                user_id=student_id if student_id else None,
                email=clean_email,
            )
            if not user_data:
                raise ValueError(
                    f"Candidate '{student_email.strip()}' does not exist in SVARP User Portal. "
                    "Only registered portal users can be enrolled."
                )

            if not user_data.get("is_active", True):
                raise ValueError(
                    f"Candidate '{student_email.strip()}' is marked as inactive in SVARP User Portal."
                )

            verified_student_id = UUID(user_data["user_id"])
            verified_email = user_data.get("email", clean_email).strip()

            existing = (
                db.query(Enrollment)
                .filter(
                    Enrollment.exam_id == exam_id,
                    (Enrollment.student_id == verified_student_id)
                    | (func.lower(Enrollment.student_email) == clean_email),
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            if not existing:
                enrollment = Enrollment(
                    exam_id=exam_id,
                    student_id=verified_student_id,
                    student_email=verified_email,
                    status=EnrollmentStatus.ENROLLED,
                )
                db.add(enrollment)
                new_count += 1
            else:
                existing.status = EnrollmentStatus.ENROLLED
                existing.student_id = verified_student_id
                existing.student_email = verified_email
                # Re-activation of withdrawn enrollment; not counted as newly enrolled

        db.commit()
        return new_count

    def get_enrollments(self, db: Session, exam_id: UUID) -> List[Enrollment]:
        """List all active enrollments for an exam."""
        return (
            db.query(Enrollment)
            .filter(
                Enrollment.exam_id == exam_id,
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
            .order_by(Enrollment.created_at.desc())
            .all()
        )

    def withdraw_student(self, db: Session, exam_id: UUID, student_id: UUID) -> bool:
        """Withdraw student enrollment from an exam."""
        enrollment = (
            db.query(Enrollment)
            .filter(Enrollment.exam_id == exam_id, Enrollment.student_id == student_id)
            .first()
        )
        if not enrollment:
            return False
        enrollment.status = EnrollmentStatus.WITHDRAWN
        db.commit()
        return True

    def generate_paper_for_attempt(
        self, db: Session, exam_id: UUID, student_id: UUID
    ) -> Attempt:
        """
        Atomic paper generation satisfying stream quotas & difficulty allocations.
        Key Invariant: An existing attempt NEVER receives a regenerated paper!
        """
        # 0. Acquire advisory transaction lock to serialize concurrent requests for same candidate & exam
        try:
            lock_key = f"attempt_{exam_id}_{student_id}"
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})
        except Exception as e:
            logger.warning(f"Advisory lock acquisition failed for attempt generation (exam={exam_id}, student={student_id}): {e}")

        # 1. Invariant: Check if attempt already exists
        existing_attempt = (
            db.query(Attempt)
            .options(
                joinedload(Attempt.attempt_questions)
                .joinedload(AttemptQuestion.options)
            )
            .filter(
                Attempt.exam_id == exam_id,
                Attempt.student_id == student_id,
            )
            .first()
        )
        if existing_attempt:
            if existing_attempt.state in [AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]:
                raise ValueError("Attempt already submitted. Cannot regenerate paper.")
            return existing_attempt

        # 2. Load exam and configuration
        exam = (
            db.query(Exam)
            .options(joinedload(Exam.exam_streams).joinedload(ExamStream.difficulty_quotas))
            .filter(Exam.id == exam_id, Exam.is_deleted == False)
            .first()
        )
        if not exam:
            raise ValueError("Exam not found")

        # 3. Timing calculation
        now = datetime.now(timezone.utc)
        allocated_duration = timedelta(minutes=exam.duration_minutes)
        calculated_deadline = now + allocated_duration
        if exam.hard_end_time and calculated_deadline > exam.hard_end_time:
            deadline = exam.hard_end_time
        else:
            deadline = calculated_deadline

        # 4. Resolve questions satisfying quotas
        allocated_questions_data: List[Dict[str, Any]] = []
        selected_question_ids = set()

        sys_rand = secrets.SystemRandom()

        for es in exam.exam_streams:
            stream_marks = es.marks_per_question if es.marks_per_question is not None else exam.default_marks_per_question
            stream_negative = es.negative_marks if es.negative_marks is not None else exam.default_negative_marks

            if es.difficulty_quotas:
                for dq in es.difficulty_quotas:
                    # Resolve count
                    if dq.quota_mode == AllocationMode.PERCENTAGE:
                        required_count = round((dq.quota_percentage / 100.0) * (es.question_count or 0))
                    else:
                        required_count = dq.quota_count or 0

                    if required_count <= 0:
                        continue

                    # Query pool of eligible active questions
                    candidate_pool = (
                        db.query(Question)
                        .options(joinedload(Question.versions))
                        .filter(
                            Question.stream_id == es.stream_id,
                            Question.difficulty == dq.difficulty,
                            Question.status == QuestionStatus.ACTIVE,
                            Question.is_deleted == False,
                            ~Question.id.in_(selected_question_ids) if selected_question_ids else True,
                        )
                        .all()
                    )

                    if len(candidate_pool) < required_count:
                        raise ValueError(
                            f"Insufficient question bank quota for stream {es.stream_id} ({dq.difficulty}). "
                            f"Required {required_count}, but only {len(candidate_pool)} available."
                        )

                    # Secure random sample
                    chosen_sample = sys_rand.sample(candidate_pool, required_count)
                    for q in chosen_sample:
                        selected_question_ids.add(q.id)
                        allocated_questions_data.append(
                            {
                                "question": q,
                                "stream_id": es.stream_id,
                                "marks": stream_marks,
                                "negative_marks": stream_negative,
                            }
                        )
            else:
                # Flat stream allocation
                required_count = es.question_count or 0
                if required_count > 0:
                    candidate_pool = (
                        db.query(Question)
                        .options(joinedload(Question.versions))
                        .filter(
                            Question.stream_id == es.stream_id,
                            Question.status == QuestionStatus.ACTIVE,
                            Question.is_deleted == False,
                            ~Question.id.in_(selected_question_ids) if selected_question_ids else True,
                        )
                        .all()
                    )
                    if len(candidate_pool) < required_count:
                        raise ValueError(
                            f"Insufficient question bank quota for stream {es.stream_id}. "
                            f"Required {required_count}, but only {len(candidate_pool)} available."
                        )
                    chosen_sample = sys_rand.sample(candidate_pool, required_count)
                    for q in chosen_sample:
                        selected_question_ids.add(q.id)
                        allocated_questions_data.append(
                            {
                                "question": q,
                                "stream_id": es.stream_id,
                                "marks": stream_marks,
                                "negative_marks": stream_negative,
                            }
                        )

        # 5. Shuffle questions if enabled
        if exam.shuffle_questions:
            sys_rand.shuffle(allocated_questions_data)

        # 6. Create Attempt atomically in DB
        attempt = Attempt(
            exam_id=exam.id,
            student_id=student_id,
            state=AttemptState.IN_PROGRESS,
            start_time=now,
            deadline=deadline,
            is_locked=False,
            last_heartbeat=now,
        )
        db.add(attempt)
        db.flush()

        # 7. Persist allocated attempt questions and option order
        v_ids = [
            item["question"].current_version_id
            for item in allocated_questions_data
            if item["question"].current_version_id
        ]
        versions_map = {}
        if v_ids:
            loaded_versions = (
                db.query(QuestionVersion)
                .options(joinedload(QuestionVersion.options))
                .filter(QuestionVersion.id.in_(v_ids))
                .all()
            )
            versions_map = {v.id: v for v in loaded_versions}

        for seq_idx, item in enumerate(allocated_questions_data, start=1):
            q_obj = item["question"]
            version_id = q_obj.current_version_id
            current_version = versions_map.get(version_id)
            if not current_version and q_obj.versions:
                current_version = q_obj.versions[-1]
                version_id = current_version.id

            if not current_version:
                raise ValueError(
                    f"Question {q_obj.id} has no valid version. Cannot generate paper."
                )

            att_question = AttemptQuestion(
                attempt_id=attempt.id,
                question_version_id=version_id,
                stream_id=item["stream_id"],
                sequence_number=seq_idx,
                marks=item["marks"],
                negative_marks=item["negative_marks"],
            )
            db.add(att_question)
            db.flush()

            # Options
            original_options = list(current_version.options)
            if exam.shuffle_options:
                sys_rand.shuffle(original_options)

            # Map shuffled options to labels A, B, C, D for this attempt
            display_labels = ["A", "B", "C", "D"]
            for opt_idx, orig_opt in enumerate(original_options):
                lbl = display_labels[opt_idx] if opt_idx < len(display_labels) else orig_opt.option_label
                att_option = AttemptOption(
                    attempt_question_id=att_question.id,
                    original_option_id=orig_opt.id,
                    display_order=opt_idx + 1,
                    display_label=lbl,
                )
                db.add(att_option)

        try:
            db.commit()
            db.refresh(attempt)
            return attempt
        except IntegrityError:
            db.rollback()
            # Concurrently created by a competing request; fetch existing attempt
            existing_attempt = (
                db.query(Attempt)
                .options(
                    joinedload(Attempt.attempt_questions)
                    .joinedload(AttemptQuestion.options)
                )
                .filter(
                    Attempt.exam_id == exam_id,
                    Attempt.student_id == student_id,
                )
                .first()
            )
            if existing_attempt:
                return existing_attempt
            raise

    def preview_paper(self, db: Session, exam_id: UUID) -> PreviewPaperResponse:
        """Simulate candidate paper generation without saving to verify quota allocations."""
        exam = (
            db.query(Exam)
            .options(joinedload(Exam.exam_streams).joinedload(ExamStream.difficulty_quotas))
            .filter(Exam.id == exam_id, Exam.is_deleted == False)
            .first()
        )
        if not exam:
            raise ValueError("Exam not found")

        simulated_questions: List[PreviewPaperQuestion] = []
        total_marks = 0.0
        selected_ids = set()

        for es in exam.exam_streams:
            stream_obj = db.query(Stream).filter(Stream.id == es.stream_id).first()
            stream_code = stream_obj.code if stream_obj else "STREAM"
            marks = es.marks_per_question if es.marks_per_question is not None else exam.default_marks_per_question
            neg_marks = es.negative_marks if es.negative_marks is not None else exam.default_negative_marks

            if es.difficulty_quotas:
                for dq in es.difficulty_quotas:
                    if dq.quota_mode == AllocationMode.PERCENTAGE:
                        req_count = round((dq.quota_percentage / 100.0) * (es.question_count or 0))
                    else:
                        req_count = dq.quota_count or 0
                    if req_count <= 0:
                        continue

                    candidates = (
                        db.query(Question)
                        .options(joinedload(Question.versions).joinedload(QuestionVersion.options))
                        .filter(
                            Question.stream_id == es.stream_id,
                            Question.difficulty == dq.difficulty,
                            Question.status == QuestionStatus.ACTIVE,
                            Question.is_deleted == False,
                            ~Question.id.in_(selected_ids) if selected_ids else True,
                        )
                        .limit(req_count)
                        .all()
                    )

                    for q in candidates:
                        selected_ids.add(q.id)
                        ver = q.versions[-1] if q.versions else None
                        opts = [
                            {"label": opt.option_label, "text": opt.option_text}
                            for opt in (ver.options if ver else [])
                        ]
                        total_marks += marks
                        simulated_questions.append(
                            PreviewPaperQuestion(
                                sequence_number=len(simulated_questions) + 1,
                                stream_code=stream_code,
                                difficulty=q.difficulty,
                                question_text=ver.question_text if ver else "No version text",
                                marks=marks,
                                negative_marks=neg_marks,
                                options=opts,
                            )
                        )
            else:
                req_count = es.question_count or 0
                if req_count > 0:
                    candidates = (
                        db.query(Question)
                        .options(joinedload(Question.versions).joinedload(QuestionVersion.options))
                        .filter(
                            Question.stream_id == es.stream_id,
                            Question.status == QuestionStatus.ACTIVE,
                            Question.is_deleted == False,
                            ~Question.id.in_(selected_ids) if selected_ids else True,
                        )
                        .limit(req_count)
                        .all()
                    )

                    for q in candidates:
                        selected_ids.add(q.id)
                        ver = q.versions[-1] if q.versions else None
                        opts = [
                            {"label": opt.option_label, "text": opt.option_text}
                            for opt in (ver.options if ver else [])
                        ]
                        total_marks += marks
                        simulated_questions.append(
                            PreviewPaperQuestion(
                                sequence_number=len(simulated_questions) + 1,
                                stream_code=stream_code,
                                difficulty=q.difficulty,
                                question_text=ver.question_text if ver else "No version text",
                                marks=marks,
                                negative_marks=neg_marks,
                                options=opts,
                            )
                        )

        return PreviewPaperResponse(
            exam_title=exam.title,
            total_questions=len(simulated_questions),
            total_marks=total_marks,
            questions=simulated_questions,
        )


exam_service = ExamService()
