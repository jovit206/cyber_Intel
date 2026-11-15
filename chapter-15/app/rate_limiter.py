from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from pymongo import ReturnDocument


class MongoRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("Rate-limit count and window must be positive.")
        self.limit = limit
        self.window_seconds = window_seconds

    @staticmethod
    def _record_id(tenant_id: str, actor: str) -> str:
        digest = hashlib.sha256(f"{tenant_id}\0{actor}".encode("utf-8")).hexdigest()
        return digest

    def check(self, database, tenant_id: str, actor: str) -> None:
        now = datetime.now(timezone.utc)
        reset_before = now - timedelta(seconds=self.window_seconds)
        expired_window = {
            "$or": [
                {"$eq": [{"$type": "$window_start"}, "missing"]},
                {"$lte": ["$window_start", {"$literal": reset_before}]},
            ]
        }
        record = database.rate_limits.find_one_and_update(
            {"_id": self._record_id(tenant_id, actor)},
            [
                {
                    "$set": {
                        "tenant_id": {"$literal": tenant_id},
                        "actor_key": {"$literal": hashlib.sha256(actor.encode("utf-8")).hexdigest()},
                        "window_start": {
                            "$cond": [expired_window, {"$literal": now}, "$window_start"]
                        },
                        "counter": {
                            "$cond": [
                                expired_window,
                                1,
                                {"$add": [{"$ifNull": ["$counter", 0]}, 1]},
                            ]
                        },
                        "expires_at": {
                            "$dateAdd": {
                                "startDate": {
                                    "$cond": [expired_window, {"$literal": now}, "$window_start"]
                                },
                                "unit": "second",
                                "amount": self.window_seconds,
                            }
                        },
                    }
                }
            ],
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if record is None:
            raise RuntimeError("MongoDB rate-limit counter update returned no document.")
        if record["counter"] > self.limit:
            window_start = record["window_start"]
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=timezone.utc)
            retry_after = max(
                1,
                int((window_start + timedelta(seconds=self.window_seconds) - now).total_seconds()),
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Request limit exceeded. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )


def request_actor(request: Request, principal: dict | None) -> tuple[str, str]:
    if principal:
        tenant_id = principal["tenant_id"]
        return tenant_id, str(principal["_id"])
    client_host = request.client.host if request.client else "unknown"
    return "anonymous", client_host
