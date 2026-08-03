"""UsageSink / extract_usage_from_response 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.agent.adapter.usage_sink import (
    UsageSink,
    extract_usage_from_response,
    get_usage_sink,
    usage_tracking,
)


def test_extract_usage_from_usage_metadata_dict():
    resp = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    )
    assert extract_usage_from_response(resp) == (10, 5, 15)


def test_extract_usage_from_response_metadata_token_usage():
    resp = SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2}},
    )
    assert extract_usage_from_response(resp) == (3, 2, 5)


def test_extract_usage_missing_returns_none():
    assert extract_usage_from_response(SimpleNamespace()) is None
    assert extract_usage_from_response(None) is None


def test_usage_sink_accumulates_and_emits():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, data))

    async def _run() -> None:
        sink = UsageSink(emit)
        await sink.record(10, 5, 15)
        await sink.record(20, 10, 30)
        assert sink.total_tokens == 45
        assert sink.prompt_tokens == 30
        assert sink.completion_tokens == 15
        # 第二次可能被节流；强制收尾
        await sink._maybe_emit(delta_total=0, force=True)
        assert events
        assert events[-1][0] == "usage"
        assert events[-1][1]["total_tokens"] == 45

    asyncio.run(_run())


def test_usage_tracking_binds_contextvar():
    async def emit(event: str, data: dict) -> None:
        return None

    async def _run() -> None:
        assert get_usage_sink() is None
        async with usage_tracking(emit) as sink:
            assert get_usage_sink() is sink
            await sink.record(1, 1, 2)
        assert get_usage_sink() is None

    asyncio.run(_run())
