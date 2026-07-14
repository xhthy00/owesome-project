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
    re.compile(r"(?<![A-Za-z0-9_])(学生\s*\d+)(?![A-Za-z0-9_])"),
    re.compile(r"[「\"']([\u4e00-\u9fff]{2,4})[」\"']"),
)

# 兼容 STU20240003、2024_STU20260052_YZZX_3884、学生2024_STU... 等
_STUDENT_ID_PATTERNS = (
    re.compile(r"学生编号[为：:\s]*([A-Za-z0-9_]{4,64})", re.I),
    re.compile(r"学号[为：:\s]*([A-Za-z0-9_]{4,64})", re.I),
    re.compile(r"(?:学生|学员)[为：:\s]*([0-9]{4}_STU[A-Za-z0-9_]+)", re.I),
    re.compile(r"(?:学生|学员)([0-9]{4}_STU[A-Za-z0-9_]+)", re.I),
    re.compile(r"\b([0-9]{4}_STU[A-Za-z0-9_]+)\b", re.I),
    re.compile(r"\b(STU[A-Za-z0-9_]{4,})\b", re.I),
)


def normalize_student_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def extract_student_id_target(question: str) -> str | None:
    """从问题中抽取学号（如 STU20240003 / 2024_STU..._YZZX_3884）。"""
    q = (question or "").strip()
    if not q:
        return None
    for pat in _STUDENT_ID_PATTERNS:
        m = pat.search(q)
        if m:
            return str(m.group(1)).strip()
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


def extract_exam_name_hint(question: str) -> str | None:
    """从问题中抽取考试名线索（含「连淮扬镇考试」这类简称）。"""
    q = (question or "").strip()
    if not q:
        return None
    # 完整/半正式考试名
    m = re.search(
        r"([\u4e00-\u9fff]{2,30}?(?:质量检测|模拟考试|学情检测|单元测验|期末考试|期中考试|检测试卷|调研测试))",
        q,
    )
    if m:
        return m.group(1).strip("在的于对")
    for token in ("期中", "期末", "月考", "摸底", "模拟", "单元测验"):
        if token in q:
            return token
    for pat in (
        re.compile(r"在([\u4e00-\u9fffA-Za-z0-9]{2,40}?)(?:考试|测试|检测|调研)"),
        re.compile(r"([\u4e00-\u9fff]{2,20}(?:联考|统考|调研|模拟))(?:考试|测试)?"),
        re.compile(r"([\u4e00-\u9fff]{2,16})考试"),
    ):
        m = pat.search(q)
        if m:
            name = m.group(1).strip("的于对在")
            if name and name not in ("本次", "该次", "此次", "一次", "哪次"):
                return name
    return None


def is_individual_student_analysis_query(question: str) -> bool:
    """问题是否针对单个学生（含学号/学生名 + 分析/得分/报告意图）。"""
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
        "得分情况",
        "得分",
        "成绩",
        "小题",
        "明细",
        "排名",
        "查询",
    )
    if any(h in q for h in hints):
        return True
    # 学生 + 考试 → 默认走个人详细得分/知识点路径
    return "考试" in q


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


def _raw_exec_row_count(exec_result: dict[str, Any] | None) -> int:
    if not isinstance(exec_result, dict):
        return 0
    rows = exec_result.get("rows") or []
    return max(int(exec_result.get("row_count") or 0), len(rows))


def _exec_looks_like_student_scores(exec_result: dict[str, Any]) -> bool:
    """判断 SQL 结果是否含「学生 × 考试 × 分数」明细（而非班级 KPI 聚合）。"""
    cols = [str(c).lower() for c in (exec_result.get("columns") or [])]
    if not cols:
        return False
    blob = " ".join(cols)
    has_student = any(
        k in blob for k in ("student", "学生", "姓名", "学号", "stu_id", "sid")
    )
    has_score = any(k in blob for k in ("score", "分数", "成绩", "总分", "得分"))
    # 纯 KPI 聚合常只有 avg/pass_rate 而无学生列
    has_only_kpi = any(
        k in blob for k in ("avg", "pass_rate", "excellent", "stdev", "均分", "及格率")
    ) and not has_student
    return bool(has_student and has_score and not has_only_kpi)


def _exec_looks_like_item_details(exec_result: dict[str, Any]) -> bool:
    """判断是否含小题/知识点明细（knowledge_name / question_no / score_rate）。"""
    cols = [str(c).lower() for c in (exec_result.get("columns") or [])]
    if not cols:
        return False
    blob = " ".join(cols)
    has_kn = any(k in blob for k in ("knowledge", "知识点"))
    has_q = any(k in blob for k in ("question", "题号", "question_no"))
    has_rate = any(k in blob for k in ("score_rate", "得分率", "rate"))
    return bool((has_kn or has_q) and (has_rate or "score" in blob))


