"""Reopening a long conversation must show its latest turns, in chronological order."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.routes import vector_rag
from backend.services.vector_rag import CONVERSATION_META_COLLECTION, CONVERSATIONS_COLLECTION


class _Cursor(list):
    def sort(self, key, direction=1):
        return _Cursor(sorted(self, key=lambda row: row[key], reverse=direction == -1))

    def limit(self, count):
        return _Cursor(self[:count])


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        return _Cursor(row for row in self.rows if all(row.get(k) == v for k, v in query.items()))

    def find_one(self, query, projection=None):
        found = self.find(query)
        return dict(found[0]) if found else None


def test_get_conversation_returns_latest_messages_chronologically(monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [{"conversation_id": "c1", "user_id": "u1", "role": "user" if i % 2 == 0 else "assistant",
                 "content": f"m{i}", "timestamp": start + timedelta(minutes=i)} for i in range(60)]
    collections = {
        CONVERSATIONS_COLLECTION: _Collection(messages),
        CONVERSATION_META_COLLECTION: _Collection([{"conversation_id": "c1", "user_id": "u1", "title": "t"}]),
    }
    monkeypatch.setattr(vector_rag, "rag_database", lambda database: collections)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=object())))

    result = vector_rag.get_conversation("c1", request, {"user_id": "u1"}, limit=50)

    contents = [message["content"] for message in result["messages"]]
    assert contents == [f"m{i}" for i in range(10, 60)]
