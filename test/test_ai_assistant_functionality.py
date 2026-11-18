from backend.routes.vector_rag import _is_general_assistant_question
from backend.routes import chapter17_compat
from backend.routes import vector_rag
from types import SimpleNamespace
import pytest
from backend.services.vector_rag import normalize_citation_labels, validate_grounded_answer


def test_common_llm_citation_spellings_are_normalized_before_validation():
    answer = normalize_citation_labels("The feed reports the activity (S1), see [Source 1].")
    assert answer == "The feed reports the activity [S1], see [S1]."
    assert validate_grounded_answer(answer, [{"text": "feed reports the activity"}]) is None


def test_general_platform_and_security_questions_use_llm_path():
    assert _is_general_assistant_question("What does this platform do?")
    assert _is_general_assistant_question("What is a CVE?")
    assert _is_general_assistant_question("What is an IOC?")
    assert _is_general_assistant_question("How do I investigate a suspicious IP address?")
    assert _is_general_assistant_question("What are the different threat statuses?")


def test_specific_cve_and_indicator_questions_require_retrieved_evidence():
    assert not _is_general_assistant_question("Explain CVE-2024-8190")
    assert not _is_general_assistant_question("What happened to 198.51.100.23?")


def test_compat_conversation_history_passes_an_integer_limit(monkeypatch):
    observed = {}

    def fake_get_conversation(conversation_id, request, actor, limit):
        observed.update(conversation_id=conversation_id, limit=limit)
        return {"messages": []}

    monkeypatch.setattr(chapter17_compat, "get_conversation", fake_get_conversation)
    result = chapter17_compat.conversation_history("conversation-1", object(), {"user_id": "admin"})
    assert result == {"messages": []}
    assert observed == {"conversation_id": "conversation-1", "limit": 50}


def test_general_definition_prompt_distinguishes_platform_evidence():
    system, user = vector_rag._build_general_assistant_prompt("What is a CVE?", [])
    assert "Common Vulnerabilities and Exposures" in system
    assert "not evidence that a specific CVE or IOC exists" in system
    assert "What is a CVE?" in user


def test_failed_model_request_does_not_persist_partial_conversation(monkeypatch):
    class Cursor(list):
        def sort(self, *_args):
            return self

        def limit(self, count):
            return Cursor(self[:count])

    class Collection:
        def __init__(self):
            self.writes = []

        def find_one(self, *_args, **_kwargs):
            return None

        def find(self, *_args, **_kwargs):
            return Cursor()

        def insert_one(self, row):
            self.writes.append(row)

        def update_one(self, *_args, **_kwargs):
            self.writes.append(_args)

    class Database(dict):
        pass

    metadata = Collection()
    messages = Collection()
    rag = Database({vector_rag.CONVERSATION_META_COLLECTION: metadata,
                    vector_rag.CONVERSATIONS_COLLECTION: messages})
    monkeypatch.setattr(vector_rag, "rag_database", lambda _database: rag)
    monkeypatch.setattr(vector_rag, "ensure_rag_indexes", lambda _database: None)
    monkeypatch.setattr(vector_rag, "record_project_event", lambda *_args, **_kwargs: None)

    def timeout(*_args):
        raise vector_rag.VectorProjectError("llm_timeout", "The model timed out.", "generation", 504)

    monkeypatch.setattr(vector_rag, "generate_llm_answer", timeout)
    payload = vector_rag.RagQuestion(question="What does this platform do?",
                                    conversation_id="draft-conversation", client="ai-threat-assistant")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=object())))
    with pytest.raises(Exception) as error:
        vector_rag.query_rag(payload, request, {"user_id": "admin"})
    assert getattr(error.value, "status_code", None) == 504
    assert metadata.writes == []
    assert messages.writes == []

