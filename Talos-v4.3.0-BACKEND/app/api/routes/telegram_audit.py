from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.telegram_integrity import integrity_summary, message_history

router = APIRouter(prefix="/telegram-audit", tags=["telegram-audit"])


@router.get("/sources/{source_id}/integrity")
def source_integrity(source_id: int):
    try:
        return integrity_summary(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sources/{source_id}/messages/{message_id}")
def source_message_history(source_id: int, message_id: str):
    try:
        return message_history(source_id, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
