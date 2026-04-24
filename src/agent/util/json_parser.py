"""容错 JSON 解析。

LLM 输出的 JSON 常常伴有 Markdown 代码块、前后噪声、或末尾逗号，直接
``json.loads`` 会失败。本模块按以下优先级尝试：

1. 提取 ```json ... ``` 或 ``` ... ``` 代码块；
2. 直接 ``json.loads``；
3. 截取首个 ``{`` / ``[`` 到最后一个 ``}`` / ``]`` 之间的子串。

全部失败则抛 ``ValueError``。本函数只负责"尽力解析"，不做结构校验。
"""

from __future__ import annotations

import json
import re
from typing import Any

_CODE_BLOCK_RE = re.compile(r"```(?:json|JSON)?\s*(.+?)\s*```", re.DOTALL)
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)


def _try_parse_one(raw: str) -> Any:
    m = _CODE_BLOCK_RE.search(raw)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw, idx)
            return parsed
        except json.JSONDecodeError:
            continue

    raise ValueError("no valid json found")


def parse_json_tolerant(text: str) -> Any:
    if text is None or not str(text).strip():
        raise ValueError("empty text")

    raw = str(text)
    candidates = [raw]
    without_think = _THINK_BLOCK_RE.sub("", raw)
    if without_think != raw:
        candidates.append(without_think)

    for candidate in candidates:
        try:
            return _try_parse_one(candidate)
        except ValueError:
            continue

    snippet = raw[:200]
    raise ValueError(f"Cannot parse JSON from: {snippet!r}")
