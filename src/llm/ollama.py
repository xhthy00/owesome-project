"""Ollama LLM using LangChain's ChatOpenAI (Ollama provides OpenAI-compatible API)."""

from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI

from src.llm.base import append_instruction, normalize_messages
from src.llm.context import with_context_metadata


class OllamaLLM:
    """
    Ollama LLM using LangChain.

    Note: Ollama provides an OpenAI-compatible API, so we use ChatOpenAI
    with the Ollama base URL to connect.
    """

    def __init__(
        self,
        model: str = "qwen2.5",
        base_url: Optional[str] = None,
        temperature: float = 0,
        timeout: int = 300,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        **kwargs
    ):
        self.model = model or "qwen2.5"
        # Ollama's API is OpenAI-compatible at /v1 endpoint
        self.base_url = base_url or "http://localhost:11434"
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.extra_params = kwargs

        # Create LangChain chat model using OpenAI client (Ollama compatible)
        llm_kwargs = dict(kwargs)
        if isinstance(self.max_tokens, int) and self.max_tokens > 0:
            llm_kwargs["max_tokens"] = self.max_tokens
        if self.thinking in ("disabled", "adaptive"):
            llm_kwargs["extra_body"] = {"thinking": {"type": self.thinking}}
        endpoint = self.base_url.rstrip("/")
        if not endpoint.endswith("/v1"):
            endpoint += "/v1"
        self._llm = ChatOpenAI(
            model=self.model,
            api_key="ollama",  # Ollama doesn't require API key
            base_url=endpoint,
            temperature=self.temperature,
            timeout=self.timeout,
            **llm_kwargs
        )
        self._llm = with_context_metadata(
            self._llm, model_name=self.model, base_url=self.base_url,
        )

    def chat(self, messages: List, **kwargs) -> str:
        """
        Send chat request via LangChain.

        Args:
            messages: List of LangChain messages or dicts
            **kwargs: Additional parameters

        Returns:
            Response text
        """
        response = self._llm.invoke(normalize_messages(messages), **kwargs)
        return response.content

    def chat_with_structured_output(
        self,
        messages: List,
        response_format: Dict,
        **kwargs
    ) -> Any:
        """
        Send chat request with structured output.

        Ollama handles this via prompt engineering since it doesn't
        natively support response_format.
        """
        import json

        if response_format.get("type") == "json_object":
            schema = response_format.get("schema", {})
            format_instruction = f"\n\nPlease respond with ONLY valid JSON matching this schema: {json.dumps(schema)}"

            modified_messages = append_instruction(messages, format_instruction)

            result = self.chat(modified_messages, **kwargs)
        else:
            result = self.chat(messages, **kwargs)

        # Parse JSON
        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start != -1 and end != 0:
                return json.loads(result[start:end])
            return json.loads(result)
        except json.JSONDecodeError:
            raise Exception(f"Failed to parse JSON from response: {result}")

    def invoke(self, prompt: str, **kwargs) -> str:
        """Simple invoke with a single prompt string."""
        return self._llm.invoke(prompt, **kwargs)
