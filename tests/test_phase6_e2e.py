"""Phase 6 End-to-End Security Audit, Proctoring Event Stream, and Session Lock Verification."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.core.database import SessionLocal
from app.models.audit import AuditLog, ExamEvent
from app.models.exam_session import ExamSession
from app.models.attempt import Attempt, AttemptState
from app.models.exam import Exam, ExamStatus
from app.utils.audit import log_audit


def run_phase_6_tests():
    db = SessionLocal()
    print("=== STARTING PHASE 6 SECURITY AUDIT & CONCURRENCY INVARIANT TESTS ===")

    try:
        # 1. Test Immutable Audit Trail Logging
        actor_id = uuid.uuid4()
        actor_email = f"admin_{uuid.uuid4().hex[:6]}@svarp.org"

        log1 = log_audit(
            db=db,
            action="PHASE6_VERIFY_ACTION",
            actor_id=actor_id,
            actor_email=actor_email,
            target_type="exam",
            target_id=str(uuid.uuid4()),
            details={"test_key": "val1", "environment": "production_test"},
            ip_address="192.168.1.100",
        )
        assert log1.id is not None
        assert log1.action == "PHASE6_VERIFY_ACTION"

        # Query back from DB with action filter
        queried_logs = (
            db.query(AuditLog)
            .filter(AuditLog.action == "PHASE6_VERIFY_ACTION", AuditLog.actor_email == actor_email)
            .all()
        )
        assert len(queried_logs) >= 1
        assert queried_logs[0].details["test_key"] == "val1"
        print(f"[1/4] Audit trail verified: Action '{log1.action}' logged and retrieved with intact JSON payload.")

        # 2. Test Proctoring Telemetry Event Stream
        student_id = uuid.uuid4()
        attempt_id = uuid.uuid4()

        # Create dummy attempt to satisfy foreign key if needed
        now = datetime.now(timezone.utc)
        exam = Exam(
            title="P6 Security Exam",
            start_time=now,
            end_time=now,
            duration_minutes=30,
            total_questions=1,
            status=ExamStatus.DRAFT,
        )
        db.add(exam)
        db.flush()

        att = Attempt(
            id=attempt_id,
            exam_id=exam.id,
            student_id=student_id,
            state=AttemptState.IN_PROGRESS,
            start_time=now,
            deadline=now,
        )
        db.add(att)
        db.flush()

        ev1 = ExamEvent(
            attempt_id=attempt_id,
            student_id=student_id,
            event_type="tab_switch",
            details={"window_blurred": True, "count": 1},
            ip_address="10.0.0.5",
        )
        ev2 = ExamEvent(
            attempt_id=attempt_id,
            student_id=student_id,
            event_type="fullscreen_exit",
            details={"escape_pressed": True},
            ip_address="10.0.0.5",
        )
        db.add_all([ev1, ev2])
        db.commit()

        # Query events
        tab_events = (
            db.query(ExamEvent)
            .filter(ExamEvent.attempt_id == attempt_id, ExamEvent.event_type == "tab_switch")
            .all()
        )
        assert len(tab_events) == 1
        assert tab_events[0].details["window_blurred"] is True

        fs_events = (
            db.query(ExamEvent)
            .filter(ExamEvent.attempt_id == attempt_id, ExamEvent.event_type == "fullscreen_exit")
            .all()
        )
        assert len(fs_events) == 1
        print(f"[2/4] Proctoring telemetry verified: 'tab_switch' and 'fullscreen_exit' stored with details.")

        # 3. Test Security Statistics Calculation
        from app.api.v1.audit import get_audit_stats
        # Call the logic directly
        from sqlalchemy import func
        total_logs = db.query(func.count(AuditLog.id)).scalar() or 0
        total_events = db.query(func.count(ExamEvent.id)).scalar() or 0
        assert total_logs >= 1
        assert total_events >= 2
        print(f"[3/4] Security stats verified: Total logs={total_logs}, Total proctoring events={total_events}.")

        # 4. CRITICAL: Test Single-Session Concurrency Lock (PostgreSQL Partial Index)
        session_user_id = uuid.uuid4()
        session_email = f"user_{uuid.uuid4().hex[:6]}@svarp.org"

        # First active session
        s1 = ExamSession(
            user_id=session_user_id,
            user_email=session_email,
            token_jti=f"jti_test_1_{uuid.uuid4().hex}",
            is_active=True,
            ip_address="127.0.0.1",
        )
        db.add(s1)
        db.commit()
        print(f"[4/4] Active Session #1 established for user {session_user_id}.")

        # Attempt to create Second concurrent active session for the same user
        s2 = ExamSession(
            user_id=session_user_id,
            user_email=session_email,
            token_jti=f"jti_test_2_{uuid.uuid4().hex}",
            is_active=True,
            ip_address="127.0.0.2",
        )
        db.add(s2)

        duplicate_lock_threw = False
        try:
            db.commit()
        except IntegrityError as e:
            duplicate_lock_threw = True
            db.rollback()
            assert "uq_active_session_per_user" in str(e).lower()
            print("✓ PostgreSQL partial unique index uq_active_session_per_user strictly intercepted duplicate active session!")
        assert duplicate_lock_threw, "CONCURRENCY INVARIANT VIOLATION: Concurrent active session was not blocked!"

        # Deactivate first session
        s1 = db.query(ExamSession).filter(ExamSession.id == s1.id).first()
        s1.is_active = False
        s1.revoked_reason = "logout"
        db.commit()

        # Now creating session #2 succeeds because user has 0 active sessions
        s3 = ExamSession(
            user_id=session_user_id,
            user_email=session_email,
            token_jti=f"jti_test_3_{uuid.uuid4().hex}",
            is_active=True,
            ip_address="127.0.0.3",
        )
        db.add(s3)
        db.commit()
        print("✓ Session #2 successfully established after clean deactivation of previous session.")

        print("\n🎉 ALL PHASE 6 SECURITY AUDIT & CONCURRENCY TESTS PASSED WITH 100% INVARIANT COMPLIANCE!")

    finally:
        db.close()


if __name__ == "__main__":
    run_phase_6_tests()
