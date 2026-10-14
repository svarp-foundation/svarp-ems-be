"""Question management and versioning service."""

import io
import csv
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
import openpyxl
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import or_, func, String, case, distinct
from app.models.question import Question, QuestionVersion, QuestionOption, QuestionStatus, DifficultyLevel
from app.models.stream import Stream
from app.schemas.question import QuestionCreate, QuestionUpdate, RowError, ImportResultResponse


class QuestionService:

    def _format_question_response(self, question: Question, db: Session) -> Dict[str, Any]:
        """Helper to format a Question model into a schema dictionary."""
        # Find current version efficiently
        current_ver = None
        if question.versions:
            if question.current_version_id:
                current_ver = next((v for v in question.versions if v.id == question.current_version_id), None)
            if not current_ver:
                current_ver = question.versions[-1]
            total_versions = len(question.versions)
        elif question.current_version_id:
            current_ver = (
                db.query(QuestionVersion)
                .options(joinedload(QuestionVersion.options))
                .filter(QuestionVersion.id == question.current_version_id)
                .first()
            )
            total_versions = (
                db.query(func.count(QuestionVersion.id))
                .filter(QuestionVersion.question_id == question.id)
                .scalar()
                or 1
            )
        else:
            total_versions = 1

        return {
            "id": question.id,
            "stream_id": question.stream_id,
            "stream_code": question.stream.code if question.stream else None,
            "stream_name": question.stream.name if question.stream else None,
            "status": question.status,
            "difficulty": question.difficulty,
            "tags": question.tags or [],
            "current_version": current_ver,
            "total_versions": total_versions,
            "created_at": question.created_at,
            "updated_at": question.updated_at,
        }

    def create_question(self, db: Session, data: QuestionCreate) -> Dict[str, Any]:
        """Create a question with version 1 and 4 options."""
        # Validate stream
        stream = db.query(Stream).filter(Stream.id == data.stream_id, Stream.is_deleted == False).first()
        if not stream:
            raise ValueError(f"Stream with ID '{data.stream_id}' not found")

        # 1. Master Question record
        question = Question(
            stream_id=data.stream_id,
            status=data.status,
            difficulty=data.difficulty,
            tags=data.tags or [],
        )
        db.add(question)
        db.flush()

        # 2. Immutable QuestionVersion record (v1)
        version = QuestionVersion(
            question_id=question.id,
            version_number=1,
            question_text=data.question_text,
            explanation=data.explanation,
        )
        db.add(version)
        db.flush()

        # 3. 4 MCQ Options
        for opt in data.options:
            option_row = QuestionOption(
                version_id=version.id,
                option_label=opt.option_label.upper(),
                option_text=opt.option_text,
                is_correct=opt.is_correct,
            )
            db.add(option_row)

        question.current_version_id = version.id
        db.commit()
        db.refresh(question)

        return self._format_question_response(question, db)

    def get_question(self, db: Session, question_id: UUID) -> Optional[Dict[str, Any]]:
        """Get single question by ID with current version."""
        question = (
            db.query(Question)
            .options(joinedload(Question.stream))
            .filter(Question.id == question_id, Question.is_deleted == False)
            .first()
        )
        if not question:
            return None
        return self._format_question_response(question, db)

    def get_questions(
        self,
        db: Session,
        stream_id: Optional[UUID] = None,
        difficulty: Optional[DifficultyLevel] = None,
        status: Optional[QuestionStatus] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Query questions with optional filtering, search, and pagination."""
        query = (
            db.query(Question)
            .options(
                joinedload(Question.stream),
                selectinload(Question.versions).joinedload(QuestionVersion.options),
            )
            .filter(Question.is_deleted == False)
        )

        if stream_id:
            query = query.filter(Question.stream_id == stream_id)
        if difficulty:
            query = query.filter(Question.difficulty == difficulty)
        if status:
            query = query.filter(Question.status == status)

        if search:
            search_pattern = f"%{search.strip()}%"
            # Search in tags (JSONB string) or joins to QuestionVersion text
            query = query.outerjoin(
                QuestionVersion, Question.current_version_id == QuestionVersion.id
            ).filter(
                or_(
                    QuestionVersion.question_text.ilike(search_pattern),
                    Question.tags.cast(String).ilike(search_pattern) if hasattr(Question.tags, "cast") else False,
                )
            ).distinct()

        total = query.count() if not search else (query.with_entities(func.count(distinct(Question.id))).scalar() or 0)
        questions = query.order_by(Question.created_at.desc()).offset(skip).limit(limit).all()

        results = [self._format_question_response(q, db) for q in questions]
        return results, total

    def get_stream_question_stats(self, db: Session) -> List[Dict[str, Any]]:
        """Aggregated question counts and difficulty breakdown by stream."""
        streams = db.query(Stream).filter(Stream.is_deleted == False).order_by(Stream.name).all()
        counts_query = (
            db.query(
                Question.stream_id,
                func.count(Question.id).label("total"),
                func.sum(case((Question.difficulty == DifficultyLevel.EASY, 1), else_=0)).label("easy"),
                func.sum(case((Question.difficulty == DifficultyLevel.MEDIUM, 1), else_=0)).label("medium"),
                func.sum(case((Question.difficulty == DifficultyLevel.HARD, 1), else_=0)).label("hard"),
                func.sum(case((Question.status == QuestionStatus.ACTIVE, 1), else_=0)).label("active"),
            )
            .filter(Question.is_deleted == False)
            .group_by(Question.stream_id)
            .all()
        )
        counts_map = {
            row.stream_id: {
                "total": row.total,
                "easy": int(row.easy or 0),
                "medium": int(row.medium or 0),
                "hard": int(row.hard or 0),
                "active": int(row.active or 0),
            }
            for row in counts_query
        }
        res = []
        for s in streams:
            st_counts = counts_map.get(s.id, {"total": 0, "easy": 0, "medium": 0, "hard": 0, "active": 0})
            res.append({
                "stream_id": str(s.id),
                "stream_code": s.code,
                "stream_name": s.name,
                "description": s.description or "",
                "is_active": s.is_active,
                **st_counts,
            })
        return res

    def update_question(
        self, db: Session, question_id: UUID, data: QuestionUpdate
    ) -> Optional[Dict[str, Any]]:
        """
        Update question.
        If question_text, explanation, or options change, creates a NEW QuestionVersion.
        Metadata changes (difficulty, stream_id, status, tags) update the master question record directly.
        """
        question = (
            db.query(Question)
            .filter(Question.id == question_id, Question.is_deleted == False)
            .first()
        )
        if not question:
            return None

        # Check metadata updates
        if data.stream_id is not None:
            stream = db.query(Stream).filter(Stream.id == data.stream_id, Stream.is_deleted == False).first()
            if not stream:
                raise ValueError(f"Stream with ID '{data.stream_id}' not found")
            question.stream_id = data.stream_id

        if data.difficulty is not None:
            question.difficulty = data.difficulty

        if data.status is not None:
            question.status = data.status

        if data.tags is not None:
            question.tags = data.tags

        # Check if content needs a new immutable version
        content_changed = (
            data.question_text is not None
            or data.explanation is not None
            or data.options is not None
        )

        if content_changed:
            current_ver = (
                db.query(QuestionVersion)
                .options(joinedload(QuestionVersion.options))
                .filter(QuestionVersion.id == question.current_version_id)
                .first()
            )

            # Determine next version number
            last_ver_num = (
                db.query(func.max(QuestionVersion.version_number))
                .filter(QuestionVersion.question_id == question.id)
                .scalar()
                or 1
            )
            new_ver_num = last_ver_num + 1

            new_text = data.question_text if data.question_text is not None else (current_ver.question_text if current_ver else "")
            new_explanation = data.explanation if data.explanation is not None else (current_ver.explanation if current_ver else None)

            new_version = QuestionVersion(
                question_id=question.id,
                version_number=new_ver_num,
                question_text=new_text,
                explanation=new_explanation,
            )
            db.add(new_version)
            db.flush()

            # Options
            if data.options is not None:
                for opt in data.options:
                    db.add(
                        QuestionOption(
                            version_id=new_version.id,
                            option_label=opt.option_label.upper(),
                            option_text=opt.option_text,
                            is_correct=opt.is_correct,
                        )
                    )
            elif current_ver:
                # Copy current options over to new version
                for old_opt in current_ver.options:
                    db.add(
                        QuestionOption(
                            version_id=new_version.id,
                            option_label=old_opt.option_label,
                            option_text=old_opt.option_text,
                            is_correct=old_opt.is_correct,
                        )
                    )

            question.current_version_id = new_version.id

        db.commit()
        db.refresh(question)
        return self._format_question_response(question, db)

    def get_question_versions(self, db: Session, question_id: UUID) -> List[QuestionVersion]:
        """Get all immutable versions for a question."""
        return (
            db.query(QuestionVersion)
            .options(joinedload(QuestionVersion.options))
            .filter(QuestionVersion.question_id == question_id)
            .order_by(QuestionVersion.version_number.desc())
            .all()
        )

    def delete_question(self, db: Session, question_id: UUID) -> bool:
        """Soft delete question and mark status archived."""
        question = (
            db.query(Question)
            .filter(Question.id == question_id, Question.is_deleted == False)
            .first()
        )
        if not question:
            return False

        question.is_deleted = True
        question.status = QuestionStatus.ARCHIVED
        db.commit()
        return True

    def import_questions(
        self, db: Session, file_bytes: bytes, filename: str
    ) -> ImportResultResponse:
        """
        Validate and import MCQs from CSV or XLSX.
        Strict pre-flight validation: if ANY row fails, rolls back completely!
        """
        is_excel = filename.endswith(".xlsx") or filename.endswith(".xls")
        rows: List[Dict[str, str]] = []

        try:
            if is_excel:
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
                sheet = wb.active
                headers = [str(cell.value).strip().lower() if cell.value else "" for cell in sheet[1]]
                for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                    if not any(row):
                        continue
                    row_dict = {}
                    for col_idx, h in enumerate(headers):
                        if h and col_idx < len(row):
                            val = row[col_idx]
                            row_dict[h] = str(val).strip() if val is not None else ""
                    rows.append((row_idx, row_dict))
            else:
                text_content = file_bytes.decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text_content))
                headers = [h.strip().lower() for h in (reader.fieldnames or [])]
                for row_idx, raw_row in enumerate(reader, start=2):
                    cleaned_row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}
                    if any(cleaned_row.values()):
                        rows.append((row_idx, cleaned_row))
        except Exception as e:
            return ImportResultResponse(
                success=False,
                total_rows=0,
                imported_count=0,
                error_count=1,
                errors=[RowError(row_number=1, field="file", message=f"Failed to parse file: {str(e)}")],
            )

        if not rows:
            return ImportResultResponse(
                success=False,
                total_rows=0,
                imported_count=0,
                error_count=1,
                errors=[RowError(row_number=1, field="file", message="File contains no question rows")],
            )

        # Pre-load all active streams map by uppercase code
        active_streams = db.query(Stream).filter(Stream.is_deleted == False).all()
        stream_map = {s.code.upper(): s.id for s in active_streams}

        errors: List[RowError] = []
        valid_items: List[Dict[str, Any]] = []

        # Validate every row
        for row_num, row_data in rows:
            start_err_count = len(errors)
            stream_code = row_data.get("stream_code", "").upper()
            if not stream_code:
                errors.append(RowError(row_number=row_num, field="stream_code", message="Stream code is required"))
            elif stream_code not in stream_map:
                errors.append(
                    RowError(
                        row_number=row_num,
                        field="stream_code",
                        message=f"Stream code '{stream_code}' does not exist",
                    )
                )

            diff_str = row_data.get("difficulty", "").lower()
            if not diff_str or diff_str not in ["easy", "medium", "hard"]:
                errors.append(
                    RowError(
                        row_number=row_num,
                        field="difficulty",
                        message=f"Difficulty must be 'easy', 'medium', or 'hard'; got '{diff_str}'",
                    )
                )

            q_text = row_data.get("question_text", "")
            if not q_text:
                errors.append(RowError(row_number=row_num, field="question_text", message="Question text cannot be empty"))

            opt_a = row_data.get("option_a", "")
            opt_b = row_data.get("option_b", "")
            opt_c = row_data.get("option_c", "")
            opt_d = row_data.get("option_d", "")

            if not opt_a:
                errors.append(RowError(row_number=row_num, field="option_a", message="Option A is required"))
            if not opt_b:
                errors.append(RowError(row_number=row_num, field="option_b", message="Option B is required"))
            if not opt_c:
                errors.append(RowError(row_number=row_num, field="option_c", message="Option C is required"))
            if not opt_d:
                errors.append(RowError(row_number=row_num, field="option_d", message="Option D is required"))

            correct_label = row_data.get("correct_option", "").upper()
            if not correct_label or correct_label not in ["A", "B", "C", "D"]:
                errors.append(
                    RowError(
                        row_number=row_num,
                        field="correct_option",
                        message=f"Correct option must be A, B, C, or D; got '{correct_label}'",
                    )
                )

            tags_raw = row_data.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

            # Only append to valid_items if this row passed all validations
            if len(errors) == start_err_count:
                valid_items.append(
                    {
                        "stream_id": stream_map.get(stream_code),
                        "difficulty": diff_str,
                        "question_text": q_text,
                        "explanation": row_data.get("explanation", None),
                        "tags": tags,
                        "options": [
                            {"label": "A", "text": opt_a, "is_correct": correct_label == "A"},
                            {"label": "B", "text": opt_b, "is_correct": correct_label == "B"},
                            {"label": "C", "text": opt_c, "is_correct": correct_label == "C"},
                            {"label": "D", "text": opt_d, "is_correct": correct_label == "D"},
                        ],
                    }
                )

        # Strict integrity check: abort if any error found
        if errors:
            return ImportResultResponse(
                success=False,
                total_rows=len(rows),
                imported_count=0,
                error_count=len(errors),
                errors=errors,
            )

        # Batch insert within a single transaction
        try:
            for item in valid_items:
                q = Question(
                    stream_id=item["stream_id"],
                    status=QuestionStatus.ACTIVE,
                    difficulty=DifficultyLevel(item["difficulty"]),
                    tags=item["tags"],
                )
                db.add(q)
                db.flush()

                ver = QuestionVersion(
                    question_id=q.id,
                    version_number=1,
                    question_text=item["question_text"],
                    explanation=item["explanation"],
                )
                db.add(ver)
                db.flush()

                for opt in item["options"]:
                    db.add(
                        QuestionOption(
                            version_id=ver.id,
                            option_label=opt["label"],
                            option_text=opt["text"],
                            is_correct=opt["is_correct"],
                        )
                    )

                q.current_version_id = ver.id

            db.commit()

            return ImportResultResponse(
                success=True,
                total_rows=len(rows),
                imported_count=len(valid_items),
                error_count=0,
                errors=[],
            )
        except Exception as e:
            db.rollback()
            return ImportResultResponse(
                success=False,
                total_rows=len(rows),
                imported_count=0,
                error_count=1,
                errors=[RowError(row_number=0, field="transaction", message=f"Database commit error: {str(e)}")],
            )


question_service = QuestionService()
