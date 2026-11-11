"""503 重试：DataSemantic 忙/未就绪时先退避重试，耗尽后才报服务不可用。"""

from types import SimpleNamespace

import httpx
import pytest

import src.chat.service.semantic_query as semantic_query_module
from src.chat.service.semantic_query import SemanticContractError, SemanticQuery

BINDING = {"bundle_id": "b", "manifest_hash": "h"}


def _query():
    query = SemanticQuery.__new__(SemanticQuery)
    query.settings = SimpleNamespace(
        datasemantic_url="http://planner.internal",
        datasemantic_token="test-token",
    )
    return query


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "http://planner.internal/internal/v1/plan")
    return httpx.Response(status, request=request, json=payload or {})


def test_call_retries_busy_503_before_succeeding(monkeypatch):
    responses = [
        _response(503),
        _response(503),
        _response(200, BINDING),
    ]
    calls, sleeps = [], []

    def fake_request(*args, **kwargs):
        calls.append(args)
        return responses[len(calls) - 1]

    monkeypatch.setattr(semantic_query_module.httpx, "request", fake_request)
    monkeypatch.setattr(semantic_query_module.time, "sleep", sleeps.append)

    result = _query()._call("POST", "plan", BINDING)

    assert result["bundle_id"] == "b"
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_call_reports_service_unavailable_after_retries(monkeypatch):
    calls = []

    def fake_request(*args, **kwargs):
        calls.append(args)
        return _response(503)

    monkeypatch.setattr(semantic_query_module.httpx, "request", fake_request)
    monkeypatch.setattr(semantic_query_module.time, "sleep", lambda _: None)

    with pytest.raises(SemanticContractError) as caught:
        _query()._call("POST", "plan", BINDING)

    assert caught.value.detail["code"] == "QUERY_SERVICE_UNAVAILABLE"
    assert len(calls) == 3
