"""Stream management service."""

from typing import Optional, List
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.stream import Stream
from app.schemas.stream import StreamCreate, StreamUpdate


class StreamService:

    def create_stream(self, db: Session, data: StreamCreate) -> Stream:
        """Create a new stream."""
        stream = Stream(
            name=data.name,
            code=data.code.upper(),
            description=data.description,
        )
        db.add(stream)
        try:
            db.commit()
            db.refresh(stream)
            return stream
        except IntegrityError:
            db.rollback()
            raise ValueError(f"Stream with name '{data.name}' or code '{data.code}' already exists")

    def get_stream(self, db: Session, stream_id: UUID) -> Optional[Stream]:
        """Get a stream by ID."""
        return (
            db.query(Stream)
            .filter(Stream.id == stream_id, Stream.is_deleted == False)
            .first()
        )

    def get_streams(
        self, db: Session, active_only: bool = True
    ) -> List[Stream]:
        """List all streams, optionally filtered to active only."""
        query = db.query(Stream).filter(Stream.is_deleted == False)
        if active_only:
            query = query.filter(Stream.is_active == True)
        return query.order_by(Stream.name).all()

    def update_stream(
        self, db: Session, stream_id: UUID, data: StreamUpdate
    ) -> Optional[Stream]:
        """Update a stream's fields."""
        stream = self.get_stream(db, stream_id)
        if not stream:
            return None

        update_data = data.model_dump(exclude_unset=True)
        if "code" in update_data and update_data["code"]:
            update_data["code"] = update_data["code"].upper()

        for field, value in update_data.items():
            setattr(stream, field, value)

        try:
            db.commit()
            db.refresh(stream)
            return stream
        except IntegrityError:
            db.rollback()
            raise ValueError("Stream with this name or code already exists")

    def delete_stream(self, db: Session, stream_id: UUID) -> bool:
        """Soft-delete a stream."""
        stream = self.get_stream(db, stream_id)
        if not stream:
            return False
        stream.is_deleted = True
        stream.is_active = False
        db.commit()
        return True


stream_service = StreamService()
