from __future__ import annotations

import csv
import os
import statistics
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.config import Settings
from app.database import DatabaseConnections
from app.fraud_query_service import FraudQueryService

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def measure(operation, iterations: int) -> dict[str, float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    return {
        "average_seconds": statistics.fmean(samples),
        "minimum_seconds": min(samples),
        "maximum_seconds": max(samples),
        "median_seconds": statistics.median(samples),
    }


def benchmark(service: FraudQueryService, user_id: str, txn_id: str, iterations: int) -> list[dict]:
    if iterations < 1:
        raise ValueError("BENCHMARK_ITERATIONS must be at least one.")
    operations = (
        ("mongodb_transaction_lookup", lambda: service.get_transaction(txn_id)),
        ("cassandra_recent_events", lambda: service.get_user_profile(user_id)),
        ("neo4j_graph_traversal", lambda: service.detect_fraud_rings(user_id, 3)),
        ("combined_fraud_analysis", lambda: service.analyze_user(user_id, txn_id)),
    )
    rows = []
    for name, operation in operations:
        row = {"operation": name, "iterations": iterations, **measure(operation, iterations)}
        rows.append(row)
    return rows


def write_results(rows: list[dict], csv_path: Path, chart_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "operation", "iterations", "average_seconds", "minimum_seconds",
        "maximum_seconds", "median_seconds",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    labels = [row["operation"].replace("_", "\n") for row in rows]
    means = [row["average_seconds"] * 1000 for row in rows]
    medians = [row["median_seconds"] * 1000 for row in rows]
    positions = range(len(rows))
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.bar([position - 0.18 for position in positions], means, 0.36, label="Average")
    axis.bar([position + 0.18 for position in positions], medians, 0.36, label="Median")
    axis.set_xticks(list(positions), labels)
    axis.set_ylabel("Latency (milliseconds)")
    axis.set_title("Measured Chapter 16 Fraud Query Latency")
    axis.legend()
    figure.tight_layout()
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(chart_path, dpi=150)
    plt.close(figure)


def main() -> None:
    iterations = int(os.getenv("BENCHMARK_ITERATIONS", "100"))
    user_id = os.getenv("BENCHMARK_USER_ID", "USER001")
    txn_id = os.getenv("BENCHMARK_TXN_ID", "TXN001")
    connections = DatabaseConnections.connect(Settings.from_env())
    try:
        service = FraudQueryService(
            connections.transactions, connections.cassandra_session,
            connections.neo4j_driver, connections.neo4j_database,
        )
        rows = benchmark(service, user_id, txn_id, iterations)
        write_results(rows, REPORTS / "latency_comparison.csv", REPORTS / "latency_comparison.png")
        for row in rows:
            print(
                f"{row['operation']}: avg={row['average_seconds']:.6f}s, "
                f"min={row['minimum_seconds']:.6f}s, max={row['maximum_seconds']:.6f}s, "
                f"median={row['median_seconds']:.6f}s"
            )
    finally:
        connections.close()


if __name__ == "__main__":
    main()