def extract_item_detail_rows_from_report_data(
    report_data: dict[str, Any] | None,
    *,
    student_id: str = "",
) -> list[dict[str, Any]]:
    """从上游 execute_sql / tool_calls 回收小题·知识点行（Agent 常已手查 80+ 行）。"""
    if not report_data:
        return []

    best_rows: list[dict[str, Any]] = []
    best_n = 0

    def _er_to_dicts(er: dict[str, Any]) -> list[dict[str, Any]]:
        cols = [str(c) for c in (er.get("columns") or [])]
        raw = list(er.get("rows") or [])
        if not cols or not raw:
            return []
        out: list[dict[str, Any]] = []
        for row in raw:
            if isinstance(row, dict):
                out.append(dict(row))
            else:
                out.append(dict(zip(cols, row)))
        return out

    def _consider(er: dict[str, Any] | None) -> None:
        nonlocal best_rows, best_n
        if not isinstance(er, dict) or not _exec_looks_like_item_details(er):
            return
        rows = _er_to_dicts(er)
        if len(rows) > best_n:
            best_rows = rows
            best_n = len(rows)

    for st in report_data.get("sub_tasks") or []:
        if not isinstance(st, dict):
            continue
        for key in ("exec_result", "last_exec_result"):
            er = st.get(key)
            if isinstance(er, dict):
                _consider(er)
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            if str(tc.get("tool") or "") != "execute_sql":
                continue
            data = tc.get("data")
            if isinstance(data, dict):
                _consider(data)

    if not best_rows:
        return []

    # 列名归一，便于 aggregate_student_item_insights
    normalized: list[dict[str, Any]] = []
    for r in best_rows:
        lower_map = {str(k).lower(): k for k in r.keys()}

        def _get(*names: str) -> Any:
            for n in names:
                if n in r:
                    return r[n]
                lk = lower_map.get(n.lower())
                if lk is not None:
                    return r[lk]
            return None

        item = {
            "student_id": _get("student_id", "student", "学号", "姓名") or "",
            "exam_name": _get("exam_name", "exam", "考试名称", "考试") or "",
            "question_no": _get("question_no", "题号"),
            "knowledge_name": _get("knowledge_name", "knowledge", "知识点") or "未关联知识点",
            "full_score": _get("full_score", "question_score", "满分"),
            "score": _get("score", "avg_score", "得分"),
            "score_rate": _get("score_rate", "得分率"),
        }
        # 若无 score_rate 但有 score/full_score，现场估算
        if item["score_rate"] is None and item["score"] is not None and item["full_score"]:
            try:
                fs = float(item["full_score"])
                if fs > 0:
                    item["score_rate"] = round(float(item["score"]) * 100.0 / fs, 2)
            except (TypeError, ValueError):
                pass
        normalized.append(item)

    if student_id:
        filtered = [
            r
            for r in normalized
            if student_matches(str(r.get("student_id") or ""), student_id)
        ]
        # 上游 SQL 可能已按该生过滤、未带 student_id 列
        if filtered:
            return filtered
        if all(not str(r.get("student_id") or "").strip() for r in normalized):
            return normalized
        return filtered
    return normalized


