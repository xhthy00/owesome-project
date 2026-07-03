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

logger = logging.getLogger(__name__)

# 轻量重试：仅针对网络/连接类错误，避免瞬时抖动直接打断 Agent 链路。
_DEFAULT_RETRY_COUNT = 3
_RETRY_BACKOFF_SECONDS = (0.4, 1.0)


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
                    if self._is_network_error(exc):
                        raise RuntimeError(
                            "LLM 网络连接失败：无法连接到模型服务。请检查 llm_base_url、网络代理与目标服务可达性。"
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

        content = getattr(response, "content", None)
        if content is None:
            return str(response)
        if isinstance(content, str):
            return content
        return "".join(str(part) for part in content)

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
