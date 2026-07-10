"""从自然语言问题中抽取学生/学校/考试等过滤条件。"""

from __future__ import annotations

import re
from typing import Any

_SCHOOL_SUFFIX = r"(?:中学|学校|学院|大学|附中|分校)"
_SCHOOL_PATTERNS = (
    re.compile(rf"[「\"'【]([^「\"'」】]+{_SCHOOL_SUFFIX})[」\"'】]"),
    re.compile(
        rf"([\u4e00-\u9fff]{{2,4}}(?:市|省|区|县)[\u4e00-\u9fff\d]{{0,12}}{_SCHOOL_SUFFIX})"
    ),
    re.compile(
        rf"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{{2,8}}{_SCHOOL_SUFFIX})(?![\u4e00-\u9fff])"
    ),
)

_STUDENT_PATTERNS = (
    re.compile(r"[「\"'](学生\s*\d+)[」\"']"),
    re.compile(r"(学生\s*\d+)"),
    re.compile(r"[「\"']([\u4e00-\u9fff]{2,4})[」\"']"),
)

_STUDENT_ID_PATTERNS = (
    re.compile(r"学生编号[为：:\s]*([A-Za-z0-9]{4,24})", re.I),
    re.compile(r"学号[为：:\s]*([A-Za-z0-9]{4,24})", re.I),
    re.compile(r"\b(STU\d{4,})\b", re.I),
)


def normalize_student_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def extract_student_id_target(question: str) -> str | None:
    """从问题中抽取学号（如 STU20240003）。"""
    q = (question or "").strip()
    if not q:
        return None
    for pat in _STUDENT_ID_PATTERNS:
        m = pat.search(q)
        if m:
            return str(m.group(1)).strip().upper()
    return None


def extract_student_target(question: str) -> str | None:
    """从问题中抽取目标学生标识（学号或「学生001」等）。"""
    sid = extract_student_id_target(question)
    if sid:
        return sid
    q = (question or "").strip()
    if not q:
        return None
    for pat in _STUDENT_PATTERNS:
        m = pat.search(q)
        if m:
            return re.sub(r"\s+", "", m.group(1))
    return None


def is_individual_student_analysis_query(question: str) -> bool:
    """问题是否针对单个学生（含学号/学生名 + 分析/报告意图）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q):
        return False
    if not extract_student_target(q):
        return False
    hints = (
        "成绩分析",
        "分析报告",
        "学情",
        "知识点",
        "薄弱",
        "加强",
        "诊断",
        "报告",
        "分析",
    )
    return any(h in q for h in hints)


_DISTRICT_RE = re.compile(r"([\u4e00-\u9fff]{2,8}(?:区|县))")


def extract_district_target(question: str) -> str | None:
    """从问题中抽取区县名（如「鼓楼区」）。"""
    q = (question or "").strip()
    if not q:
        return None
    m = _DISTRICT_RE.search(q)
    return m.group(1) if m else None


_CITYWIDE_MARKERS = ("全市", "全域", "市域", "全区县", "各区县")
_CITYWIDE_ANALYSIS_HINTS = ("成绩分析", "详细报告", "质量检测", "期末", "分析报告", "形成报告")


def is_citywide_analysis_query(question: str) -> bool:
    """判断是否为全市/全域范围的考试成绩分析类问题。"""
    q = (question or "").strip()
    if not q:
        return False
    if not any(m in q for m in _CITYWIDE_MARKERS):
        return False
    if extract_school_target(q):
        return False
    return any(h in q for h in _CITYWIDE_ANALYSIS_HINTS) or ("分析" in q and "考试" in q)


_SCHOOL_REPORT_HINTS = (
    "成绩分析",
    "分析报告",
    "形成报告",
    "多维分析",
    "学情报告",
    "诊断报告",
    "详细报告",
    "质量检测",
)


def is_school_exam_report_query(question: str) -> bool:
    """学校/班级范围 + 考试 + 分析报告类问题（非全市、非个人学生）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if not extract_school_target(q):
        return False
    return any(h in q for h in _SCHOOL_REPORT_HINTS) or ("分析" in q and "报告" in q)


