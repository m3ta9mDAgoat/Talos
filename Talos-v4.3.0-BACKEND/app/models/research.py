from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def utcnow():
    return datetime.now(timezone.utc)


class ResearchArtifactRecord(Base):
    __tablename__ = "research_artifacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="paper")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategyRunRecord(Base):
    __tablename__ = "strategy_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_config_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, default=dict, nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column("state", JSON, default=dict, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column("summary", JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyTradeRecord(Base):
    __tablename__ = "strategy_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    strategy_config_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    venue: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column("result", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OptimizationRunRecord(Base):
    __tablename__ = "optimization_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_config_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    objective: Mapped[str] = mapped_column(String(120), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, default=dict, nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column("state", JSON, default=dict, nullable=False)
    best_json: Mapped[dict[str, Any]] = mapped_column("best", JSON, default=dict, nullable=False)
    trials_json: Mapped[list[dict[str, Any]]] = mapped_column("trials", JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategyDeploymentRecord(Base):
    __tablename__ = "strategy_deployments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_config_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="paper", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JSON, default=dict, nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column("state", JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
