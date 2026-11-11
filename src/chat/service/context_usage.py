"""Report current root-model window separately from accumulated turn usage."""

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages.utils import count_tokens_approximately


class ContextUsageCallback(AsyncCallbackHandler):
    def __init__(self, usage):
        self.usage = usage
        self.prompts = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if (serialized or {}).get("name") == "FallbackChatModel":
            return
        estimate = count_tokens_approximately(
            [message for group in messages for message in group],
            tools=(kwargs.get("invocation_params") or {}).get("tools"),
        )
        metadata = kwargs.get("metadata") or {}
        namespace = metadata.get("langgraph_checkpoint_ns") or ""
        is_root = "|" not in namespace and metadata.get("lc_source") not in {"summarization", "question_assessment", "verified_answer"}
        self.prompts[run_id] = (estimate, metadata if is_root else None)

    async def on_llm_end(self, response, *, run_id, **kwargs):
        prompt = self.prompts.pop(run_id, None)
        if prompt is None:
            return
        estimate, metadata = prompt
        usage = {}
        for group in response.generations:
            for generation in group:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or usage
        input_tokens = usage.get("input_tokens", estimate)
        output_tokens = usage.get("output_tokens", sum(
            max(1, len(str(getattr(generation, "text", ""))) // 3)
            for group in response.generations for generation in group
        ))
        context = None
        if metadata is not None:
            capacity = metadata.get("chat_context_limit")
            context = {
                "context_tokens": input_tokens,
                "context_limit": capacity,
                "context_model": metadata.get("ls_model_name", ""),
                "context_key": metadata.get("chat_context_key", ""),
                "context_estimated": "input_tokens" not in usage,
            }
        await self.usage.record(
            input_tokens, output_tokens, usage.get("total_tokens", input_tokens + output_tokens),
            context=context, estimated=not bool(usage),
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.prompts.pop(run_id, None)
