"""Central plan access and monthly feature-use policy."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

FREE_WALLET_LIMIT = max(1, int(os.getenv("FREE_WALLET_LIMIT_INR", "500")))
_DEFAULT_LIMITS = {
    "reports": 30,
    "search": 3,
    "cross_source": 20,
    "working_set": 10,
    "write_concern": 5,
    "network_reliability": 5,
    "sharding": 10,
    "inventory": 10,
}
PLUS_LIMITS = {key: max(1, int(os.getenv(f"PLUS_{key.upper()}_MONTHLY_LIMIT", value)))
               for key, value in _DEFAULT_LIMITS.items()}
FEATURE_LABELS = {
    "reports": "Report Corpus", "search": "Search & Enrich",
    "cross_source": "Cross-Source Analysis", "working_set": "Working Set Monitor",
    "write_concern": "Write Concern Latency", "network_reliability": "Network Reliability",
    "sharding": "Sharding & Horizontal Scalability",
    "inventory": "Reliability & Inventory",
}
FEATURE_PATHS = (
    ("/api/reports", "reports"),
    ("/api/search", "search"),
    ("/api/virustotal/", "search"),
    ("/api/cross-source/", "cross_source"),
    ("/api/metrics", "working_set"),
    ("/api/lab72/", "working_set"),
    ("/api/lab9/latency", "write_concern"),
    ("/api/lab9/run-latency", "write_concern"),
    ("/api/lab9/partition", "network_reliability"),
    ("/api/lab9/run-partition", "network_reliability"),
    ("/api/lab8/", "network_reliability"),
    ("/api/chapter11/", "sharding"),
    ("/api/lab10/inventory/", "inventory"),
    ("/api/lab10/reliability/", "inventory"),
)


def billing_period(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).strftime("%Y-%m")


def effective_plan(user: dict, now: datetime | None = None) -> str:
    if user.get("role") == "admin":
        return "admin"
    plan = user.get("plan", "free")
    renewal = user.get("renewal_at")
    if plan not in {"free", "plus", "pro"}:
        return "free"
    if plan != "free" and renewal:
        if renewal.tzinfo is None:
            renewal = renewal.replace(tzinfo=timezone.utc)
        if renewal <= (now or datetime.now(timezone.utc)):
            return "free"
    return plan


def feature_for_path(path: str, method: str = "GET") -> str | None:
    if method.upper() not in {"GET", "POST"}:
        return None
    for prefix, feature in FEATURE_PATHS:
        if path == prefix or path.startswith(prefix):
            return feature
    return None


def feature_limit(plan: str, feature: str) -> int | None:
    if feature not in FEATURE_LABELS:
        return None
    if plan in {"pro", "admin"}:
        return None
    if plan == "free":
        return 0
    return PLUS_LIMITS[feature]


def describe_entitlements(user: dict, db, now: datetime | None = None) -> dict:
    period = billing_period(now)
    plan = effective_plan(user, now)
    features = {}
    for feature, label in FEATURE_LABELS.items():
        limit = feature_limit(plan, feature)
        usage = db.feature_usage.find_one({"_id": f"{user['user_id']}:{feature}:{period}"}) or {}
        used = int(usage.get("count", 0))
        features[feature] = {
            "label": label, "allowed": plan in {"pro", "admin"} or limit is None or (limit > 0 and used < limit),
            "limit": limit, "used": used, "remaining": None if limit is None else max(0, limit - used),
            "reset_period": "monthly", "period": period,
        }
    return {"plan": plan, "period": period, "features": features,
            "free_features": ["live_threat_stream", "top_threats"],
            "wallet_limit": FREE_WALLET_LIMIT if plan == "free" else None}


def reserve_feature_use(db, user: dict, feature: str, period: str) -> tuple[bool, int | None]:
    plan = effective_plan(user)
    limit = feature_limit(plan, feature)
    if limit is None:
        return True, None
    if limit <= 0:
        return False, limit
    key = f"{user['user_id']}:{feature}:{period}"
    result = db.feature_usage.update_one({"_id": key, "count": {"$lt": limit}}, {"$inc": {"count": 1}})
    if result.modified_count == 1:
        return True, limit
    try:
        db.feature_usage.insert_one({"_id": key, "user_id": user["user_id"], "feature": feature,
                                     "period": period, "count": 1})
        return True, limit
    except DuplicateKeyError:
        result = db.feature_usage.update_one({"_id": key, "count": {"$lt": limit}}, {"$inc": {"count": 1}})
        return result.modified_count == 1, limit


def release_feature_use(db, user_id: str, feature: str, period: str) -> None:
    db.feature_usage.update_one({"_id": f"{user_id}:{feature}:{period}", "count": {"$gt": 0}},
                                {"$inc": {"count": -1}})
