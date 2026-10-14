"""Exam scoring engine, result calculation, analytics, and publication service."""

import io
import csv
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.models.result import Result, ResultDetail, ResultPublication
from app.models.attempt import Attempt, AttemptQuestion, AttemptOption, Answer, AttemptState
from app.models.exam import Exam, ExamStatus
from app.models.enrollment import Enrollment
from app.models.question import QuestionVersion, QuestionOption
from app.models.stream import Stream
from app.schemas.result import PublicationConfigRequest


class ResultService:

    def score_attempt(self, db: Session, attempt_id: UUID) -> Result:
        """
        Deterministically calculate scores for a submitted attempt.
        Evaluates answers against QuestionVersion snapshots and saves stream breakdowns.
        """
        attempt = db.query(Attempt).filter(Attempt.id == attempt_id).first()
        if not attempt:
            raise ValueError("Attempt not found")

        exam = db.query(Exam).filter(Exam.id == attempt.exam_id).first()
        if not exam:
            raise ValueError("Exam not found")

        # Load attempt questions with answers, options, and version options
        attempt_questions = (
            db.query(AttemptQuestion)
            .options(
                joinedload(AttemptQuestion.answer),
                joinedload(AttemptQuestion.options),
                joinedload(AttemptQuestion.question_version).joinedload(QuestionVersion.options),
            )
            .filter(AttemptQuestion.attempt_id == attempt.id)
            .order_by(AttemptQuestion.sequence_number.asc())
            .all()
        )

        total_marks = 0.0
        total_obtained = 0.0
        correct_count = 0
        incorrect_count = 0
        unanswered_count = 0

        # Stream aggregation map: stream_id -> {total_marks, obtained_marks, correct, incorrect, unanswered}
        stream_map: Dict[UUID, Dict[str, Any]] = {}

        for aq in attempt_questions:
            st_id = aq.stream_id
            if st_id not in stream_map:
                stream_map[st_id] = {
                    "total_marks": 0.0,
                    "obtained_marks": 0.0,
                    "correct": 0,
                    "incorrect": 0,
                    "unanswered": 0,
                }

            q_marks = aq.marks or 1.0
            q_neg = aq.negative_marks or 0.0

            total_marks += q_marks
            stream_map[st_id]["total_marks"] += q_marks

            sel_opt_id = aq.answer.selected_option_id if aq.answer else None
            ver = aq.question_version
            correct_opt = next((opt for opt in (ver.options if ver else []) if opt.is_correct), None)

            if not sel_opt_id:
                # Unanswered
                unanswered_count += 1
                stream_map[st_id]["unanswered"] += 1
            elif correct_opt and sel_opt_id == correct_opt.id:
                # Correct
                correct_count += 1
                total_obtained += q_marks
                stream_map[st_id]["correct"] += 1
                stream_map[st_id]["obtained_marks"] += q_marks
            else:
                # Incorrect
                incorrect_count += 1
                total_obtained -= q_neg
                stream_map[st_id]["incorrect"] += 1
                stream_map[st_id]["obtained_marks"] -= q_neg

        # Total questions
        total_questions = len(attempt_questions)
        percentage = max(0.0, round((total_obtained / total_marks * 100), 2)) if total_marks > 0 else 0.0

        # Pass/fail evaluation
        passed = None
        if exam.pass_percentage is not None:
            passed = percentage >= exam.pass_percentage

        # Upsert Result
        result = db.query(Result).filter(Result.attempt_id == attempt.id).first()
        if not result:
            result = Result(
                attempt_id=attempt.id,
                student_id=attempt.student_id,
                exam_id=exam.id,
            )
            db.add(result)
            db.flush()

        result.total_marks = total_marks
        result.total_obtained = total_obtained
        result.total_questions = total_questions
        result.correct_count = correct_count
        result.incorrect_count = incorrect_count
        result.unanswered_count = unanswered_count
        result.percentage = percentage
        result.passed = passed

        # Re-create ResultDetails for each stream
        db.query(ResultDetail).filter(ResultDetail.result_id == result.id).delete()
        db.flush()

        for s_id, s_stats in stream_map.items():
            rd = ResultDetail(
                result_id=result.id,
                stream_id=s_id,
                total_marks=s_stats["total_marks"],
                obtained_marks=s_stats["obtained_marks"],
                correct_count=s_stats["correct"],
                incorrect_count=s_stats["incorrect"],
                unanswered_count=s_stats["unanswered"],
            )
            db.add(rd)

        db.commit()
        db.refresh(result)
        return result

    def score_exam(self, db: Session, exam_id: UUID) -> int:
        """Batch score all submitted attempts for an exam."""
        attempts = (
            db.query(Attempt)
            .filter(
                Attempt.exam_id == exam_id,
                Attempt.state.in_([AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]),
            )
            .all()
        )
        scored_count = 0
        for att in attempts:
            self.score_attempt(db, att.id)
            scored_count += 1
        return scored_count

    def _score_unscored_attempts(self, db: Session, exam_id: UUID) -> int:
        """Score only submitted attempts that don't yet have a Result record."""
        existing_result_attempt_ids = (
            db.query(Result.attempt_id)
            .filter(Result.exam_id == exam_id)
        )
        unscored = (
            db.query(Attempt)
            .filter(
                Attempt.exam_id == exam_id,
                Attempt.state.in_([AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]),
                ~Attempt.id.in_(existing_result_attempt_ids),
            )
            .all()
        )
        for att in unscored:
            self.score_attempt(db, att.id)
        return len(unscored)

    def get_exam_results(self, db: Session, exam_id: UUID) -> List[Dict[str, Any]]:
        """List all candidate results for an exam with ranking and stream breakdowns."""
        # Only score attempts that don't yet have results (avoids re-scoring all on every view)
        self._score_unscored_attempts(db, exam_id)

        results = (
            db.query(Result)
            .options(
                joinedload(Result.attempt),
                joinedload(Result.details).joinedload(ResultDetail.stream),
            )
            .filter(Result.exam_id == exam_id)
            .order_by(Result.total_obtained.desc(), Result.percentage.desc())
            .all()
        )

        formatted: List[Dict[str, Any]] = []
        for rank_idx, r in enumerate(results, start=1):
            enr = (
                db.query(Enrollment)
                .filter(Enrollment.exam_id == exam_id, Enrollment.student_id == r.student_id)
                .first()
            )
            student_email = enr.student_email if enr else "Student"

            stream_details = []
            for dt in r.details:
                stream_details.append(
                    {
                        "stream_id": dt.stream_id,
                        "stream_code": dt.stream.code if dt.stream else "STREAM",
                        "stream_name": dt.stream.name if dt.stream else "Stream",
                        "total_marks": dt.total_marks,
                        "obtained_marks": dt.obtained_marks,
                        "correct_count": dt.correct_count,
                        "incorrect_count": dt.incorrect_count,
                        "unanswered_count": dt.unanswered_count,
                    }
                )

            formatted.append(
                {
                    "result_id": r.id,
                    "attempt_id": r.attempt_id,
                    "student_id": r.student_id,
                    "student_email": student_email,
                    "exam_id": r.exam_id,
                    "rank": rank_idx,
                    "total_marks": r.total_marks,
                    "total_obtained": r.total_obtained,
                    "percentage": r.percentage,
                    "passed": r.passed,
                    "total_questions": r.total_questions,
                    "correct_count": r.correct_count,
                    "incorrect_count": r.incorrect_count,
                    "unanswered_count": r.unanswered_count,
                    "stream_details": stream_details,
                }
            )

        return formatted

    def get_candidate_result(
        self, db: Session, exam_id: UUID, student_id: UUID, is_admin: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch candidate's result.
        Enforces controlled publication gatekeeper for students.
        """
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        if not exam:
            raise ValueError("Exam not found")

        pub = self.get_or_create_publication(db, exam_id)

        # Gatekeeper: non-admins cannot access unreleased results
        if not is_admin and not pub.is_published:
            raise ValueError("Results for this examination have not been published yet.")

        # Find or calculate result
        result = db.query(Result).filter(Result.exam_id == exam_id, Result.student_id == student_id).first()
        if not result:
            # Check for submitted attempt
            attempt = (
                db.query(Attempt)
                .filter(
                    Attempt.exam_id == exam_id,
                    Attempt.student_id == student_id,
                    Attempt.state.in_([AttemptState.SUBMITTED, AttemptState.AUTO_SUBMITTED]),
                )
                .first()
            )
            if not attempt:
                raise ValueError("No submitted exam attempt found for this candidate.")
            result = self.score_attempt(db, attempt.id)

        # Calculate rank efficiently using count of higher-scoring results
        higher_count = (
            db.query(func.count(Result.id))
            .filter(
                Result.exam_id == exam_id,
                Result.total_obtained > result.total_obtained,
            )
            .scalar()
            or 0
        )
        rank = higher_count + 1

        # Load stream details
        details = (
            db.query(ResultDetail)
            .options(joinedload(ResultDetail.stream))
            .filter(ResultDetail.result_id == result.id)
            .all()
        )
        stream_details = [
            {
                "stream_id": dt.stream_id,
                "stream_code": dt.stream.code if dt.stream else "STREAM",
                "stream_name": dt.stream.name if dt.stream else "Stream",
                "total_marks": dt.total_marks,
                "obtained_marks": dt.obtained_marks,
                "correct_count": dt.correct_count,
                "incorrect_count": dt.incorrect_count,
                "unanswered_count": dt.unanswered_count,
            }
            for dt in details
        ]

        # Load question breakdown if permitted
        questions_review = None
        can_view_answers = is_admin or pub.show_answers

        if can_view_answers:
            attempt_questions = (
                db.query(AttemptQuestion)
                .options(
                    joinedload(AttemptQuestion.answer),
                    joinedload(AttemptQuestion.options),
                    joinedload(AttemptQuestion.question_version).joinedload(QuestionVersion.options),
                )
                .filter(AttemptQuestion.attempt_id == result.attempt_id)
                .order_by(AttemptQuestion.sequence_number.asc())
                .all()
            )

            questions_review = []
            for aq in attempt_questions:
                ver = aq.question_version
                stream_obj = db.query(Stream).filter(Stream.id == aq.stream_id).first()
                sel_opt_id = aq.answer.selected_option_id if aq.answer else None
                correct_opt = next((opt for opt in (ver.options if ver else []) if opt.is_correct), None)

                # Find labels & texts
                sel_opt = next((opt for opt in (ver.options if ver else []) if opt.id == sel_opt_id), None)
                sel_label = None
                sel_text = None
                if sel_opt:
                    # Find candidate display label from attempt_options
                    att_opt = next((ao for ao in aq.options if ao.original_option_id == sel_opt.id), None)
                    sel_label = att_opt.display_label if att_opt else sel_opt.option_label
                    sel_text = sel_opt.option_text

                corr_label = None
                corr_text = None
                if correct_opt:
                    corr_att = next((ao for ao in aq.options if ao.original_option_id == correct_opt.id), None)
                    corr_label = corr_att.display_label if corr_att else correct_opt.option_label
                    corr_text = correct_opt.option_text

                is_correct = bool(sel_opt_id and correct_opt and sel_opt_id == correct_opt.id)

                show_explanation = is_admin or pub.show_explanations
                questions_review.append(
                    {
                        "sequence_number": aq.sequence_number,
                        "question_text": ver.question_text if ver else "",
                        "stream_name": stream_obj.name if stream_obj else "Subject",
                        "marks": aq.marks,
                        "negative_marks": aq.negative_marks,
                        "selected_option_label": sel_label,
                        "selected_option_text": sel_text,
                        "correct_option_label": corr_label,
                        "correct_option_text": corr_text,
                        "is_correct": is_correct if sel_opt_id else False,
                        "explanation": ver.explanation if (show_explanation and ver) else None,
                    }
                )

        enr = (
            db.query(Enrollment)
            .filter(Enrollment.exam_id == exam_id, Enrollment.student_id == student_id)
            .first()
        )

        return {
            "result_id": result.id,
            "attempt_id": result.attempt_id,
            "student_id": result.student_id,
            "student_email": enr.student_email if enr else "Candidate",
            "exam_id": result.exam_id,
            "exam_title": exam.title,
            "rank": rank,
            "total_marks": result.total_marks,
            "total_obtained": result.total_obtained,
            "percentage": result.percentage,
            "passed": result.passed,
            "total_questions": result.total_questions,
            "correct_count": result.correct_count,
            "incorrect_count": result.incorrect_count,
            "unanswered_count": result.unanswered_count,
            "stream_details": stream_details,
            "questions": questions_review,
        }

    def get_exam_analytics(self, db: Session, exam_id: UUID) -> Dict[str, Any]:
        """Aggregate statistical metrics, class performance, and stream averages."""
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        if not exam:
            raise ValueError("Exam not found")

        self._score_unscored_attempts(db, exam_id)
        results = db.query(Result).filter(Result.exam_id == exam_id).all()

        total_candidates = len(results)
        if total_candidates == 0:
            return {
                "exam_id": exam.id,
                "exam_title": exam.title,
                "total_candidates": 0,
                "average_score": 0.0,
                "highest_score": 0.0,
                "lowest_score": 0.0,
                "average_percentage": 0.0,
                "pass_percentage": exam.pass_percentage,
                "pass_rate": 0.0,
                "stream_averages": [],
                "score_brackets": {
                    "0-20%": 0,
                    "21-40%": 0,
                    "41-60%": 0,
                    "61-80%": 0,
                    "81-100%": 0,
                },
            }

        scores = [r.total_obtained for r in results]
        percentages = [r.percentage for r in results]

        avg_score = round(sum(scores) / total_candidates, 2)
        high_score = round(max(scores), 2)
        low_score = round(min(scores), 2)
        avg_pct = round(sum(percentages) / total_candidates, 2)

        passed_candidates = sum(1 for r in results if r.passed is True)
        pass_rate = (
            round((passed_candidates / total_candidates * 100), 2)
            if exam.pass_percentage is not None
            else 100.0
        )

        # Brackets
        brackets = {
            "0-20%": sum(1 for p in percentages if p <= 20.0),
            "21-40%": sum(1 for p in percentages if 20.0 < p <= 40.0),
            "41-60%": sum(1 for p in percentages if 40.0 < p <= 60.0),
            "61-80%": sum(1 for p in percentages if 60.0 < p <= 80.0),
            "81-100%": sum(1 for p in percentages if p > 80.0),
        }

        # Stream averages
        stream_details = (
            db.query(ResultDetail)
            .options(joinedload(ResultDetail.stream))
            .join(Result, ResultDetail.result_id == Result.id)
            .filter(Result.exam_id == exam_id)
            .all()
        )

        st_map: Dict[UUID, Dict[str, Any]] = {}
        for dt in stream_details:
            sid = dt.stream_id
            if sid not in st_map:
                st_map[sid] = {
                    "stream_id": sid,
                    "stream_code": dt.stream.code if dt.stream else "STREAM",
                    "stream_name": dt.stream.name if dt.stream else "Stream",
                    "total_obtained": 0.0,
                    "max_marks": dt.total_marks,
                    "count": 0,
                }
            st_map[sid]["total_obtained"] += dt.obtained_marks
            st_map[sid]["count"] += 1

        stream_averages = []
        for s_data in st_map.values():
            c = s_data["count"] or 1
            avg_ob = round(s_data["total_obtained"] / c, 2)
            max_m = s_data["max_marks"] or 1.0
            avg_p = round((avg_ob / max_m * 100), 2) if max_m > 0 else 0.0
            stream_averages.append(
                {
                    "stream_id": s_data["stream_id"],
                    "stream_code": s_data["stream_code"],
                    "stream_name": s_data["stream_name"],
                    "average_score": avg_ob,
                    "max_marks": max_m,
                    "average_percentage": avg_p,
                }
            )

        return {
            "exam_id": exam.id,
            "exam_title": exam.title,
            "total_candidates": total_candidates,
            "average_score": avg_score,
            "highest_score": high_score,
            "lowest_score": low_score,
            "average_percentage": avg_pct,
            "pass_percentage": exam.pass_percentage,
            "pass_rate": pass_rate,
            "stream_averages": stream_averages,
            "score_brackets": brackets,
        }

    def get_or_create_publication(self, db: Session, exam_id: UUID) -> ResultPublication:
        """Fetch or initialize publication record for an exam."""
        pub = db.query(ResultPublication).filter(ResultPublication.exam_id == exam_id).first()
        if not pub:
            pub = ResultPublication(
                exam_id=exam_id,
                is_published=False,
                show_answers=False,
                show_explanations=False,
            )
            db.add(pub)
            db.commit()
            db.refresh(pub)
        return pub

    def update_publication(
        self, db: Session, exam_id: UUID, config: PublicationConfigRequest, published_by_id: UUID
    ) -> ResultPublication:
        """Update publication rules; scores all submitted attempts upon publication."""
        pub = self.get_or_create_publication(db, exam_id)
        now = datetime.now(timezone.utc)

        if config.is_published and not pub.is_published:
            pub.published_at = now
            pub.published_by_id = published_by_id
            # Ensure every submitted paper is evaluated
            self.score_exam(db, exam_id)

        pub.is_published = config.is_published
        pub.show_answers = config.show_answers
        pub.show_explanations = config.show_explanations

        db.commit()
        db.refresh(pub)
        return pub

    def generate_csv_export(self, db: Session, exam_id: UUID) -> str:
        """Export ranked candidate results to CSV format."""
        results = self.get_exam_results(db, exam_id)
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        exam_title = exam.title if exam else "Exam"

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            [
                "Rank",
                "Student Email",
                "Exam Title",
                "Score Obtained",
                "Total Marks",
                "Percentage",
                "Status",
                "Correct",
                "Incorrect",
                "Unanswered",
                "Streams Breakdown",
            ]
        )

        for r in results:
            streams_str = "; ".join(
                f"{sd['stream_code']}: {sd['obtained_marks']}/{sd['total_marks']}"
                for sd in r["stream_details"]
            )
            status_str = "Passed" if r["passed"] is True else "Failed" if r["passed"] is False else "Completed"

            writer.writerow(
                [
                    r["rank"],
                    r["student_email"],
                    exam_title,
                    r["total_obtained"],
                    r["total_marks"],
                    f"{r['percentage']}%",
                    status_str,
                    r["correct_count"],
                    r["incorrect_count"],
                    r["unanswered_count"],
                    streams_str,
                ]
            )

        return output.getvalue()


result_service = ResultService()
