"""把现有 ``src.llm.service`` 的 LLM 适配为 ``agent.LlmClient`` 协议。

设计要点：
- 不改现有 LLM 代码：复用 ``create_llm()`` / ``get_default_llm()`` 返回的
  wrapper，从内部 ``._llm`` 取出 LangChain ``BaseChatModel`` 直接用其原生
  ``ainvoke``，避免 ``asyncio.to_thread`` 包同步 ``chat()`` 的开销与阻塞风险。
- 消息转换遵循 OpenAI 风格 dict：{"role": "system"|"user"|"assistant", "content": ...}。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.agent.util.tool_call_parser import parse_minimax_tool_call

logger = logging.getLogger(__name__)

# 轻量重试：仅针对网络/连接类错误，避免瞬时抖动直接打断 Agent 链路。
_DEFAULT_RETRY_COUNT = 3
_RETRY_BACKOFF_SECONDS = (0.4, 1.0)
# ReAct 回灌 LLM 的单条 observation 上限，降低上下文膨胀与厂商内容审核误报。
_MAX_OBSERVATION_CHARS = 12000


def _dict_messages_to_langchain(messages: list[dict[str, str]]) -> list[BaseMessage]:
    converted: list[BaseMessage] = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if role == "system":
            converted.append(SystemMessage(content=content))
        elif role == "assistant":
            converted.append(AIMessage(content=content))
        else:
            converted.append(HumanMessage(content=content))
    return converted


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None:
        return str(response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def _extract_native_tool_call(response: Any) -> dict[str, Any] | None:
    """把模型原生 function-calling 结果归一化成 ReAct 协议的 ``{"tool","args"}``。

    OpenAI 兼容网关（Qwen / MiniMax / DeepSeek 等）在模型走原生工具调用时，会把
    调用体放进 ``tool_calls`` / ``additional_kwargs``，只在 ``content`` 里留下前言
    和形如 ``[TOOL_CALL] {too`` 的残片。若只读 ``content``，ReAct 侧就会反复报
    "无法从 LLM 输出解析 JSON" 并空转到轮数上限。
    """
    call: Any = None
    calls = getattr(response, "tool_calls", None)
    if isinstance(calls, list) and calls:
        call = calls[0]
    else:
        extra = getattr(response, "additional_kwargs", None) or {}
        raw_calls = extra.get("tool_calls")
        if isinstance(raw_calls, list) and raw_calls:
            call = raw_calls[0]
        elif isinstance(extra.get("function_call"), dict):
            call = {"function": extra["function_call"]}
    if not isinstance(call, dict):
        return None

    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = call.get("name") or fn.get("name")
    if not name:
        return None

    args = call.get("args")
    if args is None:
        args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = None
    return {"tool": str(name), "args": args if isinstance(args, dict) else {}}


def _response_finish_reason(response: Any) -> str:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return ""
    return str(
        metadata.get("finish_reason")
        or metadata.get("stop_reason")
        or (metadata.get("token_usage") or {}).get("finish_reason")
        or ""
    )


def _truncate_observation_for_llm(text: str, *, limit: int = _MAX_OBSERVATION_CHARS) -> str:
    """截断工具 observation，避免多轮 ReAct 上下文过大触发厂商审核或超长。"""
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n…（观察内容已截断，原长 {len(text)} 字符；完整结果见 tool_result 事件）"
    )


def _is_content_safety_error(exc: Exception) -> bool:
    """MiniMax 等内容安全审核（如 1026 input new_sensitive）。"""
    blob = str(exc).lower()
    markers = (
        "new_sensitive",
        "input new_sensitive",
        "content policy",
        "content_policy",
        "moderation",
        "safety",
        "(1026)",
        "1026",
    )
    return any(m in blob for m in markers)


class LangChainLlmClient:
    """实现 ``src.agent.core.base_agent.LlmClient`` 协议。

    Args:
        llm: 由 ``create_llm()`` 产出的 wrapper（OpenAILLM / OllamaLLM）；或直接
            传入一个实现了 ``ainvoke`` 的 LangChain 聊天模型。留空则延迟到首次
            调用时通过 ``get_default_llm()`` 获取。
    """

    def __init__(self, llm: Any | None = None) -> None:
        self._wrapper = llm
        self._chat_model: Any | None = self._extract_chat_model(llm)

    @staticmethod
    def _extract_chat_model(llm: Any) -> Any | None:
        if llm is None:
            return None
        inner = getattr(llm, "_llm", None)
        return inner if inner is not None else llm

    def _ensure_chat_model(self) -> Any:
        if self._chat_model is None:
            from src.llm.service import get_default_llm

            self._wrapper = get_default_llm()
            self._chat_model = self._extract_chat_model(self._wrapper)
        if self._chat_model is None:
            raise RuntimeError("LangChainLlmClient: no chat model available")
        return self._chat_model

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        name = type(exc).__name__
        module = type(exc).__module__
        # 覆盖 openai/httpx/httpcore 常见网络异常。
        if name in {"APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout"}:
            return True
        if module.startswith(("openai", "httpx", "httpcore")) and (
            "connect" in name.lower() or "timeout" in name.lower()
        ):
            return True
        return isinstance(exc, (ConnectionError, TimeoutError))

    @staticmethod
    def _is_transient_http_error(exc: Exception) -> bool:
        """网关侧瞬态错误（5xx / 429），重试通常能恢复。

        覆盖 openai.InternalServerError（含 520/502/503）、RateLimitError，
        以及 langchain_openai 抛出的带 status_code 的 5xx/429。
        """
        name = type(exc).__name__
        module = type(exc).__module__
        if name in {"InternalServerError", "RateLimitError", "APIStatusError"}:
            return True
        if module.startswith("openai"):
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and status in (429, 500, 502, 503, 520):
                return True
        return False

    async def chat(self, messages: list[dict[str, str]]) -> str:
        model = self._ensure_chat_model()
        lc_messages = _dict_messages_to_langchain(messages)

        response: Any = None
        last_error: Exception | None = None
        for attempt in range(_DEFAULT_RETRY_COUNT):
            try:
                ainvoke = getattr(model, "ainvoke", None)
                if ainvoke is not None:
                    response = await ainvoke(lc_messages)
                else:
                    response = await asyncio.to_thread(model.invoke, lc_messages)
                break
            except Exception as exc:  # noqa: BLE001 - 统一在适配层做网络异常归一化
                last_error = exc
                transient = self._is_network_error(exc) or self._is_transient_http_error(exc)
                if not transient or attempt >= _DEFAULT_RETRY_COUNT - 1:
                    if _is_content_safety_error(exc):
                        raise RuntimeError(
                            "LLM 内容安全审核未通过（MiniMax 错误码 1026：input new_sensitive）。"
                            "这通常由多轮对话中累积的 SQL/成绩明细触发，并非应用逻辑错误。"
                            "建议：新开一轮对话重试、缩短子任务上下文，或更换模型/API。"
                        ) from exc
                    if self._is_network_error(exc):
                        raise RuntimeError(
                            "LLM 网络连接失败：无法连接到模型服务。请检查该模块的模型地址、网络代理与目标服务可达性。"
                        ) from exc
                    if self._is_transient_http_error(exc):
                        raise RuntimeError(
                            f"LLM 服务暂不可用（{type(exc).__name__}），已重试 {_DEFAULT_RETRY_COUNT} 次仍失败。请稍后重试。"
                        ) from exc
                    raise
                wait_s = _RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "LLM request transient error (attempt %d/%d): %s",
                    attempt + 1,
                    _DEFAULT_RETRY_COUNT,
                    exc,
                )
                await asyncio.sleep(wait_s)

        if response is None:
            # 理论上不会走到这里，兜底便于调用侧拿到明确错误。
            raise RuntimeError(f"LLM 调用失败：{last_error}")

        await self._record_usage(response)

        text = _response_text(response)
        native = _extract_native_tool_call(response)
        finish_reason = _response_finish_reason(response)
        logger.info(
            "LLM response shape=%s content_length=%d finish_reason=%s native_tool_call=%s",
            type(response).__name__,
            len(text),
            finish_reason or "unknown",
            bool(native),
        )
        if finish_reason.lower() in {"length", "max_tokens", "max_output_tokens"}:
            logger.warning(
                "LLM response ended because of token limit; content_length=%d",
                len(text),
            )
        if native is not None:
            thoughts = text.strip()
            if thoughts:
                native["thoughts"] = thoughts
            logger.info(
                "LLM returned a native tool_call for %r; normalized to ReAct JSON",
                native.get("tool"),
            )
            return json.dumps(native, ensure_ascii=False)
        vendor_call = parse_minimax_tool_call(text)
        if vendor_call is not None:
            logger.info(
                "LLM returned a MiniMax XML tool_call for %r; normalized to ReAct JSON",
                vendor_call.get("tool"),
            )
            return json.dumps(vendor_call, ensure_ascii=False)
        return text

    @staticmethod
    async def _record_usage(response: Any) -> None:
        from src.agent.adapter.usage_sink import extract_usage_from_response, get_usage_sink

        sink = get_usage_sink()
        if sink is None:
            return
        usage = extract_usage_from_response(response)
        if usage is None:
            return
        prompt, completion, total = usage
        try:
            await sink.record(prompt, completion, total)
        except Exception:  # noqa: BLE001 - token 上报失败不影响主回答
            logger.debug("usage sink record failed", exc_info=True)

    async def chat_with_schema(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """使用 LangChain structured output 获取结构化结果。

        约定返回 dict；若底层模型不支持或返回非对象则抛异常，让调用方回退到普通 chat。
        """
        model = self._ensure_chat_model()
        if not hasattr(model, "with_structured_output"):
            raise RuntimeError("model does not support structured output")

        lc_messages = _dict_messages_to_langchain(messages)
        structured_model = model.with_structured_output(schema)
        ainvoke = getattr(structured_model, "ainvoke", None)
        if ainvoke is not None:
            response = await ainvoke(lc_messages)
        else:
            response = await asyncio.to_thread(structured_model.invoke, lc_messages)

        if isinstance(response, str):
            try:
                parsed = json.loads(response)
            except Exception as e:
                raise RuntimeError(f"structured output is not valid JSON: {e}") from e
            if not isinstance(parsed, dict):
                raise RuntimeError("structured output JSON is not an object")
            return parsed
        if not isinstance(response, dict):
            raise RuntimeError(f"structured output is not an object: {type(response).__name__}")
        return response