def extract_school_target(question: str) -> str | None:
    """从问题中抽取目标学校/机构名（如「南京市第一中学」）。"""
    q = (question or "").strip()
    if not q:
        return None
    _VERB_PREFIXES = ("帮我分析", "帮我", "分析", "查询", "统计", "查看", "了解", "生成")
    for pat in _SCHOOL_PATTERNS:
        m = pat.search(q)
        if m:
            name = re.sub(r"\s+", "", m.group(1))
            for prefix in _VERB_PREFIXES:
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
            if _is_regional_exam_label(name):
                return None
            return name or None
    return None


def _is_regional_exam_label(name: str) -> bool:
    """省/市/区县级行政区划且无校名后缀——多为统考冠名，非学校。"""
    n = re.sub(r"\s+", "", str(name or ""))
    if not n:
        return False
    if re.search(_SCHOOL_SUFFIX + r"$", n):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{1,6}(?:省|市|自治区|区|县|州|盟)", n))


def build_edu_aware_constraints(
    question: str,
    edu_scope: dict[str, Any] | None = None,
    *,
    required_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """合并问题抽取与用户教育权限，供 Planner / DataAnalyst 范围约束。"""
    edu = edu_scope if isinstance(edu_scope, dict) else {}
    role = str(edu.get("edu_role") or "").strip()
    target_school = extract_school_target(question)
    target_student = extract_student_target(question)
    target_student_id = extract_student_id_target(question) or (
        target_student if target_student and re.match(r"^STU\d+$", target_student, re.I) else None
    )
    target_classes: list[str] | None = None

    if role in ("teacher", "school_admin"):
        bound = (str(edu.get("school_name") or "").strip()) or (
            str(edu.get("school_id") or "").strip() or None
        )
        if bound:
            target_school = bound
    if role == "teacher":
        raw = edu.get("class_names")
        if isinstance(raw, list):
            target_classes = [str(x).strip() for x in raw if str(x).strip()]
    if role == "student" and edu.get("student_id"):
        target_student = str(edu.get("student_id")).strip() or target_student

    ctx: dict[str, Any] = {
        "target_school": target_school,
        "target_student": target_student,
        "target_student_id": target_student_id,
        "required_keywords": list(required_keywords or []),
    }
    if role:
        ctx["edu_scope"] = edu
    if target_classes:
        ctx["target_classes"] = target_classes
    return ctx


def student_matches(record_name: str, target: str) -> bool:
    """判断记录中的学生名是否匹配目标学生。"""
    if not target:
        return True
    rn = normalize_student_key(record_name)
    tn = normalize_student_key(target)
    if rn == tn:
        return True
    if tn in rn or rn in tn:
        return True
    # 学生001 vs 001
    rn_digits = re.sub(r"[^\d]", "", rn)
    tn_digits = re.sub(r"[^\d]", "", tn)
    if rn_digits and tn_digits and rn_digits == tn_digits:
        return True
    return False


def format_scope_constraints(constraints: dict[str, Any] | None) -> str:
    """从会话 constraints 生成 Agent 范围提示（DataAnalyst / ToolExpert / Planner 共用）。"""
    raw = constraints if isinstance(constraints, dict) else {}
    parts: list[str] = []
    edu = raw.get("edu_scope")
    if isinstance(edu, dict) and edu.get("edu_role"):
        role = edu.get("edu_role_label") or edu.get("edu_role")
        parts.append(f"当前用户教育角色={role}")
        school = edu.get("school_name") or edu.get("school_id")
        if school:
            parts.append(
                f"权限绑定学校={school}（SQL/工具参数须用 sch.name 或 sc.school_id 过滤该校；"
                "禁止把问题里的「江苏省/南京市」等省市区统考冠名当作学校名）"
            )
        classes = raw.get("target_classes") or edu.get("class_names")
        if isinstance(classes, list) and classes:
            joined = "、".join(str(c) for c in classes[:20])
            parts.append(
                f"权限绑定班级={joined}（可用 sc.class IN (...) 查全部绑定班；"
                "若问题指定其中一班则再收窄到该班）"
            )
        if edu.get("student_id"):
            parts.append(f"权限绑定学号={edu['student_id']}")
    elif raw.get("target_school"):
        parts.append(f"学校/机构={raw['target_school']}")
    if raw.get("target_student"):
        parts.append(f"学生={raw['target_student']}")
    keywords = raw.get("required_keywords") or []
    if keywords:
        kw = "、".join(str(k) for k in keywords[:12])
        parts.append(f"问题关键词={kw}")
    if not parts:
        return "（无额外范围约束，按当前子任务描述理解即可）"
    return (
        "报告/SQL 范围必须与用户数据权限及子任务描述一致（WHERE 须含学校/班级/学生/考试等过滤），"
        "禁止默认查全量学生、全校或多校合并数据。"
        "范围：" + "；".join(parts)
    )


def _normalize_school_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or ""))


