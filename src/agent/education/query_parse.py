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


def normalize_student_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def extract_student_target(question: str) -> str | None:
    """从问题中抽取目标学生标识（如「学生001」）。"""
    q = (question or "").strip()
    if not q:
        return None
    for pat in _STUDENT_PATTERNS:
        m = pat.search(q)
        if m:
            return re.sub(r"\s+", "", m.group(1))
    return None


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
    """从上游 DataAnalyst 子任务推断参考人数，供报告校验。"""
    if not report_data:
        return None
    counts: list[int] = []
    for st in report_data.get("sub_tasks") or []:
        if st.get("sub_task_agent") == "ToolExpert":
            continue
        fa = str(st.get("final_answer") or "")
        for pat in (
            r"共\s*(\d+)\s*人",
            r"(\d+)\s*名?学生",
            r"参考人数\s*[:：]?\s*(\d+)",
            r"count['\"]?\s*[:=]\s*(\d+)",
        ):
            m = re.search(pat, fa, flags=re.I)
            if m:
                counts.append(int(m.group(1)))
        er = st.get("exec_result") or {}
        cols = [str(c).lower() for c in (er.get("columns") or [])]
        col_blob = "".join(cols)
        if any(k in col_blob for k in ("student", "学生", "姓名", "name", "学号")):
            rc = er.get("row_count")
            if isinstance(rc, int) and rc > 0:
                counts.append(rc)
    if not counts:
        return None
    return min(counts)


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
    "extract_school_target",
    "extract_student_target",
    "format_scope_constraints",
    "normalize_student_key",
    "extract_upstream_participant_count",
    "report_matches_school",
    "report_matches_student",
    "report_participant_count_conflicts",
    "student_matches",
]
