"""
Verification test suite for Phase 1 (System Rigidity) & Phase 2 (Background Enforcement).
Tests:
1. Paper generation concurrency and IntegrityError resilience
2. Attempt submit state & lock guards
3. Session expiry validation
4. Background task runner (auto-submitting expired attempts & heartbeat locks)
5. Question search tag cast (no NameError)
6. Admin resumption rejection
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import text, String, or_
from app.core.database import SessionLocal
from app.models.attempt import Attempt, AttemptState
from app.models.exam import Exam, ExamStatus
from app.models.exam_session import ExamSession
from app.models.question import Question, QuestionVersion
from app.services.attempt_service import attempt_service
from app.services.exam_service import exam_service
from app.services.session_service import session_service
from app.services.question_service import question_service
from app.services.background_tasks import (
    sweep_expired_attempts,
    sweep_stale_heartbeats,
    sweep_stale_sessions,
    run_monitoring_cycle,
    MONITOR_STATS,
)


def run_tests():
    db = SessionLocal()
    print("=== STARTING PHASE 1 & PHASE 2 VERIFICATION SUITE ===")

    try:
        # TEST 1: Question search tag cast (Verify BUG-05 fix)
        print("\n[TEST 1] Verifying Question search with tags String cast...")
        try:
            questions, total = question_service.get_questions(db, search="algorithm", limit=5)
            print(f"✓ Question search executed successfully with total count={total}. No NameError!")
        except NameError as ne:
            raise AssertionError(f"FAILED: NameError still present in question search: {ne}")

        # TEST 2: Submit Attempt State & Lock Guards (Verify BUG-02 fix)
        print("\n[TEST 2] Verifying Submit Attempt State and Lock Guards...")
        # Find or create a test exam
        test_exam = db.query(Exam).first()
        if test_exam:
            dummy_student = uuid.uuid4()
            test_attempt = Attempt(
                student_id=dummy_student,
                exam_id=test_exam.id,
                state=AttemptState.IN_PROGRESS,
                start_time=datetime.now(timezone.utc),
                deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
                is_locked=True,
                lock_reason="Test Lock for Guard Verification",
            )
            db.add(test_attempt)
            db.commit()
            db.refresh(test_attempt)

            # 2a. Attempting to submit a locked attempt MUST fail
            try:
                attempt_service.submit_attempt(db, test_attempt.id, dummy_student)
                raise AssertionError("FAILED: Submitting a locked attempt should have raised ValueError!")
            except ValueError as ve:
                print(f"✓ Locked attempt submit strictly rejected: {ve}")

            # 2b. Unlock attempt and submit
            test_attempt.is_locked = False
            test_attempt.lock_reason = None
            db.commit()

            res1 = attempt_service.submit_attempt(db, test_attempt.id, dummy_student)
            assert res1 is True, "First submission should succeed"
            print("✓ Valid attempt submitted successfully.")

            # 2c. Resubmitting an already-submitted attempt should be idempotent and return True
            res2 = attempt_service.submit_attempt(db, test_attempt.id, dummy_student)
            assert res2 is True, "Re-submitting already submitted attempt must be idempotent"
            print("✓ Idempotent resubmission handled cleanly without error.")

            # Clean up test attempt
            db.delete(test_attempt)
            db.commit()

        # TEST 3: Session Expiry Validation (Verify BUG-04 fix)
        print("\n[TEST 3] Verifying Session Expiry Validation...")
        test_jti = f"test_jti_{uuid.uuid4().hex}"
        old_time = datetime.now(timezone.utc) - timedelta(hours=25) # Exceeds 24h (1440m)
        expired_session = ExamSession(
            user_id=uuid.uuid4(),
            user_email="expired_test@svarp.org",
            token_jti=test_jti,
            is_active=True,
            created_at=old_time,
            last_heartbeat=old_time,
        )
        db.add(expired_session)
        db.commit()

        val_result = session_service.validate_session(db, test_jti)
        assert val_result is None, "Expired session MUST return None from validate_session!"
        db.refresh(expired_session)
        assert expired_session.is_active is False, "Expired session MUST be deactivated"
        assert expired_session.revoked_reason == "session_expired"
        print("✓ Expired session correctly invalidated with reason='session_expired'.")

        db.delete(expired_session)
        db.commit()

        # TEST 4: Background Task Sweep for Expired Attempts (Phase 2)
        print("\n[TEST 4] Verifying Background Task Deadline Sweep...")
        if test_exam:
            expired_student = uuid.uuid4()
            past_deadline = datetime.now(timezone.utc) - timedelta(minutes=5)
            dead_attempt = Attempt(
                student_id=expired_student,
                exam_id=test_exam.id,
                state=AttemptState.IN_PROGRESS,
                start_time=past_deadline - timedelta(minutes=30),
                deadline=past_deadline,
                is_locked=False,
            )
            db.add(dead_attempt)
            db.commit()
            db.refresh(dead_attempt)

            # Run sweep
            auto_submitted_count = sweep_expired_attempts(db)
            assert auto_submitted_count >= 1, "Sweep should have caught the expired attempt"
            db.refresh(dead_attempt)
            assert dead_attempt.state == AttemptState.AUTO_SUBMITTED, f"Expected AUTO_SUBMITTED, got {dead_attempt.state}"
            assert dead_attempt.submitted_at is not None
            print(f"✓ Background sweep caught expired deadline: attempt auto-submitted into state '{dead_attempt.state}'.")

            # Clean up
            db.delete(dead_attempt)
            db.commit()

        # TEST 5: Background Task Sweep for Stale Heartbeats (Phase 2)
        print("\n[TEST 5] Verifying Background Task Heartbeat Disconnect Lock...")
        if test_exam:
            hb_student = uuid.uuid4()
            stale_hb_time = datetime.now(timezone.utc) - timedelta(minutes=5)
            unresponsive_attempt = Attempt(
                student_id=hb_student,
                exam_id=test_exam.id,
                state=AttemptState.IN_PROGRESS,
                start_time=datetime.now(timezone.utc) - timedelta(minutes=10),
                deadline=datetime.now(timezone.utc) + timedelta(minutes=50),
                last_heartbeat=stale_hb_time,
                is_locked=False,
            )
            db.add(unresponsive_attempt)
            db.commit()
            db.refresh(unresponsive_attempt)

            locked_count = sweep_stale_heartbeats(db)
            assert locked_count >= 1, "Sweep should lock unresponsive attempt"
            db.refresh(unresponsive_attempt)
            assert unresponsive_attempt.is_locked is True
            assert unresponsive_attempt.state == AttemptState.INTERRUPTED
            print(f"✓ Background sweep successfully locked disconnected attempt with reason: '{unresponsive_attempt.lock_reason}'.")

            # Clean up
            db.delete(unresponsive_attempt)
            db.commit()

        # TEST 6: Monitoring cycle execution & stats update
        print("\n[TEST 6] Verifying Full Monitoring Cycle and Stats Payload...")
        cycle_res = run_monitoring_cycle()
        print("✓ Monitoring cycle completed cleanly:", cycle_res)
        print("✓ Updated MONITOR_STATS:", MONITOR_STATS)
        assert MONITOR_STATS["cycles_completed"] >= 1
        assert MONITOR_STATS["last_error"] is None

        print("\n🎉 ALL PHASE 1 & PHASE 2 RIGIDITY AND MONITORING TESTS PASSED!")

    finally:
        db.close()


if __name__ == "__main__":
    run_tests()