def extract_best_exec_result_from_report_data(
    report_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """从上游 DataAnalyst 子任务选取最适合综合报告的完整 exec_result。

    优先选含学生明细的结果；同行数时取更大者。避免误用「班级 KPI 聚合」结果
    导致进步 TOP / 学生档案为空或变成班级汇总。

    除 ``exec_result`` 外，也会从 ``score_rows`` / ``tool_calls`` 里的
    ``execute_sql`` data 回收，避免上游只缓存了其中一种形态。
    """
    if not report_data:
        return None
    best: dict[str, Any] | None = None
    best_key: tuple[int, int] = (-1, -1)  # (is_student_detail, row_count)

    def _consider(er: dict[str, Any] | None) -> None:
        nonlocal best, best_key
        if not isinstance(er, dict):
            return
        cols = er.get("columns") or []
        rows = er.get("rows") or []
        if not cols or not rows:
            return
        n = _raw_exec_row_count(er)
        key = (1 if _exec_looks_like_student_scores(er) else 0, n)
        if key > best_key:
            best = {
                "columns": list(cols),
                "rows": list(rows),
                "row_count": n,
            }
            best_key = key

    def _score_rows_as_exec(score_rows: list[Any]) -> dict[str, Any] | None:
        dicts = [dict(x) for x in score_rows if isinstance(x, dict)]
        if not dicts:
            return None
        cols = list(dicts[0].keys())
        rows = [[d.get(c) for c in cols] for d in dicts]
        return {"columns": cols, "rows": rows, "row_count": len(rows)}

    for st in report_data.get("sub_tasks") or []:
        if st.get("sub_task_agent") == "ToolExpert":
            continue
        # 兼容个别路径误存为 last_exec_result
        _consider(st.get("exec_result") if isinstance(st.get("exec_result"), dict) else None)
        _consider(st.get("last_exec_result") if isinstance(st.get("last_exec_result"), dict) else None)
        cached = st.get("score_rows")
        if isinstance(cached, list) and cached:
            _consider(_score_rows_as_exec(cached))
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            if str(tc.get("tool") or "") != "execute_sql":
                continue
            data = tc.get("data")
            if isinstance(data, dict):
                _consider(data)
    return best


def resolve_comprehensive_table_input(
    *,
    records: list[dict[str, Any]] | None = None,
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    last_exec_result: dict[str, Any] | None = None,
    report_data: dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]] | None,
    list[list[Any]] | None,
    list[str] | None,
    bool,
]:
    """为综合/学生考试报告选取最完整的表格输入。

    LLM 常从 ``execute_sql`` observation 的 preview（默认 20 行）手抄 ``records`` /
    ``rows``，导致全班 50+ 人、多次考试只剩 20 条。本函数优先使用运行时保留的
    完整 ``last_exec_result`` 与上游 ``report_data.exec_result``。

    Returns:
        ``(records, rows, columns, used_upstream)``。若选用上游全量，则
        ``records=None`` 且 ``rows/columns`` 为完整 SQL 结果。
    """
    llm_n = 0
    if records:
        llm_n = max(llm_n, len(records))
    if rows:
        llm_n = max(llm_n, len(rows))

    candidates: list[dict[str, Any]] = []
    for er in (
        last_exec_result if isinstance(last_exec_result, dict) else None,
        extract_best_exec_result_from_report_data(report_data),
    ):
        if not er:
            continue
        cols = er.get("columns") or []
        raw = er.get("rows") or []
        if cols and raw:
            candidates.append(
                {
                    "columns": list(cols),
                    "rows": list(raw),
                    "row_count": _raw_exec_row_count(er),
                }
            )

    best: dict[str, Any] | None = None
    best_key: tuple[int, int] = (-1, -1)
    for er in candidates:
        n = int(er.get("row_count") or 0)
        key = (1 if _exec_looks_like_student_scores(er) else 0, n)
        if key > best_key:
            best = er
            best_key = key

    # 上游是学生明细且明显更全，或 LLM 未传数据 → 改用上游
    if best and best_key[0] == 1 and (best_key[1] > llm_n or llm_n == 0):
        return None, list(best["rows"]), [str(c) for c in best["columns"]], True
    if best and best_key[1] > llm_n:
        return None, list(best["rows"]), [str(c) for c in best["columns"]], True
    if llm_n == 0 and best:
        return None, list(best["rows"]), [str(c) for c in best["columns"]], True
    return records, rows, columns, False


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


def _fetch_bundle_richness(data: dict[str, Any] | None) -> int:
    """衡量 fetch 返回包是否含可用明细（用于优先生效非空源）。"""
    if not isinstance(data, dict) or data.get("error"):
        return -1
    score_result = data.get("score_result")
    has_sr = (
        1
        if isinstance(score_result, dict)
        and (score_result.get("rows") or score_result.get("columns"))
        else 0
    )
    return (
        len(data.get("item_rows") or [])
        + len(data.get("knowledge_rows") or [])
        + len(data.get("score_rows") or [])
        + has_sr
    )


def resolve_subject_diagnosis_fetch_data(
    fetch_data: dict[str, Any] | None = None,
    *,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """解析科目诊断 assemble 用的 fetch 包。

    优先级按「内容最丰富」：显式 fetch_data、同子任务 last_fetch_data、上游 report_data。
    避免 LLM 传空 dict/空数组盖掉真实上游数据。
    """
    candidates: list[dict[str, Any]] = []
    if isinstance(fetch_data, dict) and not fetch_data.get("error"):
        candidates.append(fetch_data)
    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    last = ctx.get("last_fetch_data")
    if isinstance(last, dict) and not last.get("error"):
        candidates.append(last)
    rd = report_data if isinstance(report_data, dict) else ctx.get("report_data")
    upstream = find_upstream_fetch_data(rd if isinstance(rd, dict) else None)
    if isinstance(upstream, dict):
        candidates.append(upstream)

    best: dict[str, Any] | None = None
    best_score = -1
    for cand in candidates:
        score = _fetch_bundle_richness(cand)
        if score > best_score:
            best = cand
            best_score = score
    return best if best_score > 0 else None


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
    "extract_exam_name_hint",
    "extract_school_target",
    "extract_student_id_target",
    "extract_student_target",
    "is_individual_student_analysis_query",
    "extract_student_target",
    "format_scope_constraints",
    "is_citywide_analysis_query",
    "normalize_student_key",
    "extract_upstream_participant_count",
    "extract_best_exec_result_from_report_data",
    "extract_item_detail_rows_from_report_data",
    "extract_score_rows_from_report_data",
    "find_upstream_fetch_data",
    "resolve_subject_diagnosis_fetch_data",
    "resolve_comprehensive_table_input",
    "resolve_stats_input",
    "report_matches_school",
    "report_matches_student",
    "report_participant_count_conflicts",
    "sub_task_called_tools",
    "sub_task_primary_tool",
]
