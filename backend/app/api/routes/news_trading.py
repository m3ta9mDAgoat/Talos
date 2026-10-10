"""Point-in-time news review, risk catalog, sandbox orders and metrics.

Only /admin actions can write; the middleware enforces the Talos admin cookie.
"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.services import news_trading as strategy

router = APIRouter(prefix="/news-trade", tags=["news trading / research"])

class ConfigWrite(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)

@router.get("/config")
def configuration():
    return strategy.config()

@router.get("/metrics")
def metrics():
    return strategy.metrics()

@router.get("/decisions")
def decisions(limit: int = Query(50, ge=1, le=500)):
    return strategy.decisions(limit)

@router.get("/preview/{event_id}")
async def preview(event_id: int):
    try:
        return await strategy.preview(event_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

@router.put("/admin/config")
def configuration_write(body: ConfigWrite):
    try:
        return strategy.update_config(body.changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@router.post("/admin/paper/{event_id}")
async def paper_entry(event_id: int):
    try:
        return await strategy.paper_entry(event_id, origin="manual")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

class SentimentReplayRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=90)
    limit: int = Field(default=2000, ge=1, le=3000)
    overrides: dict[str, Any] = Field(default_factory=dict)

@router.get("/sentiment/catalog")
def sentiment_catalog():
    """Version and a fully inspectable set of frozen trade selection parameters."""
    return {"version": strategy.SENTIMENT_VERSION, "rules": strategy.SENTIMENT_DEFAULT,
            "feature_rules": [{"name": name, "pattern": pattern, "sentiment_weight": weight}
                              for name, pattern, weight in strategy.SENTIMENT_PATTERNS],
            "long_only": True, "shorting": False, "real_orders": False,
            "latency_mode": "counterfactual publication-time oracle + next 1m bar open",
            "non_trading_label": "Not a measured 5-second execution or achievable historical fill"}

@router.get("/sentiment/runs")
def sentiment_runs(limit: int = Query(20, ge=1, le=100)):
    return strategy.replay_runs(limit=limit)

@router.get("/sentiment/records")
def sentiment_records(run_id: int | None = Query(None, ge=1), days: int = Query(30, ge=1, le=90),
                      limit: int = Query(100, ge=1, le=500)):
    return strategy.sentiment_records(run_id=run_id, days=days, limit=limit)

@router.get("/sentiment/metrics")
def sentiment_metrics(run_id: int | None = Query(None, ge=1), days: int = Query(30, ge=1, le=90)):
    return strategy.sentiment_metrics(run_id=run_id, days=days)

@router.post("/admin/sentiment/backtest")
def run_sentiment_backtest(body: SentimentReplayRequest):
    try:
        return strategy.enqueue_sentiment_replay(kind="manual", days=body.days,
                                                   limit=body.limit, overrides=body.overrides)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@router.get("/sentiment/export.csv")
def sentiment_export(run_id: int | None = Query(None, ge=1), days: int = Query(30, ge=1, le=90)):
    """Stable CSV for outside review; returns exactly what the UI audit records report."""
    import csv
    import io
    import json
    from fastapi.responses import StreamingResponse
    output = io.StringIO()
    writer = csv.writer(output)
    columns = ("run_id", "event_id", "version", "symbol", "sentiment", "score", "selected",
               "status", "label", "published_at", "first_seen_at", "assumed_entry_at",
               "actual_proxy_entry_at", "exited_at", "net_return_pct", "net_pnl_quote", "trace")
    writer.writerow(columns)
    records = strategy.sentiment_records(run_id=run_id, days=days, limit=500)
    # CSV export is bounded to the 500 latest records; full monthly metrics always include all records.
    for record in records:
        writer.writerow([json.dumps(record["trace"], ensure_ascii=False, default=str) if k == "trace"
                         else record.get(k) for k in columns])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition":"attachment; filename=talos_sentiment_replay.csv",
                                      "X-Export-Row-Count": str(len(records))})
