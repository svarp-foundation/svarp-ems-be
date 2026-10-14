import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import datetime, timezone, timedelta
from app.core.database import SessionLocal
from app.models.stream import Stream
from app.models.question import Question, QuestionVersion, QuestionOption, DifficultyLevel, QuestionStatus
from app.models.exam import Exam, ExamStatus, ExamStream
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.attempt import Attempt, AttemptQuestion, AttemptOption, Answer, AttemptState
from app.models.result import Result, ResultDetail, ResultPublication
from app.schemas.result import PublicationConfigRequest
from app.services.result_service import result_service
from app.services.attempt_service import attempt_service


def run_phase_5_tests():
    db = SessionLocal()
    print("=== STARTING PHASE 5 SCORING & RESULTS INVARIANT TESTS ===")

    try:
        # 1. Setup Stream
        stream = db.query(Stream).filter(Stream.code == "TEST_P5").first()
        if not stream:
            stream = Stream(code="TEST_P5", name="Phase 5 Scoring Stream", is_active=True)
            db.add(stream)
            db.commit()
            db.refresh(stream)
        print(f"[1/8] Stream verified: {stream.code} ({stream.id})")

        # 2. Setup 3 Questions with explicit correct options
        questions_meta = [
            {"text": "Phase 5 Q1: What is 2 + 2?", "marks": 2.0, "neg": 0.5, "correct": "B", "explanation": "2 + 2 = 4."},
            {"text": "Phase 5 Q2: Capital of France?", "marks": 3.0, "neg": 1.0, "correct": "C", "explanation": "Paris is the capital."},
            {"text": "Phase 5 Q3: Boiling point of water in Celsius?", "marks": 1.0, "neg": 0.25, "correct": "A", "explanation": "Water boils at 100°C."},
        ]

        question_objs = []
        for q_data in questions_meta:
            qv_exist = db.query(QuestionVersion).filter(QuestionVersion.question_text == q_data["text"]).first()
            if not qv_exist:
                q = Question(
                    stream_id=stream.id,
                    status=QuestionStatus.ACTIVE,
                    difficulty=DifficultyLevel.EASY,
                    tags=["phase5"],
                )
                db.add(q)
                db.flush()

                qv = QuestionVersion(
                    question_id=q.id,
                    version_number=1,
                    question_text=q_data["text"],
                    explanation=q_data["explanation"],
                )
                db.add(qv)
                db.flush()
                q.current_version_id = qv.id

                for lbl in ["A", "B", "C", "D"]:
                    is_c = (lbl == q_data["correct"])
                    opt = QuestionOption(
                        version_id=qv.id,
                        option_label=lbl,
                        option_text=f"Option {lbl} text",
                        is_correct=is_c,
                    )
                    db.add(opt)
                db.commit()
                question_objs.append((q, qv))
            else:
                question_objs.append((qv_exist.question, qv_exist))

        print(f"[2/8] 3 Verification questions ready with deterministic answer keys.")

        # 3. Setup Exam
        now = datetime.now(timezone.utc)
        exam = Exam(
            title="Phase 5 Scoring & Publication Certification Exam",
            description="Testing deterministic scoring, negative marks, and publication gatekeepers",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            duration_minutes=60,
            status=ExamStatus.PUBLISHED,
            is_deleted=False,
            total_questions=3,
            default_marks_per_question=2.0,
            pass_percentage=50.0,  # Passing threshold = 50%
        )
        db.add(exam)
        db.flush()

        es = ExamStream(
            exam_id=exam.id,
            stream_id=stream.id,
            question_count=3,
        )
        db.add(es)
        db.commit()
        db.refresh(exam)
        print(f"[3/8] Exam created with 50% pass threshold: {exam.title}")

        # 4. Setup Candidate Attempt with deliberate response pattern:
        # Q1 (marks=2.0): Answered CORRECTLY (+2.0)
        # Q2 (marks=3.0, neg=1.0): Answered WRONGLY (-1.0)
        # Q3 (marks=1.0, neg=0.25): Left UNANSWERED (0.0)
        student_id = uuid.uuid4()
        student_email = f"candidate_p5_{uuid.uuid4().hex[:6]}@svarp.org"
        admin_id = uuid.uuid4()

        enrollment = Enrollment(
            exam_id=exam.id,
            student_id=student_id,
            student_email=student_email,
            status=EnrollmentStatus.ENROLLED,
        )
        db.add(enrollment)
        db.flush()

        attempt = Attempt(
            exam_id=exam.id,
            student_id=student_id,
            state=AttemptState.SUBMITTED,
            start_time=now - timedelta(minutes=45),
            deadline=now + timedelta(minutes=15),
            submitted_at=now - timedelta(minutes=5),
            is_locked=False,
        )
        db.add(attempt)
        db.flush()

        # Create Attempt Questions
        for idx, (q, qv) in enumerate(question_objs, start=1):
            q_meta = questions_meta[idx - 1]
            aq = AttemptQuestion(
                attempt_id=attempt.id,
                question_version_id=qv.id,
                stream_id=stream.id,
                sequence_number=idx,
                marks=q_meta["marks"],
                negative_marks=q_meta["neg"],
            )
            db.add(aq)
            db.flush()

            # Options
            opts = db.query(QuestionOption).filter(QuestionOption.version_id == qv.id).all()
            for opt_idx, orig_opt in enumerate(opts, start=1):
                ao = AttemptOption(
                    attempt_question_id=aq.id,
                    original_option_id=orig_opt.id,
                    display_order=opt_idx,
                    display_label=orig_opt.option_label,
                )
                db.add(ao)
            db.flush()

            # Assign Answers
            correct_opt = next((o for o in opts if o.is_correct), None)
            wrong_opt = next((o for o in opts if not o.is_correct), None)

            if idx == 1:
                # Answer Correctly
                ans = Answer(attempt_question_id=aq.id, selected_option_id=correct_opt.id)
                db.add(ans)
            elif idx == 2:
                # Answer Wrongly
                ans = Answer(attempt_question_id=aq.id, selected_option_id=wrong_opt.id)
                db.add(ans)
            else:
                # Unanswered
                ans = Answer(attempt_question_id=aq.id, selected_option_id=None)
                db.add(ans)

        db.commit()
        print(f"[4/8] Candidate attempt submitted with Q1=Correct, Q2=Wrong, Q3=Unanswered.")

        # 5. Evaluate Scoring Engine & Verify Invariant
        result = result_service.score_attempt(db, attempt.id)

        # Expected: Total Marks = 2.0 + 3.0 + 1.0 = 6.0
        # Expected Obtained = 2.0 - 1.0 + 0 = 1.0
        # Expected Percentage = 1.0 / 6.0 * 100 = 16.67%
        # Expected Passed = False (16.67% < 50.0%)
        assert result.total_marks == 6.0, f"Expected 6.0 total marks, got {result.total_marks}"
        assert result.total_obtained == 1.0, f"Expected 1.0 obtained, got {result.total_obtained}"
        assert result.correct_count == 1, f"Expected 1 correct, got {result.correct_count}"
        assert result.incorrect_count == 1, f"Expected 1 incorrect, got {result.incorrect_count}"
        assert result.unanswered_count == 1, f"Expected 1 unanswered, got {result.unanswered_count}"
        assert result.percentage == 16.67, f"Expected 16.67%, got {result.percentage}"
        assert result.passed is False, f"Expected passed=False, got {result.passed}"

        # Test Reproducibility Invariant: Scoring again must yield exact same results
        result2 = result_service.score_attempt(db, attempt.id)
        assert result2.total_obtained == 1.0
        assert result2.percentage == 16.67
        print(f"[5/8] Deterministic scoring verified: 1.0/6.0 marks (16.67%, Failed). Reproducibility confirmed.")

        # 6. Test Controlled Result Publication Gatekeeper
        # Case A: Results NOT published -> student access MUST fail with 403/ValueError
        gatekeeper_blocked = False
        try:
            result_service.get_candidate_result(db, exam.id, student_id, is_admin=False)
        except ValueError as e:
            gatekeeper_blocked = True
            assert "not been published" in str(e).lower()
        assert gatekeeper_blocked, "Gatekeeper MUST block student from accessing unreleased results!"
        print("[6/8] Gatekeeper verified: Student blocked from viewing unpublished results.")

        # Case B: Admin releases results WITHOUT answers or explanations
        pub_config = PublicationConfigRequest(
            is_published=True,
            show_answers=False,
            show_explanations=False,
        )
        result_service.update_publication(db, exam.id, pub_config, published_by_id=admin_id)

        cand_res = result_service.get_candidate_result(db, exam.id, student_id, is_admin=False)
        assert cand_res["total_obtained"] == 1.0
        assert cand_res["percentage"] == 16.67
        assert cand_res["questions"] is None, "Answers and explanations must remain hidden when show_answers=False!"

        # Case C: Admin enables answers and explanations release
        pub_config_full = PublicationConfigRequest(
            is_published=True,
            show_answers=True,
            show_explanations=True,
        )
        result_service.update_publication(db, exam.id, pub_config_full, published_by_id=admin_id)

        cand_res_full = result_service.get_candidate_result(db, exam.id, student_id, is_admin=False)
        assert cand_res_full["questions"] is not None
        assert len(cand_res_full["questions"]) == 3
        assert cand_res_full["questions"][0]["explanation"] is not None
        assert cand_res_full["questions"][0]["correct_option_label"] == "B"
        print("[7/8] Publication controls verified: Controlled release of scores, answer keys, and explanations.")

        # 7. Test Analytics Aggregation & CSV Export
        analytics = result_service.get_exam_analytics(db, exam.id)
        assert analytics["total_candidates"] >= 1
        assert analytics["average_score"] == 1.0
        assert analytics["pass_rate"] == 0.0  # 0% passed because 16.67% < 50%
        assert "0-20%" in analytics["score_brackets"]

        csv_text = result_service.generate_csv_export(db, exam.id)
        assert "Rank,Student Email" in csv_text
        assert student_email in csv_text
        assert "Failed" in csv_text
        print("[8/8] Analytics aggregation & CSV export verified successfully.")

        print("\n🎉 ALL PHASE 5 SCORING & RESULTS TESTS PASSED WITH 100% INVARIANT COMPLIANCE!")

    finally:
        db.close()


if __name__ == "__main__":
    run_phase_5_tests()
