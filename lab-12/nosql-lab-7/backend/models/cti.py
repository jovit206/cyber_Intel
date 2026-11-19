from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedCtiRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str
    source_id: str
    indicator: str | None = None
    indicator_type: str | None = None
    normalized_indicator: str | None = None
    severity: str | int | None = None
    confidence: float | None = None
    description: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    timestamp: str | None = None
    tags: list[str] = Field(default_factory=list)
    references: list[Any] = Field(default_factory=list)
