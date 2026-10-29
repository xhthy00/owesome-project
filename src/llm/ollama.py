"""Ollama LLM using LangChain's ChatOpenAI (Ollama provides OpenAI-compatible API)."""

from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI


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
        self._llm = ChatOpenAI(
            model=self.model,
            api_key="ollama",  # Ollama doesn't require API key
            base_url=f"{self.base_url.rstrip('/')}/v1",
            temperature=self.temperature,
            timeout=self.timeout,
            **llm_kwargs
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
        from langchain_core.messages import HumanMessage, SystemMessage

        # Convert dict messages to LangChain messages if needed
        langchain_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    langchain_messages.append(SystemMessage(content=content))
                else:
                    langchain_messages.append(HumanMessage(content=content))
            else:
                langchain_messages.append(msg)

        response = self._llm.invoke(langchain_messages, **kwargs)
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

        from langchain_core.messages import HumanMessage

        if response_format.get("type") == "json_object":
            schema = response_format.get("schema", {})
            format_instruction = f"\n\nPlease respond with ONLY valid JSON matching this schema: {json.dumps(schema)}"

            modified_messages = messages.copy()
            if modified_messages:
                last_msg = modified_messages[-1]
                if isinstance(last_msg, dict):
                    last_msg["content"] += format_instruction
                elif hasattr(last_msg, "content"):
                    last_msg = HumanMessage(content=last_msg.content + format_instruction)

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
