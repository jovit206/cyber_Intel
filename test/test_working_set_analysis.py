"""/api/lab72/analysis reads collStats per request and computes per-document detail once per collection state."""
from unittest.mock import MagicMock

import pytest

import backend.routes.api as api


class _SyncThread:
    def __init__(self, target, args=(), **_kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture(autouse=True)
def reset_detail_cache(monkeypatch):
    api._WS_DETAIL.update(signature=None, result=None, running=False, error=None)
    monkeypatch.setattr(api.threading, "Thread", _SyncThread)


def _database(count, size, rows):
    database = MagicMock()
    database.command.return_value = {"count": count, "size": size, "avgObjSize": size // max(count, 1),
                                     "totalIndexSize": 4096, "nindexes": 1, "storageSize": 1000}
    database.__getitem__.return_value.aggregate.return_value = rows
    return database


def test_footprint_comes_from_collstats_and_detail_is_computed_once(monkeypatch):
    rows = [{"_id": {"source": "attributes", "benchmark": True}, "count": 3, "bytes": 6000, "min": 1900, "max": 2100},
            {"_id": {"source": "passages", "benchmark": True}, "count": 1, "bytes": 2000, "min": 2000, "max": 2000}]
    database = _database(4, 8000, rows)
    monkeypatch.setattr(api, "get_database", lambda: database)

    first = api.get_working_set_analysis()
    assert first["count"] == 4 and first["totalBytes"] == 8000 and first["totalFootprintBytes"] == 8000 + 4096
    assert first["source"] == "collStats" and "not the cache usage" in first["footprint_definition"]
    second = api.get_working_set_analysis()
    assert second["detail_status"] == "ready"
    assert second["minSize"] == 1900 and second["maxSize"] == 2100
    assert second["detail"]["by_source_collection"] == {"attributes": 3, "passages": 1}
    api.get_working_set_analysis()
    # One background pass for an unchanged collection, never one scan per page view.
    assert database.__getitem__.return_value.aggregate.call_count == 1


def test_detail_is_recomputed_only_when_the_collection_changes(monkeypatch):
    database = _database(1, 2000, [{"_id": {"source": "events", "benchmark": True}, "count": 1, "bytes": 2000, "min": 2000, "max": 2000}])
    monkeypatch.setattr(api, "get_database", lambda: database)
    api.get_working_set_analysis()
    database.command.return_value = {**database.command.return_value, "count": 2, "size": 4000}
    api.get_working_set_analysis()
    assert database.__getitem__.return_value.aggregate.call_count == 2


def test_empty_collection_reports_a_real_zero_state(monkeypatch):
    database = _database(0, 0, [])
    database.command.return_value = {}
    monkeypatch.setattr(api, "get_database", lambda: database)
    result = api.get_working_set_analysis()
    assert result["count"] == 0 and result["totalFootprintBytes"] == 0 and result["detail_status"] == "not_applicable"
    database.__getitem__.return_value.aggregate.assert_not_called()
