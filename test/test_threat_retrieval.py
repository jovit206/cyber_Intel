from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from backend.services.threat_retrieval import build_source_query, retrieve_structured_threat_evidence
from backend.services.vector_rag import build_rag_prompt


def _get_path(document, path):
    value = document
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(document, query):
    for key, condition in query.items():
        if key == "$and":
            if not all(_matches(document, part) for part in condition):
                return False
        elif key == "$or":
            if not any(_matches(document, part) for part in condition):
                return False
        else:
            value = _get_path(document, key)
            if isinstance(condition, dict):
                for operator, expected in condition.items():
                    if operator == "$exists" and ((value is not None) != expected):
                        return False
                    if operator == "$ne" and value == expected:
                        return False
                    if operator == "$regex":
                        flags = re.IGNORECASE if condition.get("$options") == "i" else 0
                        matcher = re.compile(expected, flags)
                        candidates = value if isinstance(value, list) else [value]
                        if not any(isinstance(item, str) and matcher.search(item) for item in candidates):
                            return False
            elif value != condition:
                return False
    return True


class FakeCursor(list):
    def max_time_ms(self, _milliseconds):
        return self

    def limit(self, count):
        return FakeCursor(self[:count])

    def sort(self, *_args, **_kwargs):
        return self


class FakeCollection:
    def __init__(self, name, rows=(), aggregate_rows=()):
        self.name = name
        self.rows = list(rows)
        self.aggregate_rows = list(aggregate_rows)
        self.reads = 0
        self.writes = []

    def find(self, query=None, projection=None, **_kwargs):
        self.reads += 1
        query = query or {}
        projection = projection or {}
        result = []
        for row in self.rows:
            if _matches(row, query):
                if projection and any(value == 1 for value in projection.values()):
                    row = {key: value for key, value in row.items()
                           if key in projection or key == "_id"}
                result.append(dict(row))
        return FakeCursor(result)

    def find_one(self, *_args, **_kwargs):
        self.reads += 1
        return None

    def aggregate(self, *_args, **_kwargs):
        self.reads += 1
        return FakeCursor(self.aggregate_rows)

    def insert_one(self, document):
        self.writes.append(("insert_one", document))

    def update_one(self, *args, **kwargs):
        self.writes.append(("update_one", args, kwargs))


class FakeDatabase:
    def __init__(self, name, collections=None, client=None):
        self.name = name
        self.collections = collections or {}
        self.client = client

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection(name))

    def list_collection_names(self):
        return list(self.collections)


class FakeClient:
    def __init__(self, databases=None):
        self.databases = databases or {}

    def __getitem__(self, name):
        return self.databases[name]


def _databases(primary_rows, chapter18_rows=()):
    chapter18 = FakeDatabase("threat_intel_lab18", {
        "full_threats": FakeCollection("full_threats", chapter18_rows)
    })
    client = FakeClient({"threat_intel_lab18": chapter18})
    primary = FakeDatabase(
        "chapter07",
        {name: FakeCollection(name, rows) for name, rows in primary_rows.items()},
        client=client,
    )
    return primary, chapter18


def test_retrieves_evidence_from_each_existing_cti_collection(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "threat_intel_lab18")
    primary, _ = _databases({
        "events": [{"_id": "e1", "info": "alpha event", "source": "misp"}],
        "attributes": [{"_id": "a1", "normalized_indicator": "alpha", "is_current": True}],
        "threats": [{"_id": "t1", "name": "alpha actor", "source": "misp-galaxy"}],
        "reports": [{"_id": "r1", "title": "alpha report"}],
        "passages": [{"_id": "p1", "text": "alpha passage", "is_current": True}],
    }, [{
        "threat_id": "h1", "provenance": "imported", "status": "active",
        "target": "Windows", "description": "alpha Chapter 18 threat",
    }])

    result = retrieve_structured_threat_evidence(primary, "alpha", limit=10)

    assert {item["evidence_type"] for item in result["items"]} == {
        "events", "attributes", "threats", "reports", "passages", "full_threats"
    }
    assert "threat_intel_lab18.full_threats" in result["sources"]


def test_exact_cve_lookup_uses_attribute_identity_fields(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [],
        "attributes": [{"_id": "cve-attr", "indicator": "CVE-2024-8190",
            "normalized_indicator": "CVE-2024-8190", "value": "CVE-2024-8190",
            "is_current": True, "source_feed_name": "botvrij"}],
        "threats": [], "reports": [], "passages": [],
    })
    result = retrieve_structured_threat_evidence(primary, "Analyse CVE-2024-8190", limit=5)
    assert [item["chunk_id"] for item in result["items"] if item["evidence_type"] == "attributes"] == ["attributes:botvrij:cve-attr"]
    query = build_source_query("attributes", ["CVE-2024-8190"])
    assert "indicator" in str(query) and "normalized_indicator" in str(query)
    assert "$regex" not in str(query)


