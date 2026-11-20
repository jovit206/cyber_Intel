"""Routing, platform knowledge, streaming and safety checks for the AI Threat Assistant pipeline."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import assistant, llm_provider
from backend.services.assistant import _remove_unverified_cves, _strip_unknown_citations, classify_question
from backend.services.platform_knowledge import KnowledgeBase, expand_query, tokenize

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("question, route", [
    ("Explain the complete architecture of my platform.", "platform"),
    ("How do Experiments 1-18 connect?", "platform"),
    ("Explain Experiment 18 internally", "platform"),
    ("Why is Add Money failing?", "platform"),
    ("Why does Network Reliability require a replica set?", "platform"),
    ("How does the platform investigate CVEs?", "platform"),
    ("Where are threat records stored?", "platform"),
    ("What happens when Ollama is unavailable?", "platform"),
    ("What is an IOC?", "general_security"),
    ("What is XSS?", "general_security"),
    ("Analyse CVE-2024-8190", "threat"),
    ("Is 8.8.8.8 malicious?", "threat"),
    ("List IOCs associated with Phorpiex", "threat"),
    ("Which ransomware threats are recorded in the platform?", "threat"),
    ("Tell me about Lazarus Group", "threat"),
    ("What mitigations are recommended for LockBit ransomware?", "mitigation"),
    ("How can I protect against phishing?", "mitigation"),
    ("Who won the football world cup?", "unknown"),
    ("what are the top threats", "threat"),
    ("Show me the latest threats", "threat"),
    ("What are the most active malware campaigns?", "threat"),
    ("What are the top threats in general?", "general_security"),
    ("What are the main types of malware?", "general_security"),
])
def test_questions_are_routed_to_the_source_that_can_answer_them(question, route):
    assert classify_question(question) == route


def test_ranking_questions_return_the_dashboards_top_threats_instead_of_searching_ranking_words():
    from backend.services import threat_retrieval

    assert threat_retrieval._terms("what are the top threats") == []
    assert threat_retrieval._terms("latest LockBit campaigns") == ["LockBit", "campaigns"]

    class Cursor(list):
        def sort(self, spec):
            self.spec = spec
            return self

        def max_time_ms(self, _):
            return self

        def limit(self, n):
            return Cursor(self[:n])

    seen = {}

    class Collection:
        def find(self, query, projection):
            seen["query"] = query
            return Cursor([{"_id": f"e{i}", "info": f"Event {i}", "source": "misp"} for i in range(9)])

    class Database:
        name = "threat_intel"
        client = {"threat_intel_lab18": None}

        def __getitem__(self, name):
            return Collection()

    database = Database()
    result = threat_retrieval.retrieve_structured_threat_evidence(database, "what are the top threats", limit=5)
    assert seen["query"] == {"source": {"$in": ["misp", "virustotal"]}}
    assert [item["chunk_id"] for item in result["items"]] == [f"events:e{i}" for i in range(5)]


def test_short_follow_ups_continue_the_previous_topic_unless_they_name_a_new_domain():
    assert classify_question("Can you explain it more simply?", "platform") == "platform"
    assert classify_question("and what about LockBit?", "threat") == "threat"
    assert classify_question("How does the wallet work then?", "threat") == "platform"
    assert classify_question("more detail", "threat") == "threat"


def test_short_standalone_questions_do_not_inherit_the_previous_route():
    # Both were answered as "no threat evidence" because they followed a threat question.
    assert classify_question("What does SIEM stand for?", "threat") == "general_security"
    assert classify_question("What is the best pizza topping?", "threat") == "unknown"


@pytest.mark.parametrize("question, route", [
    ("whatb are the threats", "threat"), ("wahat is the top threat", "threat"), ("show me threats", "threat"),
    ("what are the threats of using public wifi", "general_security"),
    ("how many threat records are stored", "platform"), ("How many threats are there?", "platform"),
    ("how many types of malware are there", "general_security"),
])
def test_data_and_top_threat_questions_reach_the_platforms_records(question, route):
    assert classify_question(question) == route


def test_a_top_threats_question_with_an_unmatched_word_falls_back_to_the_top_threats():
    from backend.services import threat_retrieval

    calls = []

    class Cursor(list):
        def sort(self, spec):
            return self

        def max_time_ms(self, _):
            return self

        def limit(self, n):
            return Cursor(self[:n])

    class Collection:
        def __init__(self, name):
            self.name = name

        def find(self, query, projection):
            calls.append((self.name, query))
            top = query == {"source": {"$in": ["misp", "virustotal"]}}
            return Cursor([{"_id": "e1", "indicator": "downld.net", "source": "virustotal"}] if top else [])

    class Database:
        name = "threat_intel"
        client = {"threat_intel_lab18": None}

        def __getitem__(self, name):
            return Collection(name)

    result = threat_retrieval.retrieve_structured_threat_evidence(Database(), "wahat is the top threat")
    assert result["ranking"] and result["unmatched_terms"] == ["wahat"]
    assert [item["chunk_id"] for item in result["items"]] == ["events:e1"]
    # A topic search came first, so a real topic with records is still answered from those records.
    assert any(name == "events" and query != {"source": {"$in": ["misp", "virustotal"]}} for name, query in calls)


def test_query_expansion_maps_experiment_numbers_to_every_project_spelling():
    terms = expand_query("Explain Experiment 13")
    for spelling in ("chapter", "lab", "13", "chapter-13"):
        assert spelling in terms
    assert "api" in tokenize("Explain the APIs")


def test_knowledge_base_indexes_docs_code_routes_and_finds_banking_facts():
    from backend.main import app
    kb = KnowledgeBase()
    kb.refresh(app, None)
    kinds = kb.stats()["by_kind"]
    for kind in ("documentation", "source code", "verified API routes", "frontend", "configuration"):
        assert kinds.get(kind, 0) > 0, kind
    results = kb.search("Why is Add Money failing with could not be safely committed?", 5)
    assert any("standalone" in item["text"] and "replica set" in item["text"] for item in results)
    api = kb.search("Which API endpoints does the assistant use?", 6)
    assert any("/api/ask/stream" in item["text"] for item in api)


def test_route_helper_sees_included_routers_and_course_tour_reports_them():
    from backend.main import app
    from backend.routes.course_tour import course_tour
    from backend.utils.routes import route_paths
    paths = route_paths(app)
    assert {"/api/ask", "/api/ask/stream", "/api/lab9/partition", "/api/chapter18/status"} <= paths
    chapters = {c["chapter"]: c for c in course_tour(SimpleNamespace(app=app), {"role": "admin"})["chapters"]}
    assert chapters[8]["status"] == "integrated" and chapters[8]["description"] == "Replica Set and Failover"
    assert chapters[11]["description"] == "Sharding and Horizontal Scalability"
    assert chapters[12]["status"] == "not_integrated"


def test_invalid_citations_and_unverified_cves_are_removed_from_platform_answers():
    answer = "Wallets use transactions [S1][S7]. See CVE-2021-44228 and CVE-2024-8190."
    cleaned = _remove_unverified_cves(_strip_unknown_citations(answer, 2), "evidence mentions CVE-2024-8190")
    assert "[S1]" in cleaned and "[S7]" not in cleaned
    assert "CVE-2021-44228" not in cleaned and "CVE-2024-8190" in cleaned


def _context(question):
    return {"rag": None, "user_id": "u1", "conversation_id": "c1", "metadata": None, "memory": [], "question": question}


def _run(monkeypatch, question, *, items=None, threat=None, llm=None):
    saved = []
    monkeypatch.setattr(assistant, "_save", lambda context, **kwargs: saved.append(kwargs))
    monkeypatch.setattr(assistant, "search_platform_knowledge", lambda *a, **k: list(items or []))
    monkeypatch.setattr(assistant, "retrieve_structured_threat_evidence",
                        lambda *a, **k: {"items": list(threat or []), "sources": [], "unavailable_sources": []})
    calls = []

    def fake_stream(system, user, **kwargs):
        calls.append((system, user, kwargs))
        if isinstance(llm, Exception):
            raise llm
        yield from llm or []

    monkeypatch.setattr(assistant, "stream_chat_answer", fake_stream)
    events = list(assistant.answer_events(None, None, {"user_id": "u1"}, question=question, context=_context(question)))
    return events, saved, calls


def test_top_threats_question_is_answered_from_the_stored_records_without_a_model_call(monkeypatch):
    record = {"chunk_id": "events:virustotal:abc", "source": "virustotal", "evidence_type": "events",
              "source_reference": "https://www.virustotal.com/api/v3/domains/downld.net",
              "text": json.dumps({"indicator": "downld.net", "indicator_type": "domain", "verdict": "malicious",
                                  "severity": "High", "malicious_count": 14, "source": "virustotal",
                                  "timestamp": "2026-11-18T08:13:34Z",
                                  "categories": {"Sophos": "phishing and fraud", "Other": "phishing and fraud"}})}
    monkeypatch.setattr(assistant, "_save", lambda context, **kwargs: None)
    monkeypatch.setattr(assistant, "retrieve_structured_threat_evidence",
                        lambda *a, **k: {"items": [record], "sources": ["events"], "unavailable_sources": [], "ranking": True})
    monkeypatch.setattr(assistant, "stream_chat_answer", lambda *a, **k: pytest.fail("no model call expected"))
    final = list(assistant.answer_events(None, None, {"user_id": "u1"}, question="what are the top threats",
                                         context=_context("what are the top threats")))[-1]
    assert final["route"] == "threat" and final["response_mode"] == "top_threats_records"
    assert ("**downld.net (domain)**: malicious, severity High, 14 engine(s) flagged it malicious, "
            "vendor categories: phishing and fraud, source virustotal, seen 2026-11-18 [S1]") in final["answer"]


@pytest.mark.parametrize("question, shown", [
    ("what is the latest threat", 1), ("What's the newest threat?", 1), ("what are the latest threats", 3),
])
def test_singular_latest_threat_shows_only_the_newest_record(monkeypatch, question, shown):
    records = [{"chunk_id": f"events:e{i}", "source": "virustotal", "evidence_type": "events",
                "text": json.dumps({"indicator": f"bad{i}.example", "verdict": "malicious"})} for i in range(3)]
    monkeypatch.setattr(assistant, "_save", lambda context, **kwargs: None)
    monkeypatch.setattr(assistant, "retrieve_structured_threat_evidence",
                        lambda *a, **k: {"items": list(records), "sources": ["events"], "unavailable_sources": [], "ranking": True})
    final = list(assistant.answer_events(None, None, {"user_id": "u1"}, question=question, context=_context(question)))[-1]
    assert len(final["sources"]) == shown and "bad0.example" in final["answer"]
    assert ("bad1.example" in final["answer"]) == (shown > 1)
    if shown == 1:
        assert final["answer"].startswith("The latest threat recorded on the platform")


def test_unrelated_question_gets_purpose_explanation_without_a_model_call(monkeypatch):
    events, saved, calls = _run(monkeypatch, "Who won the football world cup?")
    final = events[-1]
    assert final["type"] == "final" and final["route"] == "unrelated" and not calls
    assert "Threat Intelligence Platform assistant" in final["answer"]


def test_threat_answer_with_invalid_citations_is_replaced_by_the_cited_records(monkeypatch):
    record = {"chunk_id": "events:1", "source": "botvrij", "text": json.dumps({"info": "Phorpiex delivering LockBit"})}
    events, saved, _ = _run(monkeypatch, "Which ransomware threats are recorded in the platform?",
                            threat=[record], llm=["LockBit is everywhere [S9]"])
    final = events[-1]
    assert final["response_mode"] == "citation_validation_fallback"
    assert "Phorpiex delivering LockBit [S1]" in final["answer"] and "[S9]" not in final["answer"]
    assert saved and saved[0]["mode"] == "citation_validation_fallback"


def test_specific_cve_without_records_is_answered_honestly_without_a_model_call(monkeypatch):
    events, _, calls = _run(monkeypatch, "Analyse CVE-2099-0001")
    assert not calls and events[-1]["response_mode"] == "insufficient_evidence"
    assert "CVE-2099-0001" in events[-1]["answer"]


def test_platform_answer_falls_back_to_excerpts_when_the_model_is_unavailable(monkeypatch):
    chunk = {"chunk_id": "kb:x#0", "source": "Overview", "text": "Wallets are stored in the wallets collection.", "score": 10}
    error = llm_provider.LLMProviderError("llm_provider_unreachable", "down", 502)
    events, saved, _ = _run(monkeypatch, "How does the platform store wallets?", items=[chunk], llm=error)
    final = events[-1]
    assert final["response_mode"] == "extractive_fallback" and "wallets collection" in final["answer"]


def test_general_question_is_not_persisted_when_the_model_is_unavailable(monkeypatch):
    error = llm_provider.LLMProviderError("llm_provider_unreachable", "down", 502)
    events, saved, _ = _run(monkeypatch, "What is XSS?", llm=error)
    assert events[-1]["type"] == "error" and not saved


def test_general_answers_are_streamed_and_labelled_as_general_knowledge(monkeypatch):
    events, saved, calls = _run(monkeypatch, "What is XSS?", llm=["Cross-site ", "scripting is..."])
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens.startswith(assistant.GENERAL_LABEL) and "Cross-site scripting" in tokens
    assert events[-1]["answer"].startswith(assistant.GENERAL_LABEL) and calls[0][2]["max_tokens"] == assistant.MAX_TOKENS["general_security"]


def test_stream_chat_answer_parses_ollama_ndjson_and_keeps_the_model_loaded(monkeypatch):
    lines = [json.dumps({"message": {"content": "Hel"}}), json.dumps({"message": {"content": "lo"}}),
             json.dumps({"done": True})]
    captured = {}

    class FakeResponse:
        status_code = 200

        def iter_lines(self):
            return iter(lines)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_stream(method, url, **kwargs):
        captured.update(kwargs["json"], url=url)
        return FakeResponse()

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_provider.httpx, "stream", fake_stream)
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    assert "".join(llm_provider.stream_chat_answer("sys", "hi", max_tokens=300, history=history)) == "Hello"
    assert captured["stream"] is True and captured["keep_alive"] == "30m"
    assert captured["options"]["num_predict"] == 300
    assert [m["role"] for m in captured["messages"]] == ["system", "user", "assistant", "user"]


def test_host_docker_internal_is_accepted_as_a_local_ollama_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    config = llm_provider.get_llm_configuration()
    assert config.endpoint_secure and config.configured


def test_failover_traffic_summary_reports_gaps_failures_and_primary_changes():
    spec = importlib.util.spec_from_file_location("lab03", ROOT / "lab9" / "lab_03_failover_recovery.py")
    lab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lab)
    ops = [{"t": 0, "ok": True, "ms": 10, "p": "a:27017"}, {"t": 200, "ok": False, "ms": 900, "error": "NotWritablePrimary"},
           {"t": 1300, "ok": True, "ms": 20, "p": "b:27017"}]
    summary = lab.summarise(ops)
    assert summary["succeeded"] == 2 and summary["failed"] == 1 and summary["errors"] == ["NotWritablePrimary"]
    assert summary["longest_gap_between_successes_ms"] == 1290
    assert [p["primary"] for p in summary["primary_as_seen_by_client"]] == ["a:27017", "b:27017"]


@pytest.mark.parametrize("question, expected", [
    ("Explain Experiment 1.", 1), ("What does lab 12 do?", 12), ("Explain Experiment 13.", None),
    ("How do Experiments 1-18 connect?", None), ("Compare experiment 3 and 4", None), ("Explain chapter 6", 6),
])
def test_single_unimplemented_experiment_is_detected(question, expected):
    assert assistant._unimplemented_experiment(question) == expected


def test_unimplemented_experiment_is_answered_from_the_verified_overview_without_the_model(monkeypatch):
    chunk = {"chunk_id": "kb:try#0", "source": "lab10/try_10_1_retry_backoff.py", "text": "Experiments: 1. forced write conflict",
             "source_reference": "lab10/try_10_1_retry_backoff.py", "score": 12}
    events, saved, calls = _run(monkeypatch, "Explain Experiment 1.", items=[chunk], llm=["invented"])
    final = events[-1]
    assert not calls and final["response_mode"] == "platform_knowledge_verified"
    assert "Experiment 1 is not part of this repository" in final["answer"] and "forced write conflict" not in final["answer"]
    assert final["sources"][0]["source_reference"] == "backend/knowledge/PLATFORM_OVERVIEW.md"


@pytest.mark.parametrize("question, tool", [
    ("Scan my laptop for malware.", "threat_scan"), ("Check if there are any suspicious processes on my laptop.", "suspicious_processes"),
    ("Check my laptop for vulnerabilities.", "security_posture"), ("Check for suspicious network connections on my laptop.", "network_connections"),
    ("Scan my laptop for threats.", "threat_scan"), ("Is Windows Defender enabled?", "defender_status"),
    ("Scan my project for exposed API keys.", "secret_scan"), ("Run a Defender quick scan", "defender_quick_scan"),
])
def test_laptop_security_requests_select_a_security_check(question, tool):
    from backend.services.local_assistant import classify
    assert classify(question) == ("security", (tool, {"path": r"G:\Projects\nosql-lab-7"} if tool == "secret_scan" else {}))


@pytest.mark.parametrize("question", [
    "Find the largest files on my laptop", "Find duplicate files in my Downloads folder", "Check my laptop RAM and CPU usage",
    "Clean up temporary files on my computer", "Search for a file named report.pdf on my computer", "Install Python updates on my laptop",
])
def test_general_laptop_management_is_refused(question):
    from backend.services.local_assistant import classify
    assert classify(question) == ("refuse", None)


@pytest.mark.parametrize("question", [
    "Which part of that prevents duplicate payments?", "Explain Docker deployment", "What is RAM?", "What is malware?",
    "Which of those mention LockBit?", "How does the platform detect exposed secrets?", "Explain the complete architecture of my platform.",
])
def test_platform_and_concept_questions_never_reach_the_laptop_agent(question):
    from backend.services.local_assistant import classify
    assert classify(question) is None


@pytest.mark.parametrize("question, previous, expected", [
    ("is steam installed in my laptop", None, ("installed_software", {})),
    ("What software is installed on my computer?", None, ("installed_software", {})),
    ("do I have Zoom installed", None, ("installed_software", {})),
    ("read all my files and check for malware", None, ("defender_full_scan", {})),
    ("Run a full scan of my laptop", None, ("defender_full_scan", {})),
    ("scan my G drive for viruses", None, ("defender_custom_scan", {"path": "G:\\"})),
    (r"scan C:\Users\hp\Downloads for malware", None, ("defender_custom_scan", {"path": r"C:\Users\hp\Downloads"})),
    ("what are the threats in my laptop", None, ("threat_scan", {})),
    ("scan again", "local", ("threat_scan", {})),
    ("which apps do I have", None, ("installed_software", {})),
    ("show all the apps installed in my laptop", None, ("installed_software", {})),
    ("is Spotify installed?", None, ("installed_software", {})),
])
def test_laptop_inventory_full_scans_and_rescans_reach_the_agent(question, previous, expected):
    from backend.services.local_assistant import classify
    assert classify(question, previous) == ("security", expected)


def test_platform_software_and_unrelated_rescans_stay_off_the_laptop_agent():
    from backend.services.local_assistant import classify
    assert classify("Which python packages are installed in the backend?") is None
    assert classify("Which apps does the platform have?") is None
    assert classify("scan again") is None  # no previous laptop scan to repeat


def test_installed_program_lookups_answer_yes_or_no_from_the_inventory():
    from backend.services.local_assistant import _program_name, _render_installed
    assert _program_name("is steam installed in my laptop") == "steam"
    assert _program_name("Do I have Google Chrome?") == "Google Chrome"
    assert _program_name("is anything installed") is None
    inventory = {"total": 3, "software": [{"name": "Steam", "publisher": "Valve Corporation", "version": "2.10",
                                           "kind": "desktop program"}, {"name": "Spotify", "kind": "Store app"},
                                          {"name": "MSTeams", "kind": "Store app"}]}
    assert _render_installed(inventory, "is steam installed in my laptop").startswith("Yes. 1 installed program(s)")
    assert "MSTeams" not in _render_installed(inventory, "is steam installed in my laptop")
    assert "- Steam (Valve Corporation, version 2.10)" in _render_installed(inventory, "is steam installed")
    assert _render_installed(inventory, "is zoom installed on my laptop").startswith("No installed program matching \"zoom\"")


def test_status_polls_survive_a_busy_laptop(monkeypatch):
    from backend.services import local_agent, local_assistant
    monkeypatch.setattr(local_assistant, "POLL_SECONDS", 0)
    monkeypatch.setattr(local_assistant.time, "sleep", lambda _s: None)
    monkeypatch.setattr(local_agent, "allowed", lambda actor: True)
    monkeypatch.setattr(local_agent, "status", lambda actor: {"connected": True})
    monkeypatch.setattr(local_agent, "start", lambda actor, tool, args: {"id": "j1", "tool": tool, "status": "running"})
    replies = iter([local_agent.LocalAgentError("slow", 504, "local_agent_timeout"),
                    local_agent.LocalAgentError("slow", 504, "local_agent_timeout"),
                    {"id": "j1", "tool": "installed_software", "status": "completed",
                     "result": {"total": 1, "software": [{"name": "Steam", "kind": "desktop program"}]}}])

    def job(actor, job_id):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(local_agent, "job", job)
    done = list(local_assistant.events({"role": "admin"}, "is steam installed on my laptop"))[-1]
    assert done["mode"] == "local_agent" and done["answer"].startswith("Yes.")


def test_backend_refuses_non_security_tools_even_if_requested():
    from backend.services import local_agent
    with pytest.raises(local_agent.LocalAgentError) as error:
        local_agent.start({"role": "admin", "user_id": "u"}, "largest_files", {})
    assert error.value.code == "local_agent_out_of_scope"


def test_scan_ips_and_hashes_are_matched_against_platform_indicators():
    from unittest.mock import MagicMock
    from backend.services.local_assistant import match_iocs
    db = MagicMock()
    db["attributes"].find.return_value.limit.return_value = [
        {"normalized_indicator": "203.0.113.9", "indicator_type": "ip-dst", "source_feed_name": "threatfox", "event_id": "e1"}]
    result = {"network_connections": {"remote_ips": ["203.0.113.9", "198.51.100.7"]},
              "suspicious_files": {"findings": [{"sha256": "AB" * 32}]}}
    iocs = match_iocs(db, result)
    query = db["attributes"].find.call_args.args[0]
    assert set(query["normalized_indicator"]["$in"]) == {"203.0.113.9", "198.51.100.7", "ab" * 32}
    assert iocs["checked"] == 3 and iocs["matches"][0]["feed"] == "threatfox"
