"""优势/薄弱学科：按各科均分全市排名的相对位置判定。"""

from __future__ import annotations

import re
from typing import Any

from src.agent.education.query_parse import is_subject_strength_query

# 名次/参赛数：越小越好。前 25% 为前列，后 50% 才算薄弱。
ADVANTAGE_MAX_RATIO = 0.25
WEAK_MIN_RATIO = 0.50

SUBJECT_STRENGTH_RULE = (
    "按各科均分的全市排名相对位置判定，禁止本校各科均分互比，"
    "也禁止把本校各科里名次较差的直接叫薄弱："
    f"名次/参赛数≤{int(ADVANTAGE_MAX_RATIO * 100)}% 为全市前列（优势），"
    f"≥{int(WEAK_MIN_RATIO * 100)}% 为全市靠后（薄弱），中间为中游。"
    "例如全市第7/37（约前19%）仍属前列，不得判薄弱。"
)

_RANK_KEYS = ("city_rank", "rank", "全市排名", "排名")
_N_KEYS = (
    "n_school",
    "n_class",
    "n",
    "学校数",
    "班级数",
    "参赛学校数",
    "参赛班级数",
)
_SUBJECT_KEYS = ("subject", "subject_name", "学科", "科目")
_WEAK_ASK_HINTS = ("薄弱", "弱势", "弱项", "短板")
_ADV_ASK_HINTS = ("优势", "强势", "强项")
_FALSE_WEAK_CLAIM_RE = re.compile(
    r"(?:本次|该校|该班|本场|这[次场])?(?:考试的)?"
    r"薄弱学科(?:是|为)[^。\n]+。?"
)


def classify_city_rank_band(rank: int, n: int) -> str:
    """advantage / mid / weak / unknown。"""
    if int(rank) <= 0 or int(n) <= 0:
        return "unknown"
    ratio = int(rank) / int(n)
    if ratio <= ADVANTAGE_MAX_RATIO:
        return "advantage"
    if ratio >= WEAK_MIN_RATIO:
        return "weak"
    return "mid"


def is_subject_rank_table(columns: list[Any] | None) -> bool:
    blob = " ".join(str(c).strip().lower() for c in (columns or []) if str(c).strip())
    if not blob:
        return False
    has_subject = any(k in blob for k in ("subject", "学科", "科目"))
    has_rank = any(k in blob for k in ("city_rank", "全市排名", "rank", "排名"))
    has_n = any(
        k in blob
        for k in ("n_school", "n_class", "学校数", "班级数", "参赛学校", "参赛班级")
    )
    return has_subject and has_rank and has_n


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return int(float(str(value).strip().replace(",", "").replace("%", "")))
    except (TypeError, ValueError):
        return None


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val not in (None, ""):
            return val
    return None


def _row_as_dict(row: Any, columns: list[Any] | None) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    cols = [str(c) for c in (columns or [])]
    if isinstance(row, (list, tuple)) and cols:
        return {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
    return {}


def label_subject_strength_rows(
    rows: list[Any] | None,
    columns: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """给各科行打上市位判定。"""
    out: list[dict[str, Any]] = []
    for raw in rows or []:
        row = _row_as_dict(raw, columns)
        if not row:
            continue
        subject = str(_pick(row, _SUBJECT_KEYS) or "").strip()
        rank = _as_int(_pick(row, _RANK_KEYS))
        n = _as_int(_pick(row, _N_KEYS))
        if not subject or rank is None or n is None:
            continue
        band = classify_city_rank_band(rank, n)
        out.append(
            {
                "subject": subject,
                "rank": rank,
                "n": n,
                "ratio": rank / n if n else None,
                "band": band,
            }
        )
    return out


def _asks_weak(question: str) -> bool:
    return any(h in (question or "") for h in _WEAK_ASK_HINTS)


def _asks_advantage(question: str) -> bool:
    return any(h in (question or "") for h in _ADV_ASK_HINTS)


def _names(items: list[dict[str, Any]]) -> str:
    return "、".join(str(x.get("subject") or "") for x in items if x.get("subject"))


def _cite(item: dict[str, Any]) -> str:
    rank = int(item["rank"])
    n = int(item["n"])
    pct = max(1, round(rank / n * 100)) if n else 0
    return f"{item['subject']}（第{rank}/{n}，约前{pct}%）"


def format_subject_strength_verdict(
    rows: list[Any] | None,
    columns: list[Any] | None,
    question: str,
) -> str:
    """面向用户的权威判定（可空）。"""
    labeled = label_subject_strength_rows(rows, columns)
    if not labeled:
        return ""
    adv = [x for x in labeled if x["band"] == "advantage"]
    weak = [x for x in labeled if x["band"] == "weak"]
    worst = sorted(labeled, key=lambda x: (x["ratio"] or 0, x["rank"]), reverse=True)
    ask_weak = _asks_weak(question)
    ask_adv = _asks_advantage(question)
    if not ask_weak and not ask_adv:
        ask_weak = True
        ask_adv = True

    lines: list[str] = []
    if ask_weak:
        if weak:
            lines.append(f"按全市排名，薄弱学科为{_names(weak)}（全市后50%）。")
        else:
            lines.append("按全市排名，各科均未落入后50%，没有薄弱学科。")
            rel = [x for x in worst if x["band"] != "weak"][:2]
            if rel:
                lines.append(
                    "相对本校其他科稍靠后但仍属全市"
                    + ("前列" if rel[0]["band"] == "advantage" else "中游")
                    + "的是"
                    + "、".join(_cite(x) for x in rel)
                    + "，不能因为在本校里名次较差就判为薄弱。"
                )
    if ask_adv:
        if adv:
            lines.append(f"按全市排名，优势学科为{_names(adv)}（全市前25%）。")
        elif not ask_weak:
            lines.append("按全市排名，没有落入前25%的优势学科。")
            if worst:
                best = sorted(labeled, key=lambda x: (x["ratio"] or 1, x["rank"]))[:2]
                lines.append("相对最好的是" + "、".join(_cite(x) for x in best) + "。")
    return "\n".join(lines)


def apply_subject_strength_verdict(
    text: str,
    exec_results: list[dict[str, Any]] | None,
    question: str,
) -> str:
    """纠正把全市前列误写成薄弱的结论。"""
    if not is_subject_strength_query(question):
        return text or ""
    verdict = ""
    for er in exec_results or []:
        if not isinstance(er, dict):
            continue
        columns = list(er.get("columns") or [])
        rows = list(er.get("rows") or [])
        if not is_subject_rank_table(columns) and not label_subject_strength_rows(
            rows, columns
        ):
            continue
        verdict = format_subject_strength_verdict(rows, columns, question)
        if verdict:
            break
    if not verdict:
        return text or ""

    out = text or ""
    labeled: list[dict[str, Any]] = []
    for er in exec_results or []:
        if not isinstance(er, dict):
            continue
        labeled = label_subject_strength_rows(
            list(er.get("rows") or []),
            list(er.get("columns") or []),
        )
        if labeled:
            break
    has_weak = any(x["band"] == "weak" for x in labeled)
    if _asks_weak(question) and not has_weak:
        out = _FALSE_WEAK_CLAIM_RE.sub("", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
    if verdict not in out:
        out = (out.rstrip() + "\n\n" + verdict).strip()
    return out


__all__ = [
    "ADVANTAGE_MAX_RATIO",
    "SUBJECT_STRENGTH_RULE",
    "WEAK_MIN_RATIO",
    "apply_subject_strength_verdict",
    "classify_city_rank_band",
    "format_subject_strength_verdict",
    "is_subject_rank_table",
    "label_subject_strength_rows",
]