def test_platform_status_and_combined_filters_exclude_fixtures(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "threat_intel_lab18")
    primary, _ = _databases({
        "events": [
            {"_id": "win", "info": "alpha Windows"},
            {"_id": "linux", "info": "alpha Linux"},
        ],
        "attributes": [{"_id": "a1", "normalized_indicator": "alpha", "is_current": True}],
        "threats": [], "reports": [], "passages": [],
    }, [
        {"threat_id": "match", "provenance": "imported", "status": "active",
         "target": "Windows", "description": "alpha report"},
        {"threat_id": "wrong-status", "provenance": "imported", "status": "resolved",
         "target": "Windows", "description": "alpha report"},
        {"threat_id": "wrong-platform", "provenance": "imported", "status": "active",
         "target": "Linux", "description": "alpha report"},
        {"threat_id": "fixture", "provenance": "test_fixture", "status": "active",
         "target": "Windows", "description": "alpha report"},
    ])

    result = retrieve_structured_threat_evidence(
        primary, "alpha", limit=10, platform="Windows", threat_status="active"
    )

    assert [item["chunk_id"] for item in result["items"]] == ["full_threats:match"]
    assert result["sources"] == ["threat_intel_lab18.full_threats"]
    assert primary["events"].reads == 0


def test_platform_filter_matches_persisted_text_without_inventing_data(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [
            {"_id": "win", "info": "alpha Windows activity"},
            {"_id": "linux", "info": "alpha Linux activity"},
        ],
        "attributes": [], "threats": [], "reports": [], "passages": [],
    })

    result = retrieve_structured_threat_evidence(primary, "alpha", limit=10, platform="Windows")

    assert [item["chunk_id"] for item in result["items"]] == ["events:win"]
    assert "Windows" in result["items"][0]["text"]


def test_stale_attributes_and_passages_are_excluded(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [],
        "attributes": [
            {"_id": "stale-attribute", "normalized_indicator": "alpha", "is_current": False},
            {"_id": "current-attribute", "normalized_indicator": "alpha", "is_current": True},
        ],
        "threats": [], "reports": [],
        "passages": [
            {"_id": "stale-passage", "text": "alpha evidence", "is_current": False},
            {"_id": "current-passage", "text": "alpha evidence", "is_current": True},
        ],
    })

    result = retrieve_structured_threat_evidence(primary, "alpha", limit=10)

    assert {item["chunk_id"] for item in result["items"]} == {
        "attributes:current-attribute", "passages:current-passage"
    }


def test_empty_results_are_bounded_and_retrieval_is_read_only(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({name: [] for name in ("events", "attributes", "threats", "reports", "passages")})

    result = retrieve_structured_threat_evidence(primary, "nothing matches", limit=1000)

    assert result == {"items": [], "sources": [], "unavailable_sources": []}
    assert all(not collection.writes for collection in primary.collections.values())
    assert retrieve_structured_threat_evidence(primary, "!!!") == {
        "items": [], "sources": [], "unavailable_sources": []
    }


def test_ioc_questions_retrieve_indicator_values_not_generic_events_or_ttps(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "threat_intel_lab18")
    primary, _ = _databases({
        "events": [{"_id": "event", "info": "IOC threat technique"}],
        "attributes": [
            {"_id": "ioc-attribute", "indicator": "sample.example", "indicator_type": "domain",
             "source_feed_name": "ThreatFox", "is_current": True},
            {"_id": "non-ioc", "comment": "ThreatFox ATT&CK technique", "is_current": True},
            {"_id": "attachment", "indicator": "archive.zip", "indicator_type": "attachment",
             "source_feed_name": "ThreatFox", "is_current": True},
        ],
        "threats": [{"_id": "actor", "name": "ThreatFox malware actor"}],
        "reports": [], "passages": [],
    }, [{
        "threat_id": "ioc-threat", "provenance": "imported", "status": "unreviewed",
        "source": "MISP", "from_source": "ThreatFox", "threat_type": "domain", "indicator": "198.51.100.10",
    }])

    result = retrieve_structured_threat_evidence(
        primary, "What concrete IOC values are present in ThreatFox evidence?", limit=10
    )

    assert {item["evidence_type"] for item in result["items"]} == {"attributes", "full_threats"}
    assert all("archive.zip" not in item["text"] for item in result["items"])
    chapter18_ioc = next(item for item in result["items"] if item["evidence_type"] == "full_threats")
    assert chapter18_ioc["source"] == "ThreatFox"
    assert primary["events"].reads == 0
    assert primary["threats"].reads == 0


def test_exact_cve_identifier_is_not_drowned_out_by_question_words(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [
            {"_id": "noise", "info": "source record available for review"},
            {"_id": "cve", "info": "CVE-2024-8190 is referenced by this event"},
        ],
        "attributes": [
            {"_id": "cve-attribute", "indicator": "CVE-2024-8190", "is_current": True},
        ],
        "threats": [], "reports": [], "passages": [],
    })

    result = retrieve_structured_threat_evidence(
        primary, "What does the source record say about CVE-2024-8190?", limit=10
    )

    assert {item["chunk_id"] for item in result["items"]} == {
        "events:cve", "attributes:cve-attribute"
    }


