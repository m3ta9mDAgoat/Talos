from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelegramSignalVersionRecord(Base):
    __tablename__ = "telegram_signal_versions"
    __table_args__ = (
        UniqueConstraint("raw_event_id", name="uq_tg_signal_version_raw_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_identifier: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    revision_type: Mapped[str] = mapped_column(String(30), nullable=False, default="message", index=True)
    revision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    original_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    side: Mapped[str | None] = mapped_column(String(20), nullable=True)
    market_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unparsed", index=True)
    entry_json: Mapped[dict[str, Any] | None] = mapped_column("entry", JSON, nullable=True)
    stop_json: Mapped[dict[str, Any] | None] = mapped_column("stop", JSON, nullable=True)
    targets_json: Mapped[list[float]] = mapped_column("targets", JSON, default=list, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_json: Mapped[dict[str, Any]] = mapped_column("diff", JSON, default=dict, nullable=False)
    integrity_flags_json: Mapped[list[str]] = mapped_column("integrity_flags", JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
