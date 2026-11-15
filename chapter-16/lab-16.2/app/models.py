from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FraudAnalysis:
    user_id: str
    transaction: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    connected_accounts: list[str] = field(default_factory=list)
    transfer_paths: list[list[str]] = field(default_factory=list)
    fraud_indicators: list[str] = field(default_factory=list)
    unavailable_backends: list[str] = field(default_factory=list)
    backend_errors: dict[str, str] = field(default_factory=dict)
    analysis_status: str = "completed"
    analyzed_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "transaction": self.transaction,
            "recent_events": self.recent_events,
            "connected_accounts": self.connected_accounts,
            "transfer_paths": self.transfer_paths,
            "fraud_indicators": self.fraud_indicators,
            "unavailable_backends": self.unavailable_backends,
            "backend_errors": self.backend_errors,
            "analysis_status": self.analysis_status,
            "analyzed_at": self.analyzed_at,
        }
