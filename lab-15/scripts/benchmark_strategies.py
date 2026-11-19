from __future__ import annotations

import csv
import os
import statistics
import time
import xml.sax.saxutils as xml_escape
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from app.database import create_client, ensure_indexes
from scripts.migrate_tenants import EXPECTED_TENANTS, TENANT_COLLECTION_PREFIX

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"
CSV_PATH = REPORT_DIR / "performance_comparison.csv"
CHART_PATH = REPORT_DIR / "performance_comparison.svg"
ITERATIONS = 25
ANALYTICS_TENANTS = sorted(EXPECTED_TENANTS)


def _list_shared(database, tenant_id: str) -> None:
    list(database.tasks.find({"tenant_id": tenant_id}).sort("created_at", DESCENDING).limit(100))


def _list_isolated(database, tenant_id: str) -> None:
    list(
        database[f"{TENANT_COLLECTION_PREFIX}{tenant_id}"]
        .find({})
        .sort("created_at", DESCENDING)
        .limit(100)
    )


def _create_shared(database, tenant_id: str, task_id: ObjectId) -> None:
    database.tasks.insert_one({
        "_id": task_id,
        "tenant_id": tenant_id,
        "title": "Benchmark insert",
        "status": "todo",
        "priority": "low",
        "created_at": datetime.now(timezone.utc),
    })


def _create_isolated(database, tenant_id: str, task_id: ObjectId) -> None:
    database[f"{TENANT_COLLECTION_PREFIX}{tenant_id}"].insert_one({
        "_id": task_id,
        "tenant_id": tenant_id,
        "title": "Benchmark insert",
        "status": "todo",
        "priority": "low",
        "created_at": datetime.now(timezone.utc),
    })


def _analytics_shared(database) -> list[dict]:
    return list(database.tasks.aggregate([
        {"$match": {"tenant_id": {"$in": ANALYTICS_TENANTS}}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))


def _analytics_isolated(database) -> list[dict]:
    totals: dict[str, int] = {}
    for tenant_id in ANALYTICS_TENANTS:
        collection = database[f"{TENANT_COLLECTION_PREFIX}{tenant_id}"]
        for row in collection.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]):
            totals[row["_id"]] = totals.get(row["_id"], 0) + row["count"]
    return [{"_id": status, "count": count} for status, count in sorted(totals.items())]


def _measure(iterations: int, operation) -> float:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    return statistics.fmean(samples)


def benchmark(database, iterations: int = ITERATIONS) -> list[dict]:
    if iterations < 1:
        raise ValueError("Benchmark iterations must be at least one.")
    ensure_indexes(database)
    for tenant_id in ANALYTICS_TENANTS:
        if database.tasks.count_documents({"tenant_id": tenant_id}) != 100:
            raise RuntimeError(f"Shared strategy requires exactly 100 tasks for {tenant_id}.")
        isolated = database[f"{TENANT_COLLECTION_PREFIX}{tenant_id}"]
        if isolated.count_documents({}) != 100:
            raise RuntimeError(f"Collection strategy requires exactly 100 tasks for {tenant_id}.")
        isolated.create_index([("created_at", DESCENDING)], name="ix_created_at")

    rows = []
    list_operations = {
        "document_level": lambda: _list_shared(database, ANALYTICS_TENANTS[0]),
        "collection_per_tenant": lambda: _list_isolated(database, ANALYTICS_TENANTS[0]),
    }
    analytics_operations = {
        "document_level": lambda: _analytics_shared(database),
        "collection_per_tenant": lambda: _analytics_isolated(database),
    }

    for strategy, operation in list_operations.items():
        operation()
        rows.append({
            "strategy": strategy,
            "operation": "list_one_tenant",
            "iterations": iterations,
            "average_seconds": f"{_measure(iterations, operation):.9f}",
            "sample_data_documents": 500,
        })

    for strategy, create_operation, collection_for in (
        ("document_level", _create_shared, lambda tenant: database.tasks),
        ("collection_per_tenant", _create_isolated, lambda tenant: database[f"{TENANT_COLLECTION_PREFIX}{tenant}"]),
    ):
        tenant_id = ANALYTICS_TENANTS[0]

        def timed_create() -> None:
            task_id = ObjectId()
            create_operation(database, tenant_id, task_id)
            collection_for(tenant_id).delete_one({"_id": task_id})

        rows.append({
            "strategy": strategy,
            "operation": "create_one_task",
            "iterations": iterations,
            "average_seconds": f"{_measure(iterations, timed_create):.9f}",
            "sample_data_documents": 500,
        })

    shared_analytics = analytics_operations["document_level"]()
    for strategy, operation in analytics_operations.items():
        if operation() != shared_analytics:
            raise RuntimeError("Cross-tenant analytics results differ between strategies.")
        rows.append({
            "strategy": strategy,
            "operation": "cross_tenant_analytics",
            "iterations": iterations,
            "average_seconds": f"{_measure(iterations, operation):.9f}",
            "sample_data_documents": 500,
        })
    return rows


