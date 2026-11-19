# Threat intelligence retrieval scope

The Chapter 17 RAG query now combines the existing report Vector Search path with
bounded structured searches over the platform's persisted CTI data. All searches
are read-only; no source collection is created, reindexed, or rewritten by this
retrieval layer.

## Existing MongoDB sources

| Database / collection | Existing fields used | Retrieval role |
| --- | --- | --- |
| Configured application database / `events` | `info`, `tags`, `threat_level`, `source_feed_name`, `timestamp`, `source` | MISP and VirusTotal event evidence, including CVE mentions in event text/tags. |
| Configured application database / `attributes` | `indicator`, `normalized_indicator`, `indicator_type`, `category`, `comment`, `event_id`, `is_current` | Current IOCs and vulnerability/CVE indicators; stale attributes are excluded. |
| Configured application database / `threats` | `name`, `aliases`, `description`, `type`, `country`, `meta`, `source_url` | Existing MISP Galaxy actor, malware, and tool records. |
| Configured application database / `reports` | report metadata such as `title`, `name`, `description`, `source_repository`, `source_pdf` | Report provenance and metadata. |
| Configured application database / `passages` | `text`, `entities`, `threat_ids`, `report_id`, `source_pdf`, `page`, `is_current` | Extracted report passages; stale passages are excluded. |
| `LAB18_MONGO_DATABASE` (default `threat_intel_lab18`) / `full_threats` | `threat_id`, `source`, `from_source`, `threat_type`, `indicator`, `target`, `description`, `status`, `provenance`, `timestamp` | Chapter 18 imported threat records; `test_fixture` records are excluded. |

There is no dedicated `vulnerabilities` collection in the current repository
implementation. CVE/vulnerability intelligence is represented in event text and
tags and in the existing `attributes` records (including vulnerability indicator
types). Retrieval does not invent or synthesize vulnerability records.

## Filtering and retrieval behavior

The existing `POST /api/rag/query` and compatibility `POST /api/ask` accept
optional `platform` and `threat_status` filters. Values are length-bounded and
regex-escaped before use. A threat status is matched exactly against Chapter 18's
`full_threats.status`; other collections are not treated as if their feed freshness
or `is_current` fields were threat statuses. Platform filters match persisted
platform/target metadata or an explicit mention in the relevant source text fields.
The current vector index does not guarantee either
filter, so filtered queries skip vector results rather than return evidence outside
the requested filter.

Without filters, a ready MongoDB Vector Search index supplies semantic report
chunks. Structured queries over current CTI records run alongside it, and results
are interleaved within the requested top-k. If Vector Search is unavailable, the
existing bounded text search over report chunks remains the report fallback while
structured CTI retrieval continues. Collection failures are isolated and reported
by collection name and error type; credentials and raw database errors are not
returned.

The retriever reads only already-configured Chapter 18 MongoDB data and checks that
`full_threats` exists before querying it. It does not initialize the Chapter 18
schema or connect to Cassandra/Neo4j.
