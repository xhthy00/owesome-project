"""OpenAI LLM using LangChain."""

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    """保留流式 delta 里的 reasoning_content。

    langchain-openai 不提取 ``reasoning_content``（MiniMax / DeepSeek 等兼容网关用它
    下发思考过程），这里补回到 ``additional_kwargs``，供上层作为思考流下发；
    该字段不会被回传给模型（``_convert_message_to_dict`` 只取 content/tool_calls）。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None:
            return generation
        choices = chunk.get("choices") or (chunk.get("chunk") or {}).get("choices") or []
        delta = (choices[0] or {}).get("delta") if choices else None
        reasoning = delta.get("reasoning_content") if isinstance(delta, dict) else None
        if reasoning:
            message = generation.message
            message.additional_kwargs = {
                **(message.additional_kwargs or {}),
                "reasoning_content": reasoning,
            }
        return generation


class OpenAILLM:
    """OpenAI-compatible LLM using LangChain (supports vLLM, Groq, etc.)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0,
        timeout: int = 120,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        stream_usage: Optional[bool] = None,
        **kwargs
    ):
        self.model = model or "gpt-4o-mini"
        self.api_key = api_key or "dummy"
        self.base_url = base_url
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.stream_usage = stream_usage
        self.extra_params = kwargs

        # Create LangChain chat model
        llm_kwargs = dict(kwargs)
        if isinstance(self.max_tokens, int) and self.max_tokens > 0:
            llm_kwargs["max_tokens"] = self.max_tokens
        # thinking 是 MiniMax-M3 等模型的专有参数，显式配置才发送，避免其他网关 400。
        if self.thinking in ("disabled", "adaptive"):
            llm_kwargs["extra_body"] = {"thinking": {"type": self.thinking}}
        # 流式输出要在末尾追加 usage 分片，否则 chat 的 token 统计会丢。
        # 个别网关不支持 stream_options，可用 LLM_CHAT_STREAM_USAGE=false 关闭。
        if self.stream_usage is not None:
            llm_kwargs["stream_usage"] = self.stream_usage
        self._llm = ReasoningChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
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

        Args:
            messages: List of messages
            response_format: Expected output format (JSON schema)

        Returns:
            Parsed structured response
        """
        import json

        # For JSON mode, append format instruction
        if response_format.get("type") == "json_object":
            schema = response_format.get("schema", {})
            format_instruction = f"\n\nPlease respond with ONLY valid JSON matching this schema: {json.dumps(schema)}"

            # Append to last message
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
