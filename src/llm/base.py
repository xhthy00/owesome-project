"""LangChain-based LLM module."""

from typing import Dict, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, convert_to_messages

from src.common.core.config import get_settings

settings = get_settings()


def normalize_messages(messages) -> List[BaseMessage]:
    return convert_to_messages([
        {**message, "role": message.get("role", message.get("type", "user"))}
        if isinstance(message, dict) else message
        for message in messages
    ])


def append_instruction(messages, instruction: str) -> List[BaseMessage]:
    """Append format instructions without changing the caller's message history."""
    result = normalize_messages(messages)
    if result and isinstance(result[-1], HumanMessage):
        last = result[-1]
        content = (last.content + instruction if isinstance(last.content, str) else
                   [*last.content, {"type": "text", "text": instruction}])
        result[-1] = last.model_copy(update={"content": content})
    else:
        result.append(HumanMessage(content=instruction))
    return result


def get_langchain_messages(
    system_prompt: str,
    user_prompt: str,
    history: Optional[List[Dict[str, str]]] = None
) -> List[BaseMessage]:
    """
    Convert prompts to LangChain message format.

    Args:
        system_prompt: System prompt
        user_prompt: User prompt
        history: Optional conversation history [{"role": "user"|"assistant", "content": "..."}]

    Returns:
        List of LangChain messages
    """
    messages = [SystemMessage(content=system_prompt)] if system_prompt else []
    messages.extend(normalize_messages(history or []))
    messages.append(HumanMessage(content=user_prompt))
    return messages


def parse_history(history: Optional[List[Dict[str, str]]]) -> List[BaseMessage]:
    """Parse history dict to LangChain messages."""
    if not history:
        return []
    return normalize_messages(history)
