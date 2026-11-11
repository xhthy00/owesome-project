"""Context windows follow the SDK; internal calls only affect total usage."""

import asyncio
from types import SimpleNamespace

import pytest
from deepagents.middleware.summarization import compute_summarization_defaults
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from src.agent.adapter.usage_sink import UsageSink
from src.chat.service.context_usage import ContextUsageCallback
from src.llm.context import context_limit, with_context_metadata
from src.llm.openai import OpenAILLM


@pytest.mark.parametrize("name,expected", [
    ("MiniMax-M3", 1_000_000), ("MiniMax-M3.1-Flash", 1_000_000),
    ("MiniMax-M3.1-Flash-Preview", 1_000_000),
    ("MiniMax-M2.7", 204_800), ("MiniMax-M2.5-highspeed", 204_800),
])
@pytest.mark.parametrize("host", ["api.minimaxi.com", "api.minimax.io"])
def test_native_minimax_profile_drives_official_compaction(name, expected, host):
    model = OpenAILLM(model=name, api_key="test", base_url=f"https://{host}/v1")._llm
    assert context_limit(model) == model.metadata["chat_context_limit"] == expected
    defaults = compute_summarization_defaults(model)
    assert defaults["trigger"] == ("fraction", 0.85)
    assert defaults["keep"] == ("fraction", 0.10)


def test_sdk_capacity_takes_precedence_and_custom_endpoints_remain_unknown():
    model = OpenAILLM(model="MiniMax-M3", api_key="test", base_url="https://custom.test/v1")._llm
    assert context_limit(model) is None
    updated = model.model_copy(update={"profile": {"max_input_tokens": 123456, "tool_calling": True}})
    resolved = with_context_metadata(updated, model_name="MiniMax-M3", base_url="https://api.minimax.io")
    assert resolved.profile == {"max_input_tokens": 123456, "tool_calling": True}
    assert resolved.metadata["chat_context_limit"] == 123456


def test_root_context_is_latest_input_while_summary_and_children_accumulate():
    events = []

    async def emit(event, payload):
        events.append(payload)

    async def run():
        sink = UsageSink(emit)
        callback = ContextUsageCallback(sink)
        for run_id, tokens, metadata in [
            ("root1", 100, {"chat_context_limit": 1000, "chat_context_key": "root"}),
            ("child", 9000, {"langgraph_checkpoint_ns": "tools:1|model:1"}),
            ("summary", 5000, {"lc_source": "summarization"}),
            ("root2", 200, {"chat_context_limit": 1000, "chat_context_key": "root"}),
            ("verified-answer", 3000, {"lc_source": "verified_answer"}),
        ]:
            await callback.on_chat_model_start({}, [[HumanMessage(content="test")]], run_id=run_id, metadata=metadata)
            message = AIMessage(content="done", usage_metadata={
                "input_tokens": tokens, "output_tokens": 10, "total_tokens": tokens + 10,
            })
            await callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)
        assert sink.prompt_tokens == 17300
        assert sink.context["context_tokens"] == 200
        assert sink.context["context_limit"] == 1000
        assert sink.context["context_key"] == "root"
        assert not sink.context["context_estimated"]
        assert [p["context_tokens"] for p in events] == [100, 200]

    asyncio.run(run())


def test_missing_usage_is_estimated_and_errors_drop_pending_calls():
    async def emit(*_args):
        pass

    async def run():
        sink = UsageSink(emit)
        callback = ContextUsageCallback(sink)
        await callback.on_chat_model_start({}, [[HumanMessage(content="hello")]], run_id="failed")
        await callback.on_llm_error(ValueError(), run_id="failed")
        assert not callback.prompts
        await callback.on_chat_model_start({}, [[HumanMessage(content="hello")]], run_id="ok")
        response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="done"))]])
        await callback.on_llm_end(response, run_id="ok")
        assert sink.total_tokens > 0 and sink.context["context_estimated"]
        assert sink.context["context_limit"] is None

    asyncio.run(run())


def test_authorization_fingerprint_tracks_rule_and_privacy_changes(monkeypatch):
    from contextlib import nullcontext

    from src.chat.service import conversation_state as state

    permission = SimpleNamespace(model_dump=lambda: {"id": 1, "permissions": "hidden"})
    rules = []

    class Query:
        def filter(self, *_a):
            return self

        def all(self):
            return rules if self.model is state.DsRule else [permission]

    class Session:
        def query(self, model):
            query = Query()
            query.model = model
            return query

    monkeypatch.setattr(state, "get_db_session", lambda: nullcontext(Session()))
    monkeypatch.setattr(state, "get_user_by_id", lambda *_a: SimpleNamespace(sys_role="user", system_variables={}))
    monkeypatch.setattr(state, "assert_datasource_accessible", lambda *_a: None)
    monkeypatch.setattr(state, "bypasses_data_row_column_scope", lambda *_a: False)
    monkeypatch.setattr(state, "bypasses_column_visibility", lambda *_a: False)
    monkeypatch.setattr(state, "load_edu_permission_config", lambda: {})
    monkeypatch.setattr(state, "is_anonymize_display_enabled", lambda: True)
    first = state.authorization_fingerprint(7, 1, 4)
    assert first == state.authorization_fingerprint(7, 1, 4)
    permission.model_dump = lambda: {"id": 1, "permissions": "visible"}
    assert first != state.authorization_fingerprint(7, 1, 4)
    second = state.authorization_fingerprint(7, 1, 4)
    rules.append(SimpleNamespace(model_dump=lambda: {"id": 2, "user_list": "[7]"}))
    assert second != state.authorization_fingerprint(7, 1, 4)
    third = state.authorization_fingerprint(7, 1, 4)
    monkeypatch.setattr(state, "is_anonymize_display_enabled", lambda: False)
    assert third != state.authorization_fingerprint(7, 1, 4)


def test_host_history_restores_only_recent_matching_authorization(monkeypatch):
    from contextlib import nullcontext

    from src.chat.service import conversation_state as state

    class Query:
        def join(self, *_a):
            return self

        def filter(self, *_a):
            return self

        def order_by(self, *_a):
            return self

        def limit(self, count):
            assert count == 8
            return self

        def all(self):
            return [
                SimpleNamespace(question="继续", summary="已完成", is_success=True,
                                exec_result='{"context_boundary": "current"}'),
                SimpleNamespace(question="旧权限的问题", summary="旧敏感结果", is_success=True,
                                exec_result='{"context_boundary": "old"}'),
            ]

    monkeypatch.setattr(state, "get_db_session", lambda: nullcontext(SimpleNamespace(query=lambda *_a: Query())))
    messages = state.load_conversation_history(9, 7, 1, 4, "current")
    assert [m.content for m in messages] == ["继续", "已完成"]
