from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ThreatRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=120)
    from_source: str = Field(min_length=1, max_length=120)
    threat_type: str = Field(min_length=1, max_length=120)
    indicator: str | None = Field(default=None, max_length=2048)
    target: str | None = Field(default=None, max_length=2048)
    timestamp: datetime
    severity: str | None = Field(default=None, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: str = Field(default="unreviewed", min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=10_000)
    related_threats: list[str] = Field(default_factory=list, max_length=100)
    transaction_ids: list[str] = Field(default_factory=list, max_length=50)
    source_reference: str | None = Field(default=None, max_length=2048)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    country_code: str | None = Field(
        default=None, min_length=2, max_length=2, pattern="^[A-Z]{2}$"
    )
    geolocation_source: str | None = Field(default=None, max_length=120)
    provenance: Literal["imported", "test_fixture"] = "imported"

    @field_validator(
        "threat_id", "source", "from_source", "threat_type", "status", mode="before"
    )
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "indicator",
        "target",
        "description",
        "source_reference",
        "geolocation_source",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_coordinates(self) -> "ThreatRecord":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be provided.")
        return self

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            return value or None
        return value

    @field_validator("timestamp")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_percent_confidence(cls, value: object) -> object:
        if isinstance(value, (int, float)) and 1 < value <= 100:
            return value / 100
        return value

    @field_validator("related_threats", "transaction_ids")
    @classmethod
    def normalize_identifiers(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class AnalystReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["confirmed_threat", "false_positive", "needs_review"]
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("An analyst review rationale is required.")
        return normalized


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=120)
    target: str | None = Field(default=None, max_length=2048)
    indicator: str | None = Field(default=None, max_length=2048)
    from_datetime: datetime
    to_datetime: datetime
    seed_threat_id: str | None = Field(default=None, max_length=256)

    @field_validator("source", mode="before")
    @classmethod
    def normalize_filter(cls, value: str) -> str:
        return value.strip()

    @field_validator("target", "indicator", "seed_threat_id")
    @classmethod
    def normalize_optional_filter(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("from_datetime", "to_datetime")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_query(self) -> "BenchmarkRequest":
        if self.target is None and self.indicator is None:
            raise ValueError("Provide an exact target or indicator for the search benchmark.")
        if self.from_datetime >= self.to_datetime:
            raise ValueError("from_datetime must be earlier than to_datetime.")
        if (self.to_datetime - self.from_datetime).total_seconds() > 31 * 86400:
            raise ValueError("Benchmark time ranges are limited to 31 days.")
        return self