def write_csv(rows: list[dict], path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["strategy", "operation", "iterations", "average_seconds", "sample_data_documents"]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_chart(rows: list[dict], path: Path = CHART_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 900, 420
    margin = 80
    plot_width = width - 2 * margin
    plot_height = height - 2 * margin
    max_value = max(float(row["average_seconds"]) for row in rows) or 1.0
    colors = {"document_level": "#2878b5", "collection_per_tenant": "#f28e2b"}
    operations = ["list_one_tenant", "create_one_task", "cross_tenant_analytics"]
    group_width = plot_width / len(operations)
    bar_width = 42
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="32" text-anchor="middle" font-family="Arial" font-size="20">Measured MongoDB Tenant Strategy Performance</text>',
    ]
    for tick in range(5):
        value = max_value * tick / 4
        y = margin + plot_height - (plot_height * tick / 4)
        elements.append(f'<line x1="{margin}" y1="{y:.1f}" x2="{width-margin}" y2="{y:.1f}" stroke="#dddddd"/>')
        elements.append(f'<text x="{margin-10}" y="{y+4:.1f}" text-anchor="end" font-family="Arial" font-size="11">{value:.6f}</text>')
    for index, operation in enumerate(operations):
        center = margin + group_width * (index + 0.5)
        elements.append(
            f'<text x="{center:.1f}" y="{height-margin+28}" text-anchor="middle" '
            f'font-family="Arial" font-size="12">{xml_escape.escape(operation.replace("_", " "))}</text>'
        )
        for series, strategy in enumerate(("document_level", "collection_per_tenant")):
            row = next(item for item in rows if item["operation"] == operation and item["strategy"] == strategy)
            value = float(row["average_seconds"])
            bar_height = value / max_value * plot_height
            x = center + (series - 0.5) * (bar_width + 8) - bar_width / 2
            y = margin + plot_height - bar_height
            elements.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" '
                f'fill="{colors[strategy]}"><title>{strategy}: {value:.9f} seconds</title></rect>'
            )
    elements.append(f'<text x="20" y="210" transform="rotate(-90 20 210)" text-anchor="middle" font-family="Arial" font-size="13">Average seconds per operation</text>')
    elements.append('<rect x="270" y="382" width="14" height="14" fill="#2878b5"/><text x="290" y="394" font-family="Arial" font-size="12">Document-level</text>')
    elements.append('<rect x="440" y="382" width="14" height="14" fill="#f28e2b"/><text x="460" y="394" font-family="Arial" font-size="12">Collection-per-tenant</text>')
    elements.append("</svg>")
    path.write_text("\n".join(elements) + "\n", encoding="utf-8")


def main() -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        raise SystemExit("Set MONGO_URI before running benchmarks.")
    database_name = os.getenv("TASKTRACKER_DB_NAME", "tasktracker")
    iterations = int(os.getenv("BENCHMARK_ITERATIONS", str(ITERATIONS)))
    client = create_client(uri)
    try:
        client.admin.command("ping")
        rows = benchmark(client[database_name], iterations)
        write_csv(rows)
        write_chart(rows)
        for row in rows:
            print(
                f"{row['strategy']},{row['operation']},{row['iterations']},"
                f"{row['average_seconds']} seconds"
            )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
