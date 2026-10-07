from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProcessingStateRecord(Base):
    __tablename__ = "processing_states"
    __table_args__ = (UniqueConstraint("raw_event_id", name="uq_processing_raw_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
