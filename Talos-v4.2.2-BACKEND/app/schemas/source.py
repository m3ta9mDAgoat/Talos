from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import SourceStatus, SourceType


class SourceCreate(BaseModel):
    name: str
    type: SourceType
    identifier: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class SourceRead(SourceCreate):
    id: int
    status: SourceStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceAccessResult(BaseModel):
    source_id: int
    access: bool
    can_read_history: bool
    entity_id: int | None = None
    title: str | None = None
    username: str | None = None
    kind: str | None = None
    sample_message_id: int | None = None
    checked_at: datetime


class ImportResult(BaseModel):
    source_id: int
    fetched: int
    inserted: int
    skipped: int
    history_cursor_id: int | None = None
    history_complete: bool | None = None


class RawMessageRead(BaseModel):
    id: int
    external_id: str
    occurred_at: datetime
    ingested_at: datetime
    event_type: str
    text: str | None
    edit_date: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseStoredResult(BaseModel):
    source_id: int
    examined: int
    parsed: int
    needs_review: int
    ignored: int
    created: int
    updated: int
    classification: dict[str, int] = Field(default_factory=dict)


class StoredSignalRead(BaseModel):
    id: int
    source_message_id: str | None
    timestamp: datetime
    symbol: str
    side: str
    confidence: float
    status: str
    entry: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None
    targets: list[float] = Field(default_factory=list)
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
