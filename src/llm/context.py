"""Model window metadata, following my-cowork/model_integrations/context.py."""

import hashlib
import json
from urllib.parse import urlsplit

from langchain_core.language_models import BaseChatModel

# Only fill gaps in SDK profiles for the native MiniMax endpoints.
_MINIMAX_CONTEXT_WINDOWS = {
    "minimax-m3.1-flash": 1_000_000,
    "minimax-m3.1-flash-preview": 1_000_000,
    "minimax-m3": 1_000_000,
    **{name: 204_800 for name in (
        "minimax-m2.7", "minimax-m2.7-highspeed", "minimax-m2.5",
        "minimax-m2.5-highspeed", "minimax-m2.1", "minimax-m2.1-highspeed", "minimax-m2",
    )},
}


def selection_key(*, model_name: str, base_url: str | None = None) -> str:
    value = {"model": model_name, "base_url": (base_url or "").rstrip("/")}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def context_limit(model) -> int | None:
    profile = getattr(model, "profile", None)
    value = profile.get("max_input_tokens") if isinstance(profile, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def with_context_metadata(model, *, model_name: str = "", base_url: str | None = None):
    if not isinstance(model, BaseChatModel):
        return model
    if context_limit(model) is None and urlsplit(base_url or "").hostname in {
        "api.minimax.io", "api.minimaxi.com",
    }:
        limit = _MINIMAX_CONTEXT_WINDOWS.get(model_name.lower())
        if limit is not None:
            model = model.model_copy(update={
                "profile": {**(model.profile or {}), "max_input_tokens": limit},
            })
    return model.model_copy(update={"metadata": {
        **(model.metadata or {}), "chat_context_limit": context_limit(model),
        "chat_context_key": selection_key(model_name=model_name, base_url=base_url),
    }})
