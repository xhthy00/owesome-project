"""LLM service factory using LangChain."""

from typing import Any, Literal, Optional

from src.common.core.config import get_settings
from src.llm.base import get_langchain_messages
from src.llm.ollama import OllamaLLM
from src.llm.openai import OpenAILLM

LLMModule = Literal["chat", "match", "paper"]
_MODULES = ("chat", "match", "paper")


def llm_profile(module: LLMModule) -> dict[str, Any]:
    """解析某个模块自己的模型配置。"""
    if module not in _MODULES:
        raise ValueError(f"未知的模型配置: {module}")
    settings = get_settings()

    def field(name: str) -> Any:
        value = getattr(settings, f"llm_{module}_{name}")
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    return {
        "base_url": field("base_url"),
        "api_key": field("api_key"),
        "model": field("model"),
        "max_tokens": field("max_tokens"),
        "thinking": field("thinking"),
        "stream_usage": settings.llm_chat_stream_usage if module == "chat" else None,
    }


def get_llm_provider(base_url: Optional[str] = None) -> str:
    """Detect LLM provider from base_url."""
    base_url = base_url or ""
    if "ollama" in base_url.lower() or not base_url:
        return "ollama"
    return "openai"


def create_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    module: Optional[LLMModule] = None,
    **kwargs
):
    """
    Factory function to create LLM instance using LangChain.

    Args:
        provider: "openai", "ollama", or "auto" (default)
        model: Model name
        api_key: API key
        base_url: API base URL
        module: ``chat`` 智能问数、``match`` 知识点匹配、``paper`` 试卷 AI 识别。
        **kwargs: Additional parameters. ``max_tokens`` / ``thinking`` 未传入时
            使用该模块配置。

    Returns:
        LLM instance (OpenAILLM or OllamaLLM)
    """
    if module is None:
        raise ValueError("create_llm 需要指定模块：chat、match 或 paper")
    profile = llm_profile(module)

    model = model or profile["model"]
    api_key = api_key or profile["api_key"]
    base_url = base_url or profile["base_url"]
    if "max_tokens" not in kwargs and profile["max_tokens"] is not None:
        kwargs["max_tokens"] = profile["max_tokens"]
    if "thinking" not in kwargs and profile["thinking"]:
        kwargs["thinking"] = profile["thinking"]
    if "stream_usage" not in kwargs and profile["stream_usage"] is not None:
        kwargs["stream_usage"] = profile["stream_usage"]

    if provider == "auto" or not provider:
        provider = get_llm_provider(base_url)

    if provider.lower() == "ollama":
        return OllamaLLM(
            model=model,
            base_url=base_url,
            **kwargs
        )
    return OpenAILLM(
        model=model,
        api_key=api_key,
        base_url=base_url,
        **kwargs
    )


# 智能问数模型。知识点匹配和试卷识别按次创建，不放进这个单例。
_default_llm = None


def get_default_llm():
    """智能问数使用的模型单例。"""
    global _default_llm
    if _default_llm is None:
        _default_llm = create_llm(module="chat")
    return _default_llm


def build_chat_messages(
    system_prompt: str,
    user_prompt: str,
    history: Optional[list] = None
):
    """Build message list for chat completion using LangChain format."""
    return get_langchain_messages(system_prompt, user_prompt, history)