def test_rag_prompt_distinguishes_iocs_from_ttps_and_limits_cve_claims():
    system, _ = build_rag_prompt(
        "List CVEs and indicators of compromise, then describe mitigation.",
        [{"source": "feed", "chunk_id": "events:e1", "text": "ATT&CK technique T1190."}],
        [],
    )

    assert "never infer or invent a CVE" in system
    assert "Do not label actors, malware families, ATT&CK tactics/techniques" in system
    assert "only when the retrieved source explicitly states an action" in system
    assert "Cite every factual claim" in system


def test_ioc_extract_fallback_cites_only_explicit_indicator_fields():
    from backend.services.vector_rag import extract_ioc_evidence_answer

    answer = extract_ioc_evidence_answer([
        {"text": '{"indicator":"sample.example","indicator_type":"domain"}'},
        {"text": '{"info":"ATT&CK technique T1190","tags":["malware"]}'},
        {"text": '{"indicator":"archive.zip","indicator_type":"attachment"}'},
        {"text": '{"indicator":"CVE-2024-8190","indicator_type":"vulnerability"}'},
    ])

    assert "domain: sample.example [S1]" in answer
    assert "[S2]" not in answer
    assert "attachment" not in answer
    assert "archive.zip" not in answer
    assert "CVE-2024-8190" not in answer


def test_grounded_answer_validation_rejects_uncited_and_unsupported_cve_claims():
    from backend.services.vector_rag import validate_grounded_answer

    evidence = [{"text": "The source mentions CVE-2024-8190."}]

    assert validate_grounded_answer("CVE-2024-8190 [S1].", evidence) is None
    assert validate_grounded_answer("An answer without a citation.", evidence) == "invalid_or_missing_citations"
    assert validate_grounded_answer("Unsupported [S2].", evidence) == "invalid_or_missing_citations"
    assert validate_grounded_answer("CVE-2099-9999 [S1].", evidence) == "unsupported_cve_identifier"


def test_evidence_formatting_and_citation_labels_cover_all_sources():
    rows = [
        {"source": "events", "chunk_id": "events:e1", "text": "one"},
        {"source": "reports", "chunk_id": "reports:r1", "text": "two"},
        {"source": "full_threats", "chunk_id": "full_threats:t1", "text": "three"},
        {"source": "attributes", "chunk_id": "attributes:a1", "text": "four"},
    ]

    system, _ = build_rag_prompt("Explain this", rows, [])

    assert "Source: events" in system
    assert "Source: full_threats" in system
    assert "[S1], [S2], [S3], [S4]" in system


def _query_harness(monkeypatch, *, vector_rows=(), event_rows=(), filtered_payload=None, vector_ready=True):
    from backend.routes import vector_rag as route
    from backend.routes.vector_rag import RagQuestion

    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": event_rows, "attributes": [], "threats": [], "reports": [], "passages": [],
    })
    vector_collection = FakeCollection("report_chunks_index", rows=vector_rows, aggregate_rows=vector_rows)
    rag = FakeDatabase("rag_demo", {
        route.CONVERSATION_META_COLLECTION: FakeCollection(route.CONVERSATION_META_COLLECTION),
        route.CONVERSATIONS_COLLECTION: FakeCollection(route.CONVERSATIONS_COLLECTION),
    })
    monkeypatch.setattr(route, "rag_database", lambda _database: rag)
    monkeypatch.setattr(route, "ensure_rag_indexes", lambda _database: None)
    monkeypatch.setattr(route, "record_project_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(route, "rag_search_collections", lambda _database: [vector_collection])
    monkeypatch.setattr(route, "create_embeddings", lambda *_args: ([[0.1, 0.2]], 2, 1.5))
    monkeypatch.setattr(route, "inspect_vector_index", lambda *_args: {
        "supported": True, "ready": vector_ready, "dimensions": 2, "detail": "test index state"
    })
    monkeypatch.setattr(route, "generate_llm_answer", lambda *_args: ("Grounded answer [S1].", 2.0))

    payload = filtered_payload or RagQuestion(question="alpha", top_k=5)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=primary)))
    return route.query_rag(payload, request, {"user_id": "admin"}), vector_collection


