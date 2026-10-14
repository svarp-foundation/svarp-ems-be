import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import datetime, timezone, timedelta
from app.core.database import SessionLocal
from app.models.stream import Stream
from app.models.question import Question, QuestionVersion, QuestionOption, DifficultyLevel
from app.models.exam import Exam, ExamStatus, ExamStream
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.attempt import Attempt, AttemptState
from app.services.attempt_service import attempt_service
from app.services.exam_service import exam_service


def run_phase_4_tests():
    db = SessionLocal()
    print("=== STARTING PHASE 4 INTEGRATION & INVARIANT TESTS ===")

    try:
        # 1. Setup Stream
        stream = db.query(Stream).filter(Stream.code == "TEST_P4").first()
        if not stream:
            stream = Stream(code="TEST_P4", name="Phase 4 Proctoring Stream", is_active=True)
            db.add(stream)
            db.commit()
            db.refresh(stream)
        print(f"[1/8] Stream verified: {stream.code} ({stream.id})")

        # 2. Setup Questions
        from app.models.question import QuestionStatus
        questions = []
        for i in range(1, 4):
            q_text = f"Sample Question {i} for Phase 4 Examination Arena?"
            qv_exist = db.query(QuestionVersion).filter(QuestionVersion.question_text == q_text).first()
            if not qv_exist:
                q = Question(
                    stream_id=stream.id,
                    status=QuestionStatus.ACTIVE,
                    difficulty=DifficultyLevel.EASY,
                    tags=["phase4"],
                )
                db.add(q)
                db.flush()

                qv = QuestionVersion(
                    question_id=q.id,
                    version_number=1,
                    question_text=q_text,
                    explanation=f"Confidential explanation for Question {i}",
                )
                db.add(qv)
                db.flush()

                q.current_version_id = qv.id

                for opt_idx, (lbl, text, is_corr) in enumerate(
                    [("A", "Choice Alpha", True), ("B", "Choice Beta", False), ("C", "Choice Gamma", False), ("D", "Choice Delta", False)]
                ):
                    opt = QuestionOption(
                        version_id=qv.id,
                        option_label=lbl,
                        option_text=text,
                        is_correct=is_corr,
                    )
                    db.add(opt)
                db.commit()
            else:
                q = qv_exist.question
            questions.append(q)
        print(f"[2/8] 3 Questions verified in stream {stream.code}")

        # 3. Setup Published Exam
        now = datetime.now(timezone.utc)
        exam = Exam(
            title="Phase 4 Proctoring & Resumption Certification Exam",
            description="Testing arena, autosave, security locks, and admin resumption",
            start_time=now - timedelta(minutes=5),  # Started 5 mins ago
            end_time=now + timedelta(hours=2),
            duration_minutes=60,
            status=ExamStatus.PUBLISHED,
            is_deleted=False,
            total_questions=2,
            default_marks_per_question=2.0,
            shuffle_questions=True,
            shuffle_options=True,
        )
        db.add(exam)
        db.flush()

        es = ExamStream(
            exam_id=exam.id,
            stream_id=stream.id,
            question_count=2,
            marks_per_question=2.0,
            negative_marks=0.5,
        )
        db.add(es)
        db.commit()
        db.refresh(exam)
        print(f"[3/8] Exam created & published: {exam.title} ({exam.id})")

        # 4. Enroll Student
        student_id = uuid.uuid4()
        student_email = f"candidate_{uuid.uuid4().hex[:6]}@svarp.org"
        admin_id = uuid.uuid4()

        enrollment = Enrollment(
            exam_id=exam.id,
            student_id=student_id,
            student_email=student_email,
            status=EnrollmentStatus.ENROLLED,
        )
        db.add(enrollment)
        db.commit()
        print(f"[4/8] Student enrolled: {student_email}")

        # 5. Start Attempt & Verify Security Invariants
        start_data = attempt_service.start_or_resume_attempt(db, exam.id, student_id, student_email)
        attempt_id = start_data["attempt_id"]
        assert len(start_data["questions"]) == 2, f"Expected 2 questions, got {len(start_data['questions'])}"
        
        # Verify confidentiality invariant: arena MUST NOT reveal is_correct or explanation
        for q in start_data["questions"]:
            assert "is_correct" not in q, "SECURITY VIOLATION: is_correct found in question arena response!"
            assert "explanation" not in q, "SECURITY VIOLATION: explanation leaked in question arena response!"
            for opt in q["options"]:
                assert "is_correct" not in opt, "SECURITY VIOLATION: is_correct leaked in option arena response!"
        print(f"[5/8] Paper initialized: Attempt {attempt_id}. Invariant verified: answers & explanations 100% hidden.")

        # 6. Test Idempotent Autosave
        first_q = start_data["questions"][0]
        first_opt = first_q["options"][0]["id"]
        second_opt = first_q["options"][1]["id"]

        ans1 = attempt_service.save_answer(
            db=db,
            attempt_id=attempt_id,
            student_id=student_id,
            attempt_question_id=first_q["attempt_question_id"],
            selected_option_id=first_opt,
            is_marked_for_review=False,
        )
        assert ans1["save_count"] == 1, f"Expected save_count=1, got {ans1['save_count']}"
        assert ans1["selected_option_id"] == first_opt

        # Update to second option
        ans2 = attempt_service.save_answer(
            db=db,
            attempt_id=attempt_id,
            student_id=student_id,
            attempt_question_id=first_q["attempt_question_id"],
            selected_option_id=second_opt,
            is_marked_for_review=True,
        )
        assert ans2["save_count"] == 2, f"Expected save_count=2, got {ans2['save_count']}"
        assert ans2["selected_option_id"] == second_opt
        assert ans2["is_marked_for_review"] is True
        print(f"[6/8] Autosave verified: idempotent updates, save count incremented to {ans2['save_count']}")

        # 7. Proctoring Violations & Interruption Lock
        # Trigger 3 tab switches
        for switch_no in range(1, 4):
            log_res = attempt_service.log_event(
                db=db,
                attempt_id=attempt_id,
                student_id=student_id,
                event_type="tab_switch",
                details={"switch_num": switch_no},
            )

        assert log_res["is_locked"] is True, "Attempt should be locked after 3 tab switches!"
        print(f"[7/8] Interruption Lock triggered: {log_res['lock_reason']}")

        # Verify answer save is strictly rejected while locked
        locked_attempt_threw = False
        try:
            attempt_service.save_answer(
                db=db,
                attempt_id=attempt_id,
                student_id=student_id,
                attempt_question_id=first_q["attempt_question_id"],
                selected_option_id=first_opt,
                is_marked_for_review=False,
            )
        except ValueError as e:
            locked_attempt_threw = True
            assert "locked" in str(e).lower()
        assert locked_attempt_threw, "save_answer must fail while attempt is locked!"

        # Candidate submits resumption request
        resume_req = attempt_service.request_resumption(
            db=db,
            attempt_id=attempt_id,
            student_id=student_id,
            reason="Accidental alt-tab during notification popup",
        )
        assert resume_req.reason == "Accidental alt-tab during notification popup"

        # Admin lists attempts and approves
        admin_attempts = attempt_service.get_admin_attempts(db, exam_id=exam.id, locked_only=True)
        assert len(admin_attempts) >= 1
        found_target = next((a for a in admin_attempts if a["attempt_id"] == attempt_id), None)
        assert found_target is not None
        assert found_target["pending_resume_reason"] == "Accidental alt-tab during notification popup"

        # Record deadline before approval
        deadline_before = db.query(Attempt).filter(Attempt.id == attempt_id).first().deadline

        # Admin approves
        attempt_service.approve_resumption(db, attempt_id=attempt_id, reviewer_id=admin_id)

        # Invariant check: original deadline MUST NOT be changed
        deadline_after = db.query(Attempt).filter(Attempt.id == attempt_id).first().deadline
        assert deadline_before == deadline_after, "TIMING INVARIANT VIOLATION: Resumption altered the deadline!"

        att_after = db.query(Attempt).filter(Attempt.id == attempt_id).first()
        assert att_after.is_locked is False, "Attempt should be unlocked after approval"
        assert att_after.state == AttemptState.IN_PROGRESS, "Attempt state should be IN_PROGRESS after approval"
        print(f"[8/8] Resumption approval verified! Timing Invariant preserved: deadline {deadline_before} untouched.")

        # Candidate completes and submits
        attempt_service.submit_attempt(db, attempt_id=attempt_id, student_id=student_id)
        att_final = db.query(Attempt).filter(Attempt.id == attempt_id).first()
        assert att_final.state == AttemptState.SUBMITTED
        print("✓ Final submission verified: AttemptState.SUBMITTED")

        print("\n🎉 ALL PHASE 4 INTEGRATION TESTS PASSED WITH 100% INVARIANT COMPLIANCE!")

    finally:
        db.close()


if __name__ == "__main__":
    run_phase_4_tests()
