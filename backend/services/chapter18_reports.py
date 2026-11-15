from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any


class ReportDependencyError(RuntimeError):
    pass


def _result_value(operation: dict[str, Any], name: str) -> Any:
    return (operation.get("result") or {}).get(name)


def benchmark_csv(run: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "run_id",
        "overall_status",
        "dataset_provenance",
        "dataset_record_count",
        "operation",
        "backend",
        "measurement_status",
        "warmup_runs",
        "measured_runs",
        "matched_count",
        "group_count",
        "record_count",
        "related_count",
        "min_ms",
        "mean_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
        "not_tested_reason",
        "error_type",
    ])
    for backend, operations in run.get("results", {}).items():
        for operation_name, result in operations.items():
            latencies = result.get("latency_ms") or {}
            dataset = run.get("dataset") or {}
            result_data = result.get("result") or {}
            writer.writerow([
                run.get("run_id"),
                run.get("status"),
                dataset.get("provenance"),
                dataset.get("record_count"),
                operation_name,
                backend,
                result.get("status"),
                run.get("warmup_runs"),
                run.get("measured_runs"),
                result_data.get("matched_count"),
                result_data.get("group_count"),
                result_data.get("record_count"),
                result_data.get("related_count"),
                latencies.get("min"),
                latencies.get("mean"),
                latencies.get("p50"),
                latencies.get("p95"),
                latencies.get("p99"),
                latencies.get("max"),
                result.get("reason"),
                result.get("error_type"),
            ])
    return output.getvalue().encode("utf-8-sig")


def benchmark_pdf(run: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise ReportDependencyError(
            "PDF export requires reportlab from chapter-18/requirements-chapter18.txt."
        ) from error

    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(letter),
        rightMargin=24,
        leftMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()
    dataset = run.get("dataset") or {}
    created_at = run.get("created_at")
    if isinstance(created_at, datetime):
        created_at = (
            created_at.replace(tzinfo=timezone.utc)
            if created_at.tzinfo is None
            else created_at.astimezone(timezone.utc)
        ).isoformat()
    story = [
        Paragraph("Chapter 18 · Comparative Database Benchmark", styles["Title"]),
        Paragraph(f"Run ID: {run.get('run_id', 'unknown')}", styles["Normal"]),
        Paragraph(f"Status: {run.get('status', 'unknown')}", styles["Normal"]),
        Paragraph(
            "Dataset: "
            f"{dataset.get('record_count', 0):,} imported records; "
            "user-provided and not independently verified.",
            styles["Normal"],
        ),
        Paragraph(
            f"Created: {created_at or 'unknown'} · "
            f"{run.get('warmup_runs', 0)} warm-up and "
            f"{run.get('measured_runs', 0)} measured repetitions per operation.",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    rows = [[
        "Operation",
        "Database",
        "Status",
        "Result",
        "Min ms",
        "Mean ms",
        "P50 ms",
        "P95 ms",
        "P99 ms",
        "Max ms",
    ]]
    for backend, operations in run.get("results", {}).items():
        for name, result in operations.items():
            measured = result.get("result") or {}
            metric = result.get("latency_ms") or {}
            summary = (
                f"matches={measured.get('matched_count')}"
                if "matched_count" in measured
                else f"records={measured.get('record_count')}, groups={measured.get('group_count')}"
                if "record_count" in measured
                else f"related={measured.get('related_count')}"
                if "related_count" in measured
                else result.get("reason", result.get("error_type", "—"))
            )
            rows.append([
                name,
                backend,
                result.get("status", "unknown"),
                summary,
                metric.get("min", "—"),
                metric.get("mean", "—"),
                metric.get("p50", "—"),
                metric.get("p95", "—"),
                metric.get("p99", "—"),
                metric.get("max", "—"),
            ])
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    story.extend([
        table,
        Spacer(1, 12),
        Paragraph(
            "Unconfigured or unavailable databases are reported as Not Tested; "
            "no latency is inferred for them. The graph operation follows only "
            "explicit related-threat identifiers. Source-reported confidence is "
            "not a calibrated probability.",
            styles["BodyText"],
        ),
    ])
    document.build(story)
    return output.getvalue()
