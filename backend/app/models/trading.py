from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerProfileRecord(Base):
    __tablename__ = "broker_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    paper_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    live_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaperAccountRecord(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    quote_currency: Mapped[str] = mapped_column(String(20), nullable=False, default="USDT")
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    cash_balance: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaperOrderRecord(Base):
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False, default="market")
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="filled")
    broker_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaperPositionRecord(Base):
    __tablename__ = "paper_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    targets_json: Mapped[list[float]] = mapped_column("targets", JSON, default=list, nullable=False)
    remaining_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open", index=True)
    broker_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)


class ExecutionAuditRecord(Base):
    __tablename__ = "execution_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    broker_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column("request", JSON, default=dict, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column("response", JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class LiveSignalSimulationRecord(Base):
    __tablename__ = "live_signal_simulations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="waiting_entry", index=True)
    broker_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    market_symbol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_state_json: Mapped[list[dict[str, Any]]] = mapped_column("target_state", JSON, default=list, nullable=False)
    stop_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    state_json: Mapped[dict[str, Any]] = mapped_column("state", JSON, default=dict, nullable=False)
    notified_events_json: Mapped[list[str]] = mapped_column("notified_events", JSON, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class NewsTradeDecisionRecord(Base):
    """Immutable one-event/one-mode decision and execution audit for news strategy."""
    __tablename__ = "news_trade_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    symbol: Mapped[str | None] = mapped_column(String(40))
    broker_id: Mapped[str | None] = mapped_column(String(40))
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, nullable=False, default=dict)
    evidence_json: Mapped[dict[str, Any]] = mapped_column("evidence", JSON, nullable=False, default=dict)
    execution_json: Mapped[dict[str, Any]] = mapped_column("execution", JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("event_id", "mode", name="uq_news_trade_mode_event"),)

class NewsSentimentReplayRun(Base):
    """Durable, versioned selection/replay job. Manual and automatic runs never mix."""
    __tablename__ = "news_sentiment_replay_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, index=True)  # auto | manual
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    strategy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsSentimentReplayRecord(Base):
    """Frozen decision trace and separate counterfactual result for *each* archived event."""
    __tablename__ = "news_sentiment_replay_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    strategy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(40))
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # rejected | simulated | unverifiable
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assumed_entry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_proxy_entry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    net_return_pct: Mapped[float | None] = mapped_column(Float)
    net_pnl_quote: Mapped[float | None] = mapped_column(Float)
    trace_json: Mapped[dict[str, Any]] = mapped_column("trace", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "event_id", name="uq_sentiment_replay_run_event"),)


class NewsMissedAlertRecord(Base):
    """Postgres-backed outbox, with one missed-news alert per archived event.

    The alert payload is frozen at decision time; Telegram failure cannot erase it.
    Delivery is at-least-once: a crash after Telegram accepts the POST but before
    the database ACK can occasionally duplicate a message.
    """
    __tablename__ = "news_missed_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(12), nullable=False)  # win | loss
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
