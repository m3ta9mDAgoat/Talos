"""OSINT research facts, source audit, and signal references.

Separate from legacy osint_items to upgrade an existing Neon database without
adding potentially destructive columns to a live table.
"""
from datetime import datetime, timezone
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def utcnow():
    return datetime.now(timezone.utc)


class IntelligenceEvent(Base):
    __tablename__ = "intelligence_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    headline: Mapped[str] = mapped_column(String(750), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cluster_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    regions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source_reliability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (
        UniqueConstraint("source_key", "external_id", name="uq_intelligence_source_external"),
        Index("ix_intelligence_feed", "first_seen_at", "importance"),
    )


class IntelligenceRevision(Base):
    __tablename__ = "intelligence_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("intelligence_events.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    title: Mapped[str] = mapped_column(String(750), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("event_id", "revision", name="uq_intelligence_revision"),)


class IntelligenceSourceState(Base):
    __tablename__ = "intelligence_source_state"
    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(240))
    last_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Lease avoids simultaneous collectors writing the same events after process restarts.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntelligenceSignalLink(Base):
    __tablename__ = "intelligence_signal_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("intelligence_events.id"), nullable=False, index=True)
    signal_id: Mapped[int] = mapped_column(Integer, ForeignKey("signals.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    # A related asset is not a claim of causation or a trading recommendation.
    link_reason: Mapped[str] = mapped_column(String(60), nullable=False, default="symbol_overlap")
    __table_args__ = (UniqueConstraint("event_id", "signal_id", name="uq_intelligence_event_signal"),)


class IntelligenceIndicator(Base):
    __tablename__ = "intelligence_indicators"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    asset: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    __table_args__ = (Index("ix_intelligence_indicator_history", "source_key", "metric", "observed_at"),)
