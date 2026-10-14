"""Student examination attempt, answer autosave, proctoring, and resumption service."""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.models.attempt import Attempt, AttemptQuestion, AttemptOption, Answer, ResumeRequest, AttemptState
from app.models.exam import Exam, ExamStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.question import Question, QuestionVersion, QuestionOption
from app.models.stream import Stream
from app.models.audit import ExamEvent
from app.models.result import ResultPublication
from app.services.exam_service import exam_service


class AttemptService:

    def get_available_exams(
        self, db: Session, student_id: UUID, student_email: str
    ) -> List[Dict[str, Any]]:
        """List exams that the student is actively enrolled in and eligible to start."""
        normalized_email = (student_email or "").strip().lower()

        # Find active enrollments for this candidate
        enrollments = (
            db.query(Enrollment)
            .filter(
                (Enrollment.student_id == student_id)
                | (func.lower(Enrollment.student_email) == normalized_email),
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
            .all()
        )
        enrolled_exam_ids = {e.exam_id for e in enrollments}

        # If student has no enrollments, return empty list
        if not enrolled_exam_ids:
            return []

        # Query published or active exams that the candidate is enrolled in
        exams = (
            db.query(Exam)
            .filter(
                Exam.id.in_(enrolled_exam_ids),
                Exam.status.in_([ExamStatus.PUBLISHED, ExamStatus.IN_PROGRESS]),
                Exam.is_deleted == False,
            )
            .order_by(Exam.start_time.asc())
            .all()
        )

        exam_ids_list = [ex.id for ex in exams]
        pub_records = (
            db.query(ResultPublication.exam_id, ResultPublication.is_published)
            .filter(ResultPublication.exam_id.in_(exam_ids_list))
            .all()
        ) if exam_ids_list else []
        pub_map = {p[0]: p[1] for p in pub_records}

        results = []
        for ex in exams:
            # Check existing attempt
            attempt = (
                db.query(Attempt)
                .filter(Attempt.exam_id == ex.id, Attempt.student_id == student_id)
                .first()
            )

            results.append(
                {
                    "id": ex.id,
                    "title": ex.title,
                    "description": ex.description,
                    "start_time": ex.start_time,
                    "end_time": ex.end_time,
                    "duration_minutes": ex.duration_minutes,
                    "total_questions": ex.total_questions,
                    "instructions": ex.instructions,
                    "entry_cutoff_minutes": ex.entry_cutoff_minutes or 0,
                    "is_enrolled": True,
                    "has_active_attempt": bool(attempt and attempt.state in [AttemptState.IN_PROGRESS, AttemptState.INTERRUPTED]),
                    "attempt_id": attempt.id if attempt else None,
                    "attempt_state": attempt.state if attempt else None,
                    "is_result_published": bool(pub_map.get(ex.id, False)),
                    "default_marks_per_question": float(ex.default_marks_per_question or 1.0),
                    "default_negative_marks": float(ex.default_negative_marks or 0.0),
                    "pass_percentage": ex.pass_percentage,
                }
            )

        return results

    def get_exam_instructions(
        self, db: Session, exam_id: UUID, student_id: UUID, student_email: str
    ) -> Dict[str, Any]:
        """Fetch complete examination details, marking scheme, and stream breakdown for instruction page."""
        exam = (
            db.query(Exam)
            .options(joinedload(Exam.exam_streams))
            .filter(Exam.id == exam_id, Exam.is_deleted == False)
            .first()
        )
        if not exam:
            raise ValueError("Exam not found")

        # Enrollment check
        normalized_email = (student_email or "").strip().lower()
        enrollment = (
            db.query(Enrollment)
            .filter(
                Enrollment.exam_id == exam_id,
                (Enrollment.student_id == student_id)
                | (func.lower(Enrollment.student_email) == normalized_email),
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
            .first()
        )
        if not enrollment:
            raise ValueError("You are not enrolled in this examination")

        existing_attempt = (
            db.query(Attempt)
            .filter(Attempt.exam_id == exam_id, Attempt.student_id == student_id)
            .first()
        )

        streams_data = []
        total_marks = 0.0
        for es in exam.exam_streams:
            st = es.stream
            q_count = es.question_count or 0
            m_per_q = es.marks_per_question if es.marks_per_question is not None else (exam.default_marks_per_question or 1.0)
            neg_marks = es.negative_marks if es.negative_marks is not None else (exam.default_negative_marks or 0.0)
            total_marks += q_count * m_per_q
            streams_data.append({
                "stream_id": str(st.id) if st else str(es.stream_id),
                "name": st.name if st else "General Section",
                "code": st.code if st else "GEN",
                "question_count": q_count,
                "marks_per_question": float(m_per_q),
                "negative_marks": float(neg_marks),
            })

        if not streams_data or total_marks == 0.0:
            total_marks = float((exam.total_questions or 0) * (exam.default_marks_per_question or 1.0))

        return {
            "id": exam.id,
            "title": exam.title,
            "description": exam.description,
            "instructions": exam.instructions,
            "duration_minutes": exam.duration_minutes,
            "total_questions": exam.total_questions,
            "default_marks_per_question": float(exam.default_marks_per_question or 1.0),
            "default_negative_marks": float(exam.default_negative_marks or 0.0),
            "pass_percentage": exam.pass_percentage,
            "entry_cutoff_minutes": exam.entry_cutoff_minutes or 0,
            "start_time": exam.start_time,
            "end_time": exam.end_time,
            "has_active_attempt": bool(existing_attempt and existing_attempt.state in [AttemptState.IN_PROGRESS, AttemptState.INTERRUPTED]),
            "attempt_id": existing_attempt.id if existing_attempt else None,
            "attempt_state": existing_attempt.state if existing_attempt else None,
            "streams": streams_data,
            "total_marks": round(total_marks, 2),
        }

    def start_or_resume_attempt(
        self, db: Session, exam_id: UUID, student_id: UUID, student_email: str
    ) -> Dict[str, Any]:
        """
        Start or resume an attempt.
        Validates schedule, entry cutoffs, and generates or restores paper atomically.
        """
        now = datetime.now(timezone.utc)

        exam = db.query(Exam).filter(Exam.id == exam_id, Exam.is_deleted == False).first()
        if not exam:
            raise ValueError("Exam not found")

        # 1. Enrollment check (case-insensitive email matching)
        normalized_email = (student_email or "").strip().lower()
        enrollment = (
            db.query(Enrollment)
            .filter(
                Enrollment.exam_id == exam_id,
                (Enrollment.student_id == student_id)
                | (func.lower(Enrollment.student_email) == normalized_email),
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
            .first()
        )
        if not enrollment:
            raise ValueError("You are not enrolled in this examination")

        # Link actual student_id if previously enrolled by email with dummy ID
        if enrollment.student_id != student_id:
            enrollment.student_id = student_id
            db.commit()

        # 2. Check if student already has an attempt
        existing_attempt = (
            db.query(Attempt)
            .filter(Attempt.exam_id == exam_id, Attempt.student_id == student_id)
            .first()
        )
        if existing_attempt and existing_attempt.state in [
            AttemptState.SUBMITTED,
            AttemptState.AUTO_SUBMITTED,
        ]:
            raise ValueError("You have already submitted this examination. Re-entry is not permitted.")

        # 3. Schedule & Late Cutoff Checks (only for fresh starts)
        if not existing_attempt:
            if now < exam.start_time:
                diff_sec = int((exam.start_time - now).total_seconds())
                raise ValueError(f"Exam has not started yet. Starts in {diff_sec // 60} minutes.")

            if now > exam.end_time:
                raise ValueError("The scheduled end time for this examination has passed.")

            if exam.entry_cutoff_minutes > 0:
                cutoff_time = exam.start_time + timedelta(minutes=exam.entry_cutoff_minutes)
                if now > cutoff_time:
                    raise ValueError(
                        f"Late entry cutoff ({exam.entry_cutoff_minutes} mins after start) has passed. Access locked."
                    )

        # 4. Generate or load paper
        attempt = exam_service.generate_paper_for_attempt(db, exam_id, student_id)

        # Check overall exam end time and individual attempt deadline expiry
        if now > exam.end_time and attempt.state == AttemptState.IN_PROGRESS:
            attempt.state = AttemptState.AUTO_SUBMITTED
            attempt.submitted_at = now
            db.commit()

        remaining_seconds = max(0, int((attempt.deadline - now).total_seconds()))
        if remaining_seconds <= 0 and attempt.state == AttemptState.IN_PROGRESS:
            attempt.state = AttemptState.AUTO_SUBMITTED
            attempt.submitted_at = now
            db.commit()

        if attempt.state in [AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]:
            raise ValueError("This exam has concluded and your attempt has been submitted.")

        # 5. Load questions, options, and saved answers
        attempt_questions = (
            db.query(AttemptQuestion)
            .options(
                joinedload(AttemptQuestion.options),
                joinedload(AttemptQuestion.answer),
                joinedload(AttemptQuestion.question_version),
            )
            .filter(AttemptQuestion.attempt_id == attempt.id)
            .order_by(AttemptQuestion.sequence_number.asc())
            .all()
        )

        # Batch load Streams in one query
        stream_ids = {aq.stream_id for aq in attempt_questions if aq.stream_id}
        streams_map = {}
        if stream_ids:
            streams = db.query(Stream).filter(Stream.id.in_(stream_ids)).all()
            streams_map = {s.id: s for s in streams}

        # Batch load QuestionOptions in one query
        all_orig_opt_ids = {
            opt.original_option_id
            for aq in attempt_questions
            for opt in aq.options
            if opt.original_option_id
        }
        options_map = {}
        if all_orig_opt_ids:
            options = db.query(QuestionOption).filter(QuestionOption.id.in_(all_orig_opt_ids)).all()
            options_map = {o.id: o for o in options}

        formatted_questions = []
        for aq in attempt_questions:
            stream_obj = streams_map.get(aq.stream_id)
            ver = aq.question_version

            # Sort options by candidate's display order
            sorted_opts = sorted(aq.options, key=lambda o: o.display_order)
            options_arena = []
            for opt in sorted_opts:
                orig_opt = options_map.get(opt.original_option_id)
                options_arena.append(
                    {
                        "id": opt.original_option_id,  # Student submits the chosen option ID
                        "display_label": opt.display_label,
                        "option_text": orig_opt.option_text if orig_opt else "",
                    }
                )

            saved_ans = aq.answer
            formatted_questions.append(
                {
                    "attempt_question_id": aq.id,
                    "sequence_number": aq.sequence_number,
                    "stream_code": stream_obj.code if stream_obj else "STREAM",
                    "stream_name": stream_obj.name if stream_obj else "Subject",
                    "marks": aq.marks,
                    "negative_marks": aq.negative_marks,
                    "question_text": ver.question_text if ver else "",
                    "options": options_arena,
                    "selected_option_id": saved_ans.selected_option_id if saved_ans else None,
                    "is_marked_for_review": saved_ans.is_marked_for_review if saved_ans else False,
                    "is_answered": bool(saved_ans and saved_ans.selected_option_id),
                }
            )

        return {
            "attempt_id": attempt.id,
            "exam_id": exam.id,
            "exam_title": exam.title,
            "instructions": exam.instructions,
            "duration_minutes": exam.duration_minutes,
            "start_time": attempt.start_time,
            "deadline": attempt.deadline,
            "remaining_seconds": remaining_seconds,
            "state": attempt.state,
            "is_locked": attempt.is_locked,
            "lock_reason": attempt.lock_reason,
            "total_questions": len(formatted_questions),
            "questions": formatted_questions,
        }

    def save_answer(
        self,
        db: Session,
        attempt_id: UUID,
        student_id: UUID,
        attempt_question_id: UUID,
        selected_option_id: Optional[UUID],
        is_marked_for_review: bool,
    ) -> Dict[str, Any]:
        """
        Idempotent answer saving.
        Enforces timing, lock status, and handles network retries smoothly.
        """
        now = datetime.now(timezone.utc)

        attempt = db.query(Attempt).filter(Attempt.id == attempt_id, Attempt.student_id == student_id).first()
        if not attempt:
            raise ValueError("Attempt not found or unauthorized")

        if attempt.is_locked:
            raise ValueError(f"Exam attempt is locked: {attempt.lock_reason or 'Interrupted'}. Request resumption approval.")

        if attempt.state in [AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]:
            raise ValueError("Exam has already been submitted")

        if now > attempt.deadline:
            attempt.state = AttemptState.AUTO_SUBMITTED
            attempt.submitted_at = now
            db.commit()
            raise ValueError("Exam deadline has elapsed. Attempt automatically submitted.")

        # Validate attempt_question belongs to this attempt
        att_q = (
            db.query(AttemptQuestion)
            .filter(
                AttemptQuestion.id == attempt_question_id,
                AttemptQuestion.attempt_id == attempt.id,
            )
            .first()
        )
        if not att_q:
            raise ValueError("Question does not belong to this attempt")

        # Validate selected_option_id if provided
        if selected_option_id:
            valid_opt = (
                db.query(AttemptOption)
                .filter(
                    AttemptOption.attempt_question_id == att_q.id,
                    AttemptOption.original_option_id == selected_option_id,
                )
                .first()
            )
            if not valid_opt:
                raise ValueError("Selected option is invalid for this question")

        # Upsert Answer
        answer = db.query(Answer).filter(Answer.attempt_question_id == att_q.id).first()
        if answer:
            answer.selected_option_id = selected_option_id
            answer.is_marked_for_review = is_marked_for_review
            answer.save_count += 1
            answer.updated_at = now
        else:
            answer = Answer(
                attempt_question_id=att_q.id,
                selected_option_id=selected_option_id,
                is_marked_for_review=is_marked_for_review,
                save_count=1,
            )
            db.add(answer)

        attempt.last_heartbeat = now
        db.commit()
        db.refresh(answer)

        return {
            "success": True,
            "attempt_question_id": att_q.id,
            "selected_option_id": answer.selected_option_id,
            "is_marked_for_review": answer.is_marked_for_review,
            "save_count": answer.save_count,
            "updated_at": answer.updated_at,
        }

    def heartbeat(self, db: Session, attempt_id: UUID, student_id: UUID) -> Dict[str, Any]:
        """Candidate heartbeat: updates last_heartbeat and checks deadline."""
        now = datetime.now(timezone.utc)
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id, Attempt.student_id == student_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        attempt.last_heartbeat = now
        remaining_seconds = max(0, int((attempt.deadline - now).total_seconds()))

        if remaining_seconds <= 0 and attempt.state == AttemptState.IN_PROGRESS:
            attempt.state = AttemptState.AUTO_SUBMITTED
            attempt.submitted_at = now

        db.commit()

        return {
            "remaining_seconds": remaining_seconds,
            "state": attempt.state,
            "is_locked": attempt.is_locked,
            "lock_reason": attempt.lock_reason,
        }

    def log_event(
        self,
        db: Session,
        attempt_id: UUID,
        student_id: UUID,
        event_type: str,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record proctoring events (fullscreen exits, tab switches)."""
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id, Attempt.student_id == student_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        event = ExamEvent(
            attempt_id=attempt.id,
            student_id=student_id,
            event_type=event_type,
            details=details or {},
            ip_address=ip_address,
        )
        db.add(event)
        db.flush()

        # Count violations
        if event_type in ["fullscreen_exit", "tab_switch"]:
            violation_count = (
                db.query(func.count(ExamEvent.id))
                .filter(
                    ExamEvent.attempt_id == attempt.id,
                    ExamEvent.event_type.in_(["fullscreen_exit", "tab_switch"]),
                )
                .scalar()
                or 0
            )

            # Auto-lock immediately on 1st violation or forced lock
            is_forced = bool(details and details.get("force_lock"))
            if violation_count >= 1 or is_forced:
                attempt.is_locked = True
                attempt.state = AttemptState.INTERRUPTED
                attempt.lock_reason = f"Security Violation: Focus-loss or fullscreen exit detected ({event_type.replace('_', ' ')})"

        db.commit()
        return {"logged": True, "is_locked": attempt.is_locked, "lock_reason": attempt.lock_reason}

    def submit_attempt(self, db: Session, attempt_id: UUID, student_id: UUID) -> bool:
        """Submit candidate attempt with strict state and lock guards."""
        now = datetime.now(timezone.utc)
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id, Attempt.student_id == student_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        # Guard: Attempt already submitted (idempotent success)
        if attempt.state in [AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]:
            return True

        # Guard: Attempt is locked
        if attempt.is_locked:
            raise ValueError(f"Exam attempt is locked: {attempt.lock_reason or 'Security/Connectivity lock'}. Request resumption approval.")

        attempt.state = AttemptState.SUBMITTED
        attempt.submitted_at = now
        db.commit()

        # Auto-score submitted attempt immediately to avoid scoring backlogs
        try:
            from app.services.result_service import result_service
            result_service.score_attempt(db, attempt.id)
        except Exception:
            # Fall back to background monitor / results view auto-scoring
            pass

        return True

    def request_resumption(
        self, db: Session, attempt_id: UUID, student_id: UUID, reason: str
    ) -> ResumeRequest:
        """Submit student request for admin resumption unlock."""
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id, Attempt.student_id == student_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        req = ResumeRequest(
            attempt_id=attempt.id,
            reason=reason,
            requested_at=datetime.now(timezone.utc),
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        return req

    def get_admin_attempts(
        self, db: Session, exam_id: Optional[UUID] = None, locked_only: bool = False
    ) -> List[Dict[str, Any]]:
        """List attempts for administrative review and resumption approval."""
        query = (
            db.query(Attempt)
            .options(
                joinedload(Attempt.exam),
                joinedload(Attempt.attempt_questions).joinedload(AttemptQuestion.answer),
            )
        )
        if exam_id:
            query = query.filter(Attempt.exam_id == exam_id)
        if locked_only:
            query = query.filter((Attempt.is_locked == True) | (Attempt.state == AttemptState.INTERRUPTED))

        attempts = query.order_by(Attempt.start_time.desc()).all()
        results = []

        for att in attempts:
            enr = db.query(Enrollment).filter(Enrollment.exam_id == att.exam_id, Enrollment.student_id == att.student_id).first()
            pending_resume = (
                db.query(ResumeRequest)
                .filter(ResumeRequest.attempt_id == att.id, ResumeRequest.decision.is_(None))
                .order_by(ResumeRequest.requested_at.desc())
                .first()
            )

            total_q = len(att.attempt_questions)
            answered_q = sum(1 for aq in att.attempt_questions if aq.answer and aq.answer.selected_option_id)

            results.append(
                {
                    "attempt_id": att.id,
                    "exam_id": att.exam_id,
                    "exam_title": att.exam.title if att.exam else "Exam",
                    "student_id": att.student_id,
                    "student_email": enr.student_email if enr else "Student",
                    "state": att.state,
                    "start_time": att.start_time,
                    "deadline": att.deadline,
                    "submitted_at": att.submitted_at,
                    "is_locked": att.is_locked,
                    "lock_reason": att.lock_reason,
                    "answered_count": answered_q,
                    "total_questions": total_q,
                    "pending_resume_reason": pending_resume.reason if pending_resume else None,
                }
            )

        return results

    def approve_resumption(
        self, db: Session, attempt_id: UUID, reviewer_id: UUID
    ) -> bool:
        """
        Approve resumption of an interrupted attempt.
        Crucial Rule: The original deadline remains unchanged; approval does not silently grant extra time.
        """
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        attempt.is_locked = False
        attempt.state = AttemptState.IN_PROGRESS
        attempt.lock_reason = None
        attempt.last_heartbeat = datetime.now(timezone.utc)

        # Update pending requests
        pending_requests = (
            db.query(ResumeRequest)
            .filter(ResumeRequest.attempt_id == attempt.id, ResumeRequest.decision.is_(None))
            .all()
        )
        now = datetime.now(timezone.utc)
        for pr in pending_requests:
            pr.decision = "approved"
            pr.reviewer_id = reviewer_id
            pr.decided_at = now

        db.commit()
        return True

    def reject_resumption(
        self, db: Session, attempt_id: UUID, reviewer_id: UUID
    ) -> bool:
        """Reject resumption request for an interrupted attempt."""
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        pending_requests = (
            db.query(ResumeRequest)
            .filter(ResumeRequest.attempt_id == attempt.id, ResumeRequest.decision.is_(None))
            .all()
        )
        now = datetime.now(timezone.utc)
        for pr in pending_requests:
            pr.decision = "rejected"
            pr.reviewer_id = reviewer_id
            pr.decided_at = now

        db.commit()
        return True


attempt_service = AttemptService()
