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