def report_matches_school(title: str, html: str, target: str) -> bool:
    """报告标题/HTML 是否属于目标学校（用于过滤偏离报告）。"""
    if not target:
        return True
    blob = f"{title}\n{html[:8000]}"
    blob_n = _normalize_school_key(blob)
    tn = _normalize_school_key(target)
    if tn and tn in blob_n:
        return True
    # 允许匹配校名核心后缀（如「第一中学」）
    core = re.sub(r"^[\u4e00-\u9fff]{2,6}(?:市|省|区|县)", "", tn)
    if len(core) >= 4 and core in blob_n:
        return True
    return False


def extract_upstream_participant_count(report_data: dict[str, Any] | None) -> int | None:
    """从上游 DataAnalyst 子任务推断参考人数，供报告校验。

    优先采用 ``exec_result.row_count``（SQL 实际返回行数），避免 ``final_answer``
    文案中的较小数字拉低人数（如 SQL 40 行但摘要写 20）。
    """
    if not report_data:
        return None
    sql_counts: list[int] = []
    text_counts: list[int] = []
    for st in report_data.get("sub_tasks") or []:
        if st.get("sub_task_agent") == "ToolExpert":
            continue
        fa = str(st.get("final_answer") or "")
        for pat in (
            r"共\s*(\d+)\s*人",
            r"(\d+)\s*名?学生",
            r"参考人数\s*[:：]?\s*(\d+)",
            r"count['\"]?\s*[:=]\s*(\d+)",
            r"返回\s*(\d+)\s*行",
        ):
            m = re.search(pat, fa, flags=re.I)
            if m:
                text_counts.append(int(m.group(1)))
        er = st.get("exec_result") or {}
        cols = [str(c).lower() for c in (er.get("columns") or [])]
        col_blob = "".join(cols)
        rc = er.get("row_count")
        if isinstance(rc, int) and rc > 0:
            if any(
                k in col_blob
                for k in ("student", "学生", "姓名", "name", "学号", "score", "分数")
            ):
                sql_counts.append(rc)
        cached = st.get("score_rows")
        if isinstance(cached, list) and cached:
            sql_counts.append(len(cached))
    if sql_counts:
        return max(sql_counts)
    if text_counts:
        return max(text_counts)
    return None


_SCORE_COL_HINTS = ("score", "分数", "成绩")
_FULL_SCORE_COL_HINTS = ("exam_score", "full_score", "满分")
_DISTRICT_COL_HINTS = ("district", "区县")
_CLASS_COL_HINTS = ("class", "class_name", "班级")
_SCHOOL_COL_HINTS = ("school_name", "学校")
_SUBJECT_COL_HINTS = ("subject", "subject_name", "科目")
_STUDENT_COL_HINTS = ("student_id", "学号", "学生")


def _col_index(cols: list[str], hints: tuple[str, ...]) -> int | None:
    lower = [str(c).lower() for c in cols]
    for i, name in enumerate(lower):
        if any(h in name for h in hints):
            return i
    return None


def _cell(row: Any, idx: int | None) -> Any:
    if idx is None:
        return None
    try:
        if isinstance(row, dict):
            keys = list(row.keys())
            if idx < len(keys):
                return row.get(keys[idx])
            return None
        return row[idx]
    except (IndexError, TypeError, KeyError):
        return None


