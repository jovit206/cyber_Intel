"""Smoke test: every API endpoint and static page against the running server."""
import sys

import httpx

BASE = "http://localhost:8000"
failures = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    with httpx.Client(timeout=30.0, follow_redirects=True) as http:
        # Static pages
        for path in ("/", "/app.js", "/style.css", "/config.js"):
            response = http.get(BASE + path)
            check(f"GET {path}", response.status_code == 200, f"HTTP {response.status_code}")

        html = http.get(BASE + "/").text
        check("index.html has all views", all(
            f"view-{name}" in html for name in ("feed", "threats", "reports", "cache")
        ))

        response = http.get(BASE + "/api/meta")
        meta = response.json()
        check("/api/meta", response.status_code == 200 and meta["events"] > 0,
              f"{meta.get('events')} events")

        response = http.get(BASE + "/api/stats")
        stats = response.json()
        check("/api/stats counts", stats["events"] > 0 and stats["attributes"] > 0
              and stats["threats"] > 0 and stats["reports"] > 0,
              f"events={stats['events']} attributes={stats['attributes']} "
              f"threats={stats['threats']} reports={stats['reports']}")
        check("/api/stats feeds", set(stats["events_by_source"]) == {"circl", "botvrij", "threatfox"},
              str(stats["events_by_source"]))

        response = http.get(BASE + "/api/sources")
        feeds = response.json()["feeds"]
        feed_names = {row["_id"] for row in feeds}
        check("/api/sources lists all three feeds", feed_names == {"circl", "botvrij", "threatfox"},
              str(feed_names))
        check("/api/sources has indicators", all(row["indicators"] > 0 for row in feeds))

        response = http.get(BASE + "/api/threats/top?limit=12")
        top = response.json()
        check("/api/threats/top returns real events", len(top) > 0, f"{len(top)} events")
        check("top events carry feed + uuid", all(
            event.get("source_feed_name") and event.get("misp_uuid") for event in top
        ))

        event_id = top[0]["_id"]
        response = http.get(BASE + f"/api/threats/{event_id}")
        detail = response.json()
        check("/api/threats/{id} returns event + attributes",
              bool(detail.get("event")) and len(detail.get("attributes", [])) > 0,
              f"{len(detail.get('attributes', []))} attributes")

        response = http.get(BASE + "/api/events?page=1&limit=30")
        page = response.json()
        check("/api/events paginates real data", page["total"] > 0 and len(page["events"]) > 0,
              f"total={page['total']} pages={page['pages']}")

        response = http.get(BASE + f"/api/events/{event_id}")
        event_detail = response.json()
        check("/api/events/{id}", bool(event_detail.get("event")))

        response = http.get(BASE + "/api/reports")
        reports = response.json()
        check("/api/reports returns synced reports", len(reports) > 0, f"{len(reports)} reports")
        check("reports carry provenance", all(
            row.get("source_repository") and row.get("pdf_url") for row in reports
        ))

        # Search using a term that exists in the real data
        sample_event = page["events"][0]
        term = (sample_event.get("info") or "MISP").split()[0].strip('"[],')
        response = http.get(BASE + "/api/search", params={"q": term})
        search = response.json()
        check("/api/search finds real matches",
              len(search["threats"]) + len(search["iocs"]) + len(search["passages"]) > 0,
              f"q={term!r}: threats={len(search['threats'])} iocs={len(search['iocs'])} "
              f"passages={len(search['passages'])}")

        response = http.get(BASE + "/api/metrics")
        metrics = response.json()
        check("/api/metrics WiredTiger telemetry", metrics.get("available") is True)

        response = http.get(BASE + "/api/data-source-status")
        status = response.json()
        check("/api/data-source-status MongoDB connected",
              status["mongodb"]["connected"] is True)
        synced_feeds = {row.get("_id") for row in status["misp_feeds"] if row.get("last_successful_sync")}
        check("/api/data-source-status shows last successful sync for all feeds",
                {"circl", "botvrij", "threatfox"} <= synced_feeds,
                str(sorted(synced_feeds)))

        response = http.get(BASE + "/api/lab72/analysis")
        lab72 = response.json()
        check("/api/lab72/analysis working set", lab72["count"] > 0,
              f"{lab72['count']:,} docs, avg {lab72['avgSize']:,} bytes")

        response = http.post(BASE + "/api/lab72/simulate-reads")
        reads = response.json()
        check("POST /api/lab72/simulate-reads",
              response.status_code == 200 and reads.get("queriesExecuted", 0) > 0,
              f"{reads.get('queriesExecuted')} reads in {reads.get('durationMs')} ms")

        response = http.get(BASE + "/health")
        check("/health", response.json() == {"status": "ok"})

    print()
    if failures:
        print(f"{len(failures)} FAILURES: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
