"""贡献分口径薄弱学科：差值=贡献分-实际均分，按校类选线、文理分轨。"""

from __future__ import annotations

import re
from typing import Any, Sequence

from src.agent.education.query_parse import canon_school_type
from src.agent.education.score_contribution import SUBJECT_COLS

_SCHOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

GAP_MIN = 1.5
TOP_N = 3

SUBJECT_LABELS: tuple[tuple[str, str], ...] = (
    ("yw", "语文"),
    ("sx", "数学"),
    ("yy", "英语"),
    ("wl", "物理"),
    ("ls", "历史"),
    ("hxzh", "化学"),
    ("swzh", "生物"),
    ("zzzh", "政治"),
    ("dlzh", "地理"),
)

LINE_CODES_BY_SCHOOL_TYPE: dict[str, tuple[str, ...]] = {
    "引领": ("211", "tz"),
    "支撑": ("tz", "bk"),
    "发展": ("bk",),
}
LINE_NAME_BY_CODE = {"211": "211线", "tz": "特控线", "bk": "本科线"}
TRACK_ORDER = ("物理类", "历史类")

__all__ = [
    "GAP_MIN",
    "LINE_CODES_BY_SCHOOL_TYPE",
    "SUBJECT_LABELS",
    "TOP_N",
    "apply_contribution_weak_verdict",
    "contribution_select_sql",
    "evaluate_contribution_weak",
    "format_contribution_weak_verdict",
    "is_school_id_token",
    "lines_for_school_type",
    "pick_class_filter_token",
    "pick_school_filter_token",
    "pick_weak_subjects",
    "school_subject_avg_sql",
]


def is_school_id_token(value: str) -> bool:
    """GZ_… / 纯字母数字校码，不能拿去 xx LIKE。"""
    tok = str(value or "").strip()
    if not tok or re.search(r"[\u4e00-\u9fff]", tok):
        return False
    return bool(_SCHOOL_ID_RE.fullmatch(tok))


def pick_class_filter_token(question: str, class_name: str = "") -> str:
    """班级以「补充：班级=」为准，避免问句里旧班名盖住追问结果。"""
    from src.agent.education.clarification import SLOT_CLASS, extract_filled_slots

    filled = str(extract_filled_slots(question or "").get(SLOT_CLASS) or "").strip()
    arg = str(class_name or "").strip()
    return filled or arg


def pick_school_filter_token(
    *,
    named: str = "",
    arg: str = "",
    bound_name: str = "",
    looked_up: Sequence[str] = (),
) -> str:
    """优先中文校名；脱敏 school_id 只作查找键，不当 LIKE 条件。"""
    for cand in (named, arg, bound_name, *list(looked_up or ())):
        tok = str(cand or "").strip()
        if tok and not is_school_id_token(tok):
            return tok
    return ""


def lines_for_school_type(xxlb: str | None) -> tuple[str, ...]:
    kind = canon_school_type(xxlb)
    if not kind:
        return ()
    return LINE_CODES_BY_SCHOOL_TYPE.get(kind, ())


