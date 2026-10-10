from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisRunRecord(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, default=dict, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column("summary", JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SignalBacktestRecord(Base):
    __tablename__ = "signal_backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    market_symbol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column("result", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MessageLabelRecord(Base):
    __tablename__ = "message_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    predicted_class: Mapped[str] = mapped_column(String(50), nullable=False)
    predicted_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    manual_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reasons_json: Mapped[list[str]] = mapped_column("reasons", JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
