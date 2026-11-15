"""Transparent, workload-based shard-key guidance for the Chapter 11 lab."""
from __future__ import annotations

from typing import Any


STRATEGIES: dict[str, dict[str, Any]] = {
    "range_customer_id": {
        "label": "Range customer / entity ID",
        "key": "{ customer_id: 1 } (or entity_id: 1)",
        "patterns": {"customer_lookup", "entity_lookup", "entity_history"},
        "ranges": {"customer_lookup", "entity_lookup"},
        "monotonic": False,
        "distribution": "Ordered ID ranges keep related customers or entities local; skewed activity can make ranges uneven.",
        "targeting": "Equality lookups on the ID target its owning shard. Other query shapes scatter unless they also include the shard key.",
        "range": "Good for ID range scans and entity-local reads; not useful for date ranges by itself.",
        "hotspot": "Usually avoids a global increasing-key edge, but a very active customer/entity can concentrate writes.",
        "verdict": "Choose when entity-local reads and ID range locality are important.",
    },
    "hashed_customer_id": {
        "label": "Hashed customer / entity ID",
        "key": "{ customer_id: \"hashed\" } (or entity_id: \"hashed\")",
        "patterns": {"customer_lookup", "entity_lookup", "entity_history"},
        "ranges": set(),
        "monotonic": False,
        "distribution": "Hashing spreads distinct entity IDs across hash ranges; a hot entity still maps to one shard-key value.",
        "targeting": "Exact entity equality can target one shard. Entity and date ranges lose key ordering; unrelated queries scatter.",
        "range": "Poor for customer/entity and date range scans because hashing removes natural ordering.",
        "hotspot": "Reduces the monotonic insert edge for many distinct entities; one very active entity remains a concentration risk.",
        "verdict": "Choose for frequent entity equality lookups with sustained writes when range locality is secondary.",
    },
    "range_order_id": {
        "label": "Range order ID",
        "key": "{ order_id: 1 }",
        "patterns": {"order_lookup"},
        "ranges": {"order_lookup"},
        "monotonic": True,
        "distribution": "Key order is preserved; monotonically increasing IDs append to the newest range.",
        "targeting": "Exact order lookups and order-ID ranges target the relevant shard ranges. Customer/date reads generally scatter.",
        "range": "Good for ordered order-ID ranges; does not support date-window locality.",
        "hotspot": "High risk when IDs increase and writes are frequent: the active upper range receives new writes.",
        "verdict": "Choose when order-ID reads dominate and the write edge is acceptable or mitigated.",
    },
    "range_event_time": {
        "label": "Range event / order date",
        "key": "{ event_at: 1 } (or order_date: 1)",
        "patterns": {"date_range", "time_range"},
        "ranges": {"date_range", "time_range"},
        "monotonic": True,
        "distribution": "Historical time ranges can span shards; the newest range receives the current time-ordered writes.",
        "targeting": "Date/time windows target overlapping ranges. Customer/entity and order lookups usually scatter.",
        "range": "Best fit here for contiguous date/time-window scans.",
        "hotspot": "High risk for frequent recent writes because they converge on the newest range/chunk.",
        "verdict": "Choose when time-range reads dominate and recent-write concentration is manageable.",
    },
    "hashed_event_time": {
        "label": "Hashed event / order time",
        "key": "{ event_at: \"hashed\" }",
        "patterns": {"date_range", "time_range"},
        "ranges": set(),
        "monotonic": False,
        "distribution": "Distinct event-time values can spread over hash ranges; repeated or bucketed timestamps can still concentrate.",
        "targeting": "Exact timestamp equality can target one shard; date/time windows scatter because hashing removes ordering.",
        "range": "Poor for date/time-window scans; use only when write distribution outweighs time-range locality.",
        "hotspot": "Can reduce a monotonic range edge when timestamps are diverse, but repeated timestamps remain one hashed value.",
        "verdict": "Evaluate for write-heavy time series when broad range queries are secondary.",
    },
    "hashed_indicator": {
        "label": "Hashed indicator / record ID",
        "key": "{ normalized_indicator: \"hashed\" }",
        "patterns": {"indicator_lookup"},
        "ranges": set(),
        "monotonic": False,
        "distribution": "Hashing spreads distinct indicator values; duplicates and unusually hot indicators remain concentrated.",
        "targeting": "Exact indicator equality can target one shard. Time ranges and unrelated lookups scatter.",
        "range": "Poor for indicator prefixes and date ranges; consider a separate access path or another key for range-heavy workloads.",
        "hotspot": "Avoids a monotonically increasing indicator edge for diverse values, but cannot split one identical hot key.",
        "verdict": "Useful for threat intelligence workloads dominated by exact indicator lookups.",
    },
}