def pick_weak_subjects(
    contrib: dict[str, Any],
    avgs: dict[str, Any],
    *,
    gap_min: float = GAP_MIN,
    top_n: int = TOP_N,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for col, label in SUBJECT_LABELS:
        c = _num(contrib.get(col))
        a = _num(avgs.get(col))
        if c is None or a is None:
            continue
        gap = round(c - a, 2)
        if gap + 1e-9 < gap_min:
            continue
        items.append(
            {
                "subject": label,
                "col": col,
                "contrib": c,
                "avg": a,
                "gap": gap,
            }
        )
    items.sort(key=lambda x: (-float(x["gap"]), str(x["subject"])))
    return items[: max(0, int(top_n))]


def evaluate_contribution_weak(
    contrib_rows: Sequence[dict[str, Any]],
    avg_rows: Sequence[dict[str, Any]],
    *,
    tracks: Sequence[str] | None = None,
) -> dict[str, Any]:
    wanted = {str(t).strip() for t in (tracks or ()) if str(t).strip()}
    avgs_by_track: dict[str, dict[str, Any]] = {}
    for raw in avg_rows or []:
        track = _canon_track(raw.get("track"))
        if not track or (wanted and track not in wanted):
            continue
        avgs_by_track[track] = dict(raw)
    if not avgs_by_track:
        return {"error": "no_school_avg", "groups": [], "school_type": ""}

    xxlb = ""
    for row in avgs_by_track.values():
        xxlb = str(row.get("xxlb") or xxlb)
        if canon_school_type(xxlb):
            break
    school_type = canon_school_type(xxlb) or ""
    line_codes = lines_for_school_type(xxlb)
    if not line_codes:
        return {
            "error": "unknown_school_type",
            "groups": [],
            "school_type": school_type,
            "xxlb": xxlb,
        }

    contrib_map: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in contrib_rows or []:
        track = _canon_track(raw.get("track"))
        code = _line_code(raw)
        if not track or code not in LINE_NAME_BY_CODE:
            continue
        contrib_map[(track, code)] = dict(raw)
    if not contrib_map:
        return {
            "error": "no_contrib",
            "groups": [],
            "school_type": school_type,
            "xxlb": xxlb,
        }

    groups: list[dict[str, Any]] = []
    for track in TRACK_ORDER:
        if track not in avgs_by_track:
            continue
        avgs = avgs_by_track[track]
        for code in line_codes:
            contrib = contrib_map.get((track, code)) or {}
            weak = pick_weak_subjects(contrib, avgs) if contrib else []
            groups.append(
                {
                    "track": track,
                    "line_code": code,
                    "line_name": LINE_NAME_BY_CODE[code],
                    "school_type": school_type,
                    "n": _int(avgs.get("n")),
                    "missing_contrib": not bool(contrib),
                    "weak": weak,
                }
            )
    return {
        "error": "",
        "groups": groups,
        "school_type": school_type,
        "xxlb": xxlb,
    }


def format_contribution_weak_verdict(
    result: dict[str, Any],
    *,
    exam_name: str = "",
    school_name: str = "",
    class_name: str = "",
) -> str:
    exam = str(exam_name or "").strip()
    school = str(school_name or "").strip()
    cls = str(class_name or "").strip()
    err = str(result.get("error") or "")
    who = school or "该校"
    if cls:
        who = f"{who} {cls}".strip()
    prefix = f"{exam} · {who}".strip(" ·") if exam else who
    if err == "no_school_avg":
        return f"{prefix}未找到在籍成绩，无法按贡献分判定薄弱学科。"
    if err == "unknown_school_type":
        return f"{prefix}无法识别学校类别（引领/支撑/发展），不能按贡献分选预测线。"
    if err == "no_contrib":
        return f"{prefix}该场考试没有导入贡献分。"
    groups = list(result.get("groups") or [])
    if not groups:
        return f"{prefix}没有可判定的贡献分薄弱学科。"
    kind = str(result.get("school_type") or "")
    kind_txt = f"{kind}校" if kind else "该校"
    scope = "班均分" if cls else "校均分"
    lines = [
        f"**{prefix}（{kind_txt}）**按贡献分口径的薄弱学科如下。",
        f"差值 = 贡献分 - {scope}，≥{GAP_MIN} 为薄弱，每条线最多 {TOP_N} 科。",
        "",
        "| 选科 | 预测线 | 科目 | 差值 |",
        "| --- | --- | --- | ---: |",
    ]
    for group in groups:
        track = str(group.get("track") or "") or "—"
        line_name = str(group.get("line_name") or "") or "—"
        if group.get("missing_contrib"):
            lines.append(f"| {track} | {line_name} | 未导入 | — |")
            continue
        weak = list(group.get("weak") or [])
        if not weak:
            lines.append(f"| {track} | {line_name} | 无 | — |")
            continue
        for item in weak:
            subj = str(item.get("subject") or "") or "—"
            lines.append(f"| {track} | {line_name} | {subj} | {_fmt_gap(item.get('gap'))} |")
    return "\n".join(lines)


def contribution_select_sql(exam_name: str, track: str = "") -> str:
    exam = _sql_lit(exam_name)
    sql = (
        "SELECT exam_name, track, line_code, line_name, threshold, "
        + ", ".join(SUBJECT_COLS)
        + f" FROM tb_score_contribution WHERE exam_name = '{exam}'"
    )
    tr = _canon_track(track)
    if tr:
        sql += f" AND track = '{_sql_lit(tr)}'"
    return sql


def school_subject_avg_sql(
    exam_name: str,
    school_name: str,
    *,
    class_name: str = "",
    track: str = "",
) -> str:
    exam = _sql_lit(exam_name)
    school = _sql_lit(school_name)
    avg_cols = ", ".join(
        f"ROUND(AVG({col}) FILTER (WHERE {col} > 0)::numeric, 2) AS {col}"
        for col in SUBJECT_COLS
    )
    sql = (
        "SELECT CASE WHEN xkkm LIKE '物%' THEN '物理类' "
        "WHEN xkkm LIKE '史%' OR xkkm LIKE '历%' THEN '历史类' ELSE '' END AS track, "
        "MAX(xxlb) AS xxlb, COUNT(*) AS n, "
        f"{avg_cols} "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{exam}' AND xsxz = '在籍生' "
        f"AND xx LIKE '%{school}%' "
        "AND (xkkm LIKE '物%' OR xkkm LIKE '史%' OR xkkm LIKE '历%')"
    )
    cls = str(class_name or "").strip()
    if cls:
        sql += f" AND bj LIKE '%{_sql_lit(cls)}%'"
    tr = _canon_track(track)
    if tr == "物理类":
        sql += " AND xkkm LIKE '物%'"
    elif tr == "历史类":
        sql += " AND (xkkm LIKE '史%' OR xkkm LIKE '历%')"
    sql += " GROUP BY 1"
    return sql


def _canon_track(value: Any) -> str:
    text = str(value or "").strip()
    if "物理" in text or text in {"理", "理科"}:
        return "物理类"
    if "历史" in text or text in {"文", "文科"}:
        return "历史类"
    return text if text in TRACK_ORDER else ""


def _line_code(row: dict[str, Any]) -> str:
    code = str(row.get("line_code") or "").strip().lower()
    if code in LINE_NAME_BY_CODE:
        return code
    name = str(row.get("line_name") or "")
    if "211" in name:
        return "211"
    if "特控" in name or "特招" in name:
        return "tz"
    if "本科" in name:
        return "bk"
    return code


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    num = _num(value)
    if num is None:
        return None
    return int(round(num))


def _fmt_gap(value: Any) -> str:
    num = _num(value)
    if num is None:
        return ""
    return f"{num:.2f}".rstrip("0").rstrip(".") or "0"


def _sql_lit(value: str) -> str:
    return str(value or "").replace("'", "''")


def apply_contribution_weak_verdict(
    text: str,
    question: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> str:
    """工具判定即结论，丢掉 Summarizer 的「未能完成」等套话。"""
    from src.agent.education.query_parse import is_contribution_weak_query

    if not is_contribution_weak_query(question):
        return text or ""
    verdict = ""
    for rec in reversed(list(tool_calls or [])):
        if str(rec.get("tool") or "") != "query_contribution_weak_tool":
            continue
        data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        if str(data.get("error") or "").strip() == "missing_school":
            continue
        cand = str(data.get("verdict") or "").strip()
        if not cand:
            cand = str(rec.get("content") or "").split("任务已完成")[0].strip()
        if cand and "需要具体学校" not in cand:
            verdict = cand
            break
    if not verdict:
        return text or ""
    return verdict