def test_vector_search_results_are_combined_with_structured_threats(monkeypatch):
    response, vector_collection = _query_harness(
        monkeypatch,
        vector_rows=[{"text": "alpha report evidence", "source": "report.pdf", "chunk_id": "report:1", "score": 0.9}],
        event_rows=[{"_id": "event-1", "info": "alpha threat event"}],
    )

    assert response["retrieval_mode"] == "vector_search+structured"
    assert {source["chunk_id"] for source in response["sources"]} == {"report:1", "events:event-1"}
    assert vector_collection.reads == 1


def test_vector_unavailable_uses_structured_report_fallback(monkeypatch):
    response, _ = _query_harness(
        monkeypatch,
        vector_rows=[{"text": "alpha indexed report", "source": "report.pdf", "chunk_id": "report:1", "status": "embedded"}],
        event_rows=[{"_id": "event-1", "info": "alpha threat event"}],
        vector_ready=False,
    )

    assert response["retrieval_mode"] == "structured_fallback"
    assert {source["chunk_id"] for source in response["sources"]} == {"report:1", "events:event-1"}


def test_filtered_query_skips_unfiltered_vector_index(monkeypatch):
    from backend.routes.vector_rag import RagQuestion, VectorProjectError
    from backend.routes import vector_rag as route

    def unexpected_embedding(*_args):
        raise AssertionError("Filtered queries must not use the unfiltered vector index.")

    monkeypatch.setattr(route, "create_embeddings", unexpected_embedding)
    response, vector_collection = _query_harness(
        monkeypatch,
        vector_rows=[{"text": "alpha unfiltered report", "source": "report.pdf", "chunk_id": "report:1"}],
        event_rows=[
            {"_id": "windows", "info": "alpha Windows threat"},
            {"_id": "linux", "info": "alpha Linux threat"},
        ],
        filtered_payload=RagQuestion(question="alpha", platform="Windows", top_k=5),
    )

    assert response["retrieval_mode"] == "structured_filtered"
    assert response["metrics"]["query_embedding_ms"] is None
    assert [source["chunk_id"] for source in response["sources"]] == ["events:windows"]
    assert vector_collection.reads == 0


def test_status_filter_uses_chapter18_imported_status_only(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "threat_intel_lab18")
    primary, _ = _databases({
        "events": [{"_id": "event-1", "info": "alpha active threat"}],
        "attributes": [], "threats": [], "reports": [], "passages": [],
    }, [{
        "threat_id": "threat-1", "provenance": "imported", "status": "active",
        "target": "Linux", "description": "alpha CVE-2026",
    }])

    result = retrieve_structured_threat_evidence(primary, "alpha", limit=10, threat_status="active")

    assert [item["chunk_id"] for item in result["items"]] == ["full_threats:threat-1"]
    assert primary["events"].reads == 0


def test_question_function_words_are_not_search_terms():
    from backend.services.threat_retrieval import _terms

    assert _terms("What is the stock price of Contoso Ltd tomorrow?") == ["stock", "price", "Contoso", "Ltd"]
    assert _terms("Which ransomware threats are recorded in the platform?") == ["ransomware"]
    assert _terms("Analyse CVE-2024-8190 in the platform") == ["CVE-2024-8190"]


def test_question_with_only_function_words_retrieves_no_evidence(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [{"_id": "e1", "info": "This is the list of threats in the platform"}],
        "attributes": [], "threats": [], "reports": [], "passages": [],
    })
    result = retrieve_structured_threat_evidence(primary, "What is in the platform?", limit=5)
    assert result["items"] == []


def test_feed_qualified_attribute_ids_are_not_qualified_twice(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [],
        "attributes": [{"_id": "botvrij:abc", "indicator": "CVE-2024-8190",
            "normalized_indicator": "CVE-2024-8190", "is_current": True, "source_feed_name": "botvrij"}],
        "threats": [], "reports": [], "passages": [],
    })
    result = retrieve_structured_threat_evidence(primary, "CVE-2024-8190", limit=5)
    assert [item["chunk_id"] for item in result["items"]] == ["attributes:botvrij:abc"]


def test_terms_do_not_match_inside_unrelated_tokens(monkeypatch):
    monkeypatch.setenv("LAB18_MONGO_DATABASE", "not-configured-database")
    primary, _ = _databases({
        "events": [],
        "attributes": [
            {"_id": "mid", "indicator": "9sltd81l.ace90.games", "is_current": True},
            {"_id": "part", "indicator": "evil.ltd-example.com", "is_current": True},
        ],
        "threats": [], "reports": [], "passages": [],
    })
    result = retrieve_structured_threat_evidence(primary, "Contoso Ltd", limit=5)
    assert [item["chunk_id"] for item in result["items"]] == ["attributes:part"]
