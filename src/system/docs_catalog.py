"""系统文档目录。地址留空时使用这里的默认链接。"""

from __future__ import annotations

DEFAULT_DOC_URLS = {
    "commitment": "https://edu.wegoo.ltd:28433/docs/xtsycns.docx",
    "training": "https://edu.wegoo.ltd:28433/docs/xqfxxtpxsp.mp4",
    "manual": "https://edu.wegoo.ltd:28433/docs/yhsc.docx",
}

_DOCS = (
    {
        "key": "commitment",
        "title": "系统使用承诺书",
        "kind": "docx",
        "filename": "系统使用承诺书.docx",
        "summary": "使用系统前请阅读并遵守。",
    },
    {
        "key": "training",
        "title": "培训视频",
        "kind": "video",
        "filename": "培训视频.mp4",
        "summary": "可在线播放，也可下载到本地。",
    },
    {
        "key": "manual",
        "title": "用户手册",
        "kind": "docx",
        "filename": "用户手册.docx",
        "summary": "按实际操作查看功能说明。",
    },
)


def _pick_url(value: str, fallback: str) -> str:
    text = (value or "").strip()
    return text or fallback


def build_doc_catalog(commitment_url: str, training_url: str, manual_url: str) -> list[dict[str, str]]:
    """按固定顺序返回三份文档。传入空字符串时用默认地址。"""
    urls = {
        "commitment": _pick_url(commitment_url, DEFAULT_DOC_URLS["commitment"]),
        "training": _pick_url(training_url, DEFAULT_DOC_URLS["training"]),
        "manual": _pick_url(manual_url, DEFAULT_DOC_URLS["manual"]),
    }
    return [{**item, "url": urls[item["key"]]} for item in _DOCS]


def find_doc(items: list[dict[str, str]], key: str) -> dict[str, str] | None:
    for item in items:
        if item["key"] == key:
            return item
    return None