def extract_score_rows_from_report_data(
    report_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """从上游 DataAnalyst 的 exec_result 还原完整 score_rows。"""
    if not report_data:
        return []
    best: list[dict[str, Any]] = []
    for st in report_data.get("sub_tasks") or []:
        if st.get("sub_task_agent") == "ToolExpert":
            continue
        cached = st.get("score_rows")
        if isinstance(cached, list) and cached:
            if len(cached) > len(best):
                best = [dict(x) for x in cached if isinstance(x, dict)]
        er = st.get("exec_result") or {}
        cols = list(er.get("columns") or [])
        raw_rows = list(er.get("rows") or [])
        if not cols or not raw_rows:
            continue
        si = _col_index(cols, _SCORE_COL_HINTS)
        if si is None:
            continue
        fs_i = _col_index(cols, _FULL_SCORE_COL_HINTS)
        di = _col_index(cols, _DISTRICT_COL_HINTS)
        ci = _col_index(cols, _CLASS_COL_HINTS)
        sch_i = _col_index(cols, _SCHOOL_COL_HINTS)
        sub_i = _col_index(cols, _SUBJECT_COL_HINTS)
        stu_i = _col_index(cols, _STUDENT_COL_HINTS)
        parsed: list[dict[str, Any]] = []
        for row in raw_rows:
            if isinstance(row, dict):
                drow = dict(row)
            else:
                drow = {str(cols[i]): row[i] for i in range(min(len(cols), len(row)))}
            score = drow.get("score")
            if score is None:
                score = _cell(row, si)
            if score is None or score == "":
                continue
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                continue
            out: dict[str, Any] = {"score": score_f}
            field_map = (
                ("exam_score", fs_i),
                ("district", di),
                ("class", ci),
                ("class_name", ci),
                ("school_name", sch_i),
                ("subject", sub_i),
                ("student_id", stu_i),
            )
            for key, idx in field_map:
                val = drow.get(key)
                if val is None and idx is not None:
                    val = _cell(row, idx)
                if val is not None and val != "":
                    out[key] = val
            parsed.append(out)
        if len(parsed) > len(best):
            best = parsed
    return best


def _score_rows_to_exec_result(score_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not score_rows:
        return {"columns": ["score"], "rows": [], "row_count": 0}
    cols = ["score", "exam_score", "district", "class", "student_id", "subject_name"]
    present = [c for c in cols if any(r.get(c) is not None for r in score_rows)]
    if not present:
        present = list(score_rows[0].keys())
    rows = [[r.get(c) for c in present] for r in score_rows]
    return {"columns": present, "rows": rows, "row_count": len(rows)}


def _exec_result_score_count(exec_result: dict[str, Any] | None) -> int:
    if not isinstance(exec_result, dict):
        return 0
    parsed = extract_score_rows_from_report_data(
        {"sub_tasks": [{"exec_result": exec_result}]}
    )
    if parsed:
        return len(parsed)
    rows = exec_result.get("rows") or []
    return max(int(exec_result.get("row_count") or 0), len(rows))


def resolve_stats_input(
    *,
    scores: list[float] | None = None,
    exec_result: dict[str, Any] | None = None,
    last_exec_result: dict[str, Any] | None = None,
    report_data: dict[str, Any] | None = None,
) -> tuple[list[float] | None, dict[str, Any] | None]:
    """为 ``compute_score_stats_tool`` 选取最完整的成绩输入。

    LLM 常从 ``execute_sql`` observation 的 preview（默认 20 行）手抄 ``scores`` /
    ``exec_result``，本函数优先使用运行时保留的完整 ``last_exec_result`` 与
    上游 ``report_data``。
    """
    best_er = exec_result if isinstance(exec_result, dict) else None
    best_n = _exec_result_score_count(best_er)

    for cand in (last_exec_result,):
        if not isinstance(cand, dict):
            continue
        cand_n = _exec_result_score_count(cand)
        if cand_n > best_n:
            best_er = cand
            best_n = cand_n

    upstream_rows = extract_score_rows_from_report_data(report_data)
    expected = extract_upstream_participant_count(report_data)
    if len(upstream_rows) > best_n:
        best_er = _score_rows_to_exec_result(upstream_rows)
        best_n = len(upstream_rows)

    if scores is not None:
        score_n = len([s for s in scores if s is not None])
        if score_n < best_n:
            scores = None
        elif expected and score_n < expected:
            scores = None

    if best_er and expected and best_n < expected and len(upstream_rows) >= expected:
        best_er = _score_rows_to_exec_result(upstream_rows)
        best_n = len(upstream_rows)
        scores = None

    return scores, best_er


def resolve_diagnostic_score_rows(
    *,
    score_rows: list[dict[str, Any]] | None = None,
    report_data: dict[str, Any] | None = None,
    fetch_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """合并 LLM 传入 / 上游 SQL / fetch 成绩行，取最完整的一份。"""
    upstream = extract_score_rows_from_report_data(report_data)
    expected = extract_upstream_participant_count(report_data)
    candidates: list[list[dict[str, Any]]] = []
    if upstream:
        candidates.append(upstream)
    if score_rows:
        candidates.append([dict(r) for r in score_rows if isinstance(r, dict)])
    if isinstance(fetch_data, dict):
        fr = fetch_data.get("score_rows")
        if isinstance(fr, list) and fr:
            candidates.append([dict(r) for r in fr if isinstance(r, dict)])
        sr = fetch_data.get("score_result")
        if isinstance(sr, dict):
            cols = sr.get("columns") or []
            raw = sr.get("rows") or []
            if cols and raw:
                si = _col_index(list(cols), _SCORE_COL_HINTS) or 0
                fs_i = _col_index(list(cols), _FULL_SCORE_COL_HINTS)
                parsed_sr: list[dict[str, Any]] = []
                for row in raw:
                    try:
                        score = float(row[si] if not isinstance(row, dict) else row.get("score", row.get(cols[si])))
                    except (TypeError, ValueError, IndexError, KeyError):
                        continue
                    item: dict[str, Any] = {"score": score}
                    if fs_i is not None:
                        try:
                            fs = row[fs_i] if not isinstance(row, dict) else row.get("exam_score")
                            if fs is not None:
                                item["exam_score"] = float(fs)
                        except (TypeError, ValueError, IndexError, KeyError):
                            pass
                    parsed_sr.append(item)
                if parsed_sr:
                    candidates.append(parsed_sr)
    if not candidates:
        return []
    best = max(candidates, key=len)
    if expected and len(best) < expected and len(upstream) >= expected:
        return upstream
    if expected and len(upstream) > len(best):
        return upstream
    return best


_SUB_TASK_CALL_TOOL_RE = re.compile(
    r"(?:调|调用)\s*([a-z][a-z0-9_]*_tool)\s*[\(:]",
    re.I,
)


def sub_task_called_tools(sub_task: str) -> list[str]:
    """从子任务描述中提取显式「调/调用」的工具名（不含「禁止」句中的提及）。"""
    return [m.group(1).lower() for m in _SUB_TASK_CALL_TOOL_RE.finditer(sub_task or "")]


def sub_task_primary_tool(sub_task: str) -> str:
    """子任务首要动作对应的工具名；无显式调用时返回空串。"""
    tools = sub_task_called_tools(sub_task)
    return tools[0] if tools else ""


def find_upstream_fetch_data(report_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """从上游 fetch 子任务 tool_calls 中取 fetch_subject_diagnosis_data_tool 返回。"""
    if not report_data:
        return None
    for st in reversed(report_data.get("sub_tasks") or []):
        for tc in reversed(st.get("tool_calls") or []):
            if tc.get("tool") != "fetch_subject_diagnosis_data_tool":
                continue
            if not tc.get("success"):
                continue
            data = tc.get("data")
            if isinstance(data, dict) and not data.get("error"):
                return data
    return None


def report_participant_count_conflicts(html: str, expected: int) -> bool:
    """HTML 中是否出现与上游参考人数明显矛盾的数字。"""
    if expected <= 0:
        return False
    blob = html[:12000]
    patterns = (
        r"参考人数\s*(\d+)\s*人",
        r"(?:参考|参与|合计|共)\s*(\d+)\s*人",
        r"(\d+)\s*名?学生",
        r"TOTAL_COUNT[^0-9]*(\d+)",
    )
    for pat in patterns:
        for m in re.finditer(pat, blob, flags=re.I):
            found = int(m.group(1))
            if found != expected:
                return True
    return False


def report_matches_student(title: str, html: str, target: str) -> bool:
    """报告标题/HTML 是否属于目标学生（用于过滤偏离报告）。"""
    if not target:
        return True
    blob = f"{title}\n{html[:4000]}"
    tn = normalize_student_key(target)
    # 提取 blob 中的学生标识
    candidates = set(re.findall(r"学生\s*\d+", blob, flags=re.I))
    candidates |= {re.sub(r"\s+", "", c) for c in candidates}
    if not candidates:
        return True
    for c in candidates:
        cn = normalize_student_key(c)
        if student_matches(cn, tn):
            return True
    return False


__all__ = [
    "build_edu_aware_constraints",
    "extract_district_target",
    "extract_school_target",
    "extract_student_target",
    "format_scope_constraints",
    "is_citywide_analysis_query",
    "normalize_student_key",
    "extract_upstream_participant_count",
    "extract_score_rows_from_report_data",
    "find_upstream_fetch_data",
    "resolve_stats_input",
    "report_matches_school",
    "report_matches_student",
    "report_participant_count_conflicts",
    "sub_task_called_tools",
    "sub_task_primary_tool",
]