def recommend(payload: dict[str, Any]) -> dict[str, Any]:
    """Score documented rules; scores are comparative fit signals, not performance forecasts."""
    patterns = set(payload.get("query_patterns", []))
    high_writes = payload.get("write_frequency") == "high"
    recent_writes = bool(payload.get("recent_write_concentration"))
    ranges_important = bool(payload.get("range_queries_important"))
    entity_scoped = bool(payload.get("entity_scoped_queries"))
    expected_documents = int(payload.get("expected_documents", 1))

    results = []
    for strategy_id, strategy in STRATEGIES.items():
        score = 50
        rules: list[dict[str, Any]] = []
        matched = sorted(patterns & strategy["patterns"])
        if matched:
            adjustment = min(24, 12 * len(matched))
            score += adjustment
            rules.append({"rule": "Common query can include this shard key", "adjustment": adjustment,
                          "reason": f"Matches: {', '.join(matched)}."})
        if strategy_id == "hashed_indicator" and "indicator_lookup" in patterns:
            score += 8
            rules.append({"rule": "Exact indicator lookups", "adjustment": 8,
                          "reason": "Hashing the indicator preserves equality targeting for the workload's explicit indicator lookup."})
        if entity_scoped and strategy_id in {"range_customer_id", "hashed_customer_id"}:
            score += 8
            rules.append({"rule": "Entity-scoped queries", "adjustment": 8,
                          "reason": "The workload often filters on one customer/entity."})
        range_matches = sorted(patterns & strategy["ranges"])
        if ranges_important and range_matches:
            score += 12
            rules.append({"rule": "Important range query preserves key order", "adjustment": 12,
                          "reason": f"Range-compatible patterns: {', '.join(range_matches)}."})
        elif ranges_important and strategy_id.startswith("hashed_"):
            score -= 12
            rules.append({"rule": "Important range query on a hashed key", "adjustment": -12,
                          "reason": "Hashing removes useful range ordering."})
        if high_writes and recent_writes:
            if strategy["monotonic"]:
                score -= 22
                rules.append({"rule": "Frequent writes to recent records", "adjustment": -22,
                              "reason": "An increasing range key concentrates writes at its active edge."})
            elif strategy_id.startswith("hashed_"):
                score += 10
                rules.append({"rule": "Frequent writes across distinct hashed values", "adjustment": 10,
                              "reason": "Hash placement avoids one global increasing-key edge."})
        if expected_documents >= 10_000_000 and strategy_id.startswith("hashed_"):
            score += 3
            rules.append({"rule": "Large expected cardinality", "adjustment": 3,
                          "reason": "Many distinct values give hashed ranges more opportunities to spread ownership."})
        score = max(0, min(100, score))
        results.append({
            "id": strategy_id,
            "label": strategy["label"],
            "key": strategy["key"],
            "fit_score": score,
            "distribution": strategy["distribution"],
            "query_targeting": strategy["targeting"],
            "range_suitability": strategy["range"],
            "hotspot_risk": strategy["hotspot"],
            "verdict": strategy["verdict"],
            "matched_query_patterns": matched,
            "rule_breakdown": rules,
        })

    results.sort(key=lambda item: (-item["fit_score"], item["id"]))
    recommended = results[0]
    compare_ids = [payload.get("compare_a"), payload.get("compare_b")]
    compared = [next((item for item in results if item["id"] == key), None) for key in compare_ids]
    return {
        "profile": payload.get("profile", "custom"),
        "expected_documents": expected_documents,
        "write_frequency": payload.get("write_frequency", "moderate"),
        "recommended": recommended,
        "comparison": compared,
        "strategies": results,
        "educational_notice": (
            "This is an educational workload-based recommendation. Fit scores are rule-based, not benchmark results. "
            "Production decisions require representative workload testing, shard-key cardinality/skew checks, and capacity planning. "
            "Evaluate compound keys when common queries combine entity equality with a range such as time."
        ),
    }
