"""学科教研分析报告：一校 × 一场（九科或点名一科）。

均分 / 全市排序 / 层级均分只读 ``tb_score.score`` + ``tb_school.type``。
卷1/卷2 只读小题明细按题型分桶，禁止用总分互减。
名单第一期出明文 ``xm``，不调用隐私开关。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from src.agent.education.bureau_analysis import aggregate_contribution
from src.agent.education.line_reach import pick_col
from src.agent.education.report_types import ReportType, report_type_label
from src.agent.education.schema_mapping import EXAM_JOIN, EXAM_NAME_SQL

__all__ = [
    "PAPER1_PREFIXES",
    "SUBJECTS",
    "chapter_spec",
    "is_lagging_item",
    "match_schools",
    "paper_bucket",
    "school_select_sql",
    "score_select_sql",
    "paper_sum_select_sql",
    "paper_school_avg_select_sql",
    "score_school_avg_select_sql",
    "item_school_select_sql",
    "detail_select_sql",
    "overview_plain_select_sql",
    "research_subjects",
    "strip_school_code",
    "bound_research_school",
    "research_needs_citywide",
    "research_other_school_forbidden",
    "schools_mentioned",
    "build_subject_research_report_data",
]

SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理")


def research_subjects(subject_name: str = "") -> tuple[str, ...]:
    """未点名科目时按九科分批查明细，禁止一条 SQL 扫全市所有小题。"""
    s = _str(subject_name)
    if s in SUBJECTS:
        return (s,)
    return SUBJECTS


_SUBJECT_OV = {
    "语文": "yw",
    "数学": "sx",
    "英语": "yy",
    "物理": "wl",
    "化学": "hx",
    "生物": "sw",
    "政治": "zz",
    "历史": "ls",
    "地理": "dl",
}
_SUBJECT_CONV = {
    "化学": "hxzh",
    "生物": "swzh",
    "政治": "zzzh",
    "地理": "dlzh",
}
_SUBJECT_GRADE = {
    "化学": "hxdj",
    "生物": "swdj",
    "政治": "zzdj",
    "地理": "dldj",
}
_ELITE_RAW = ("语文", "数学", "英语", "物理")
_SCIENCE_ONLY = frozenset({"物理", "化学", "生物"})
_ARTS_ONLY = frozenset({"政治", "历史"})
_TIERS = ("引领校", "支撑校", "发展校", "其他校")
_CITY_PAPER = "市报"

#: 先卷1 后卷2。完形填空必须在填空之前命中卷1；语法填空不得进卷1。
PAPER1_PREFIXES = (
    "单选题",
    "多选题",
    "听力",
    "阅读理解",
    "阅读",
    "完形填空",
    "七选五",
)

_CODE_RE = re.compile(r"^[A-Za-z]\d{2}")
_CAMPUS_RE = re.compile(r"[（(]([^）)]+)[）)]")


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _esc(value: str) -> str:
    return _str(value).replace("'", "''")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    tw = sum(w for _, w in pairs)
    if tw <= 0:
        return None
    return round(sum(v * w for v, w in pairs) / tw, 2)


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def paper_bucket(question_type: str) -> str:
    """题型 → paper1 / paper2 / other。空题型记 other，不丢行。"""
    t = _str(question_type)
    if not t:
        return "other"
    for prefix in PAPER1_PREFIXES:
        if t == prefix or t.startswith(prefix):
            return "paper1"
    return "paper2"


def is_lagging_item(item_rank: int, total_rank: int, threshold_n: int) -> bool:
    """小题全市名次 − 总分全市名次 >= N（1 为最好）。"""
    try:
        return int(item_rank) - int(total_rank) >= int(threshold_n)
    except (TypeError, ValueError):
        return False


def strip_school_code(s_name: str) -> str:
    s = _str(s_name)
    if _CODE_RE.match(s):
        return s[3:]
    return s


def school_code_of(s_name: str) -> str:
    m = _CODE_RE.match(_str(s_name))
    return m.group(0).upper() if m else ""


def campus_of(text: str) -> str:
    m = _CAMPUS_RE.search(_str(text))
    return _str(m.group(1)) if m else ""


def base_school_name(s_name: str) -> str:
    return _CAMPUS_RE.sub("", strip_school_code(s_name)).strip()


def _canon_tier(raw: str) -> str:
    t = _str(raw)
    aliases = {
        "引领": "引领校",
        "引领校": "引领校",
        "支撑": "支撑校",
        "支撑校": "支撑校",
        "发展": "发展校",
        "发展校": "发展校",
        "其他": "其他校",
        "其他校": "其他校",
        "市报": _CITY_PAPER,
        "市报社": _CITY_PAPER,
    }
    return aliases.get(t, t or "其他校")


def chapter_spec(school_type: str) -> dict[str, Any]:
    """层级 → 章节深度。市报/未知按其他校深度，基准仍用其他校。"""
    raw = _str(school_type)
    tier = _canon_tier(raw)
    unknown = (not raw) or (tier not in _TIERS and tier != _CITY_PAPER)
    if tier == "引领校":
        return {
            "tier": tier,
            "lag_n": 2,
            "show_elite": True,
            "lines": ("211线", "特控线"),
            "baseline_tier": "引领校",
            "unspecified": False,
        }
    if tier == "支撑校":
        return {
            "tier": tier,
            "lag_n": 3,
            "show_elite": False,
            "lines": ("特控线", "本科线"),
            "baseline_tier": "支撑校",
            "unspecified": False,
        }
    if tier == "发展校":
        return {
            "tier": tier,
            "lag_n": 3,
            "show_elite": False,
            "lines": ("本科线",),
            "baseline_tier": "发展校",
            "unspecified": False,
        }
    return {
        "tier": "其他校",
        "lag_n": 3,
        "show_elite": False,
        "lines": ("本科线",),
        "baseline_tier": "其他校",
        "unspecified": True if unknown or tier == _CITY_PAPER else False,
        "raw_type": tier,
    }


_SCHOOL_SCOPED_ROLES = frozenset({"school_admin", "teacher"})
_MIN_SCHOOL_LABEL = 3
_FORBIDDEN_OTHER_SCHOOL = (
    "没有权限查看其他学校的学科教研分析报告。当前账号只能查看本校的该报告。"
)
_YIZHONG_IN_Q = re.compile(r"([\u4e00-\u9fff]{2,12}一中)")


def _edu_dict(edu_scope: dict[str, Any] | None) -> dict[str, Any]:
    return edu_scope if isinstance(edu_scope, dict) else {}


def bound_research_school(edu_scope: dict[str, Any] | None, school_query: str) -> str:
    """未点名学校时落到权限校；已点名不改写（他校由 research_other_school_forbidden 拦截）。"""
    asked = _str(school_query)
    if asked:
        return asked
    edu = _edu_dict(edu_scope)
    return _str(edu.get("school_id") or edu.get("school_name"))


def research_needs_citywide(edu_scope: dict[str, Any] | None) -> bool:
    """校管看本校教研报告时用全市数据算排序/层级/贡献分（展示仍只出本校）。老师不放开。"""
    return _str(_edu_dict(edu_scope).get("edu_role")) == "school_admin"


def _school_labels(sch: dict[str, Any]) -> list[str]:
    raw = _str(sch.get("s_name"))
    labels = [raw, strip_school_code(raw), base_school_name(raw), _str(sch.get("id"))]
    out: list[str] = []
    seen: set[str] = set()
    for lab in labels:
        if not lab or lab in seen:
            continue
        if len(lab) < _MIN_SCHOOL_LABEL and not lab.startswith("GZ_"):
            continue
        seen.add(lab)
        out.append(lab)
    return out


def _school_name_aliases(name: str) -> list[str]:
    """「扬州市一中」↔「扬州市第一中学」。光「一中」不展开，避免误中高邮/邗江一中。"""
    n = _str(name)
    if not n:
        return []
    out = [n]
    if n.endswith("一中") and len(n) > 2:
        out.append(n[:-2] + "第一中学")
    elif n.endswith("第一中学") and len(n) > 4:
        out.append(n[:-4] + "一中")
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _research_asked_token(question: str) -> str:
    """问句里的校名。extract_school_target 不含「一中」后缀，这里补抽。"""
    from src.agent.education.query_parse import extract_school_target

    token = _str(extract_school_target(question))
    if token:
        return token
    blob = _str(question).replace(" ", "").replace("\u3000", "")
    m = _YIZHONG_IN_Q.search(blob)
    return _str(m.group(1) if m else "")


def schools_mentioned(schools: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """问句里出现的库内学校（按去码校名命中，不改全局 extract_school_target）。"""
    blob = _str(text)
    if not blob:
        return []
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for sch in schools or []:
        if not isinstance(sch, dict):
            continue
        best = 0
        for lab in _school_labels(sch):
            for token in _school_name_aliases(lab):
                if token in blob:
                    best = max(best, len(token))
        if best:
            scored.append((best, _str(sch.get("id")), sch))
    scored.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for _, sid, sch in scored:
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sch)
    return out


def research_other_school_forbidden(
    edu_scope: dict[str, Any] | None,
    question: str,
    school_arg: str,
    schools: list[dict[str, Any]],
) -> str | None:
    """校管/老师点了其他学校时返回拒绝文案；本校或未点名返回 None。局端不拦截。"""
    edu = _edu_dict(edu_scope)
    if _str(edu.get("edu_role")) not in _SCHOOL_SCOPED_ROLES:
        return None
    bound_key = _str(edu.get("school_id") or edu.get("school_name"))
    if not bound_key:
        return None
    bound = match_schools(schools, bound_key, question)
    bound_ids = {_str(s.get("id")) for s in bound if _str(s.get("id"))}
    bound_labs = {bound_key}
    for s in bound:
        for lab in _school_labels(s):
            bound_labs.update(_school_name_aliases(lab))
    asked_ids: set[str] = set()
    candidates = []
    for cand in (_str(school_arg), _research_asked_token(question)):
        if cand and cand not in candidates:
            candidates.append(cand)
    for asked in candidates:
        matched = match_schools(schools, asked, question)
        ids = {_str(s.get("id")) for s in matched if _str(s.get("id"))}
        asked_ids.update(ids)
        if ids:
            continue
        if asked != bound_key and asked not in bound_labs:
            asked_ids.add("__other__")
    for sch in schools_mentioned(schools, question):
        sid = _str(sch.get("id"))
        if sid:
            asked_ids.add(sid)
    asked_ids.discard("")
    if not asked_ids or asked_ids <= bound_ids:
        return None
    return _FORBIDDEN_OTHER_SCHOOL


def match_schools(
    schools: list[dict[str, Any]],
    query_name: str,
    question: str = "",
) -> list[dict[str, Any]]:
    """校名对齐：去校码后相等或唯一后缀；禁止模糊 LIKE。

    未点校区且能抽出校码 → 合并同码全部校区；问句含（淮海路）等 → 不合并。
    """
    rows = [s for s in (schools or []) if isinstance(s, dict)]
    target = _str(query_name)
    if not target:
        return []
    by_id = [s for s in rows if _str(s.get("id")) == target]
    if by_id:
        campus = campus_of(question)
        if campus:
            return [
                s for s in by_id
                if campus in _str(s.get("s_name"))
            ] or by_id
        code = school_code_of(_str(by_id[0].get("s_name")))
        if code:
            return [s for s in rows if school_code_of(_str(s.get("s_name"))) == code]
        return by_id

    campus = campus_of(question) or campus_of(target)
    target_base = base_school_name(target) or target
    targets = set(_school_name_aliases(target_base))
    targets.add(target)
    exact: list[dict[str, Any]] = []
    suffix: list[dict[str, Any]] = []
    for sch in rows:
        s_name = _str(sch.get("s_name"))
        disp = strip_school_code(s_name)
        base = base_school_name(s_name)
        names = {disp, base, *(_school_name_aliases(disp)), *(_school_name_aliases(base))}
        if names & targets:
            exact.append(sch)
        elif any(
            disp.endswith(t) or base.endswith(t)
            for t in targets
            if t
        ):
            suffix.append(sch)
    matched = exact if exact else (suffix if len(suffix) == 1 else [])
    if not matched:
        return []
    if campus:
        hit = [s for s in matched if campus in _str(s.get("s_name"))]
        return hit
    codes = {school_code_of(_str(s.get("s_name"))) for s in matched}
    codes.discard("")
    if len(codes) == 1:
        code = next(iter(codes))
        return [s for s in rows if school_code_of(_str(s.get("s_name"))) == code]
    return matched


def school_select_sql() -> str:
    return "SELECT id, s_name, type FROM tb_school LIMIT 500"


def score_select_sql(exam_name: str, subject: str = "") -> str:
    lit = _esc(exam_name)
    subj = f" AND sc.subject_name = '{_esc(subject)}'" if _str(subject) else ""
    return (
        "SELECT sc.school_id, sc.student_id, sc.score, sc.subject_name, "
        "ov.xsxz, "
        f"{EXAM_NAME_SQL} AS exam_name\n"
        "FROM tb_score sc\n"
        f"{EXAM_JOIN}\n"
        "LEFT JOIN tb_score_overview ov ON ov.anon_stu_id = sc.student_id "
        f"AND ov.exam_name = {EXAM_NAME_SQL}\n"
        f"WHERE {EXAM_NAME_SQL} = '{lit}'{subj}\n"
        "LIMIT 200000"
    )


def score_school_avg_select_sql(exam_name: str, subject: str = "") -> str:
    """按校×科均分。全市九科约几百行，避免把全市学生行拉进内存。"""
    lit = _esc(exam_name)
    subj = f" AND sc.subject_name = '{_esc(subject)}'" if _str(subject) else ""
    return (
        "SELECT sc.school_id, sc.subject_name,\n"
        "       AVG(sc.score) AS score,\n"
        "       COUNT(*) AS n,\n"
        "       '在籍生' AS xsxz\n"
        "FROM tb_score sc\n"
        f"{EXAM_JOIN}\n"
        "LEFT JOIN tb_score_overview ov ON ov.anon_stu_id = sc.student_id "
        f"AND ov.exam_name = {EXAM_NAME_SQL}\n"
        f"WHERE {EXAM_NAME_SQL} = '{lit}' AND ov.xsxz = '在籍生'{subj}\n"
        "GROUP BY sc.school_id, sc.subject_name\n"
        "LIMIT 20000"
    )


def _sql_paper1_pred(col: str = "eq.question_type") -> str:
    """与 paper_bucket 同一套前缀，SQL 侧先卷1 后卷2。"""
    bits = " OR ".join(f"{col} LIKE '{_esc(p)}%'" for p in PAPER1_PREFIXES)
    return (
        f"({col} IS NOT NULL AND BTRIM(CAST({col} AS VARCHAR)) <> ''"
        f" AND ({bits}))"
    )


def _sql_paper2_pred(col: str = "eq.question_type") -> str:
    return (
        f"({col} IS NOT NULL AND BTRIM(CAST({col} AS VARCHAR)) <> ''"
        f" AND NOT {_sql_paper1_pred(col)})"
    )


def _detail_join_sql(exam_name: str, subject: str = "") -> str:
    lit = _esc(exam_name)
    subj = f" AND sc.subject_name = '{_esc(subject)}'" if _str(subject) else ""
    return (
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id "
        "AND sd.student_id = sc.student_id\n"
        f"{EXAM_JOIN}\n"
        f"WHERE {EXAM_NAME_SQL} = '{lit}'{subj}"
    )


def paper_sum_select_sql(exam_name: str, subject: str = "") -> str:
    """库内按生汇总卷1/卷2，避免拉全市小题再被 LIMIT 截断。"""
    p1 = _sql_paper1_pred()
    p2 = _sql_paper2_pred()
    return (
        "SELECT sc.school_id, sc.student_id, sc.subject_name,\n"
        f"       COALESCE(SUM(CASE WHEN {p1} THEN sd.score ELSE 0 END), 0)"
        " AS paper1,\n"
        f"       COALESCE(SUM(CASE WHEN {p2} THEN sd.score ELSE 0 END), 0)"
        " AS paper2\n"
        + _detail_join_sql(exam_name, subject)
        + "\nGROUP BY sc.school_id, sc.student_id, sc.subject_name\n"
        "LIMIT 200000"
    )


def paper_school_avg_select_sql(exam_name: str, subject: str = "") -> str:
    """先按生汇总卷1/卷2，再按校求均。全市九科只扫一遍明细。"""
    p1 = _sql_paper1_pred()
    p2 = _sql_paper2_pred()
    inner = (
        "SELECT sc.school_id, sc.student_id, sc.subject_name,\n"
        f"       COALESCE(SUM(CASE WHEN {p1} THEN sd.score ELSE 0 END), 0) AS paper1,\n"
        f"       COALESCE(SUM(CASE WHEN {p2} THEN sd.score ELSE 0 END), 0) AS paper2\n"
        + _detail_join_sql(exam_name, subject)
        + "\nGROUP BY sc.school_id, sc.student_id, sc.subject_name"
    )
    return (
        "SELECT school_id, subject_name,\n"
        "       AVG(paper1) AS paper1,\n"
        "       AVG(paper2) AS paper2,\n"
        "       COUNT(*) AS n\n"
        f"FROM ({inner}) t\n"
        "GROUP BY school_id, subject_name\n"
        "LIMIT 20000"
    )


def _item_qno_sql() -> str:
    """question_no 是 varchar、question_id 是 int，不能直接 COALESCE。"""
    return (
        "COALESCE(NULLIF(BTRIM(CAST(sd.question_no AS VARCHAR)), ''), "
        "CAST(sd.question_id AS VARCHAR))"
    )


def item_school_select_sql(exam_name: str, subject: str = "") -> str:
    """库内按校×题号聚合，供拖后腿小题排序。"""
    qno = _item_qno_sql()
    return (
        "SELECT sc.school_id, sc.subject_name,\n"
        f"       {qno} AS question_no,\n"
        "       MAX(eq.question_type) AS question_type,\n"
        "       AVG(sd.score) AS avg_score,\n"
        "       MAX(sd.score) AS max_score,\n"
        "       MIN(sd.score) AS min_score,\n"
        "       COUNT(*) AS n\n"
        + _detail_join_sql(exam_name, subject)
        + "\nGROUP BY sc.school_id, sc.subject_name,\n"
        f"         {qno}\n"
        "LIMIT 50000"
    )


def detail_select_sql(exam_name: str, subject: str = "") -> str:
    """兼容旧名：现为按生卷面汇总，不再返回小题明细行。"""
    return paper_sum_select_sql(exam_name, subject)


def overview_plain_select_sql(
    exam_name: str,
    school_xx: list[str] | None = None,
    thresholds: list[float] | None = None,
) -> str:
    """第一期始终取 xm。有本校名时不拉全市花名册（尖子生/切线生在库内筛）。"""
    lit = _esc(exam_name)
    cols = (
        "SELECT exam_name, xm, xh, anon_stu_id, xx, dq, bj, xkkm, xsxz, "
        "zf6m, yw, sx, yy, wl, hx, sw, zz, ls, dl, "
        "hxzh, hxdj, swzh, swdj, zzzh, zzdj, dlzh, dldj "
        f"FROM tb_score_overview WHERE exam_name = '{lit}' "
        "AND COALESCE(xsxz, '') <> '市报生'"
    )
    names = [_esc(x) for x in (school_xx or []) if _str(x)]
    if not names:
        return cols + " LIMIT 50000"
    xx_sql = ", ".join(f"'{n}'" for n in names)
    phy = "(xkkm LIKE '物%')"
    his = "(xkkm LIKE '史%' OR xkkm LIKE '历%')"
    parts = [
        f"xx IN ({xx_sql})",
        (
            "anon_stu_id IN (SELECT anon_stu_id FROM ("
            "SELECT anon_stu_id, RANK() OVER (ORDER BY zf6m DESC NULLS LAST) AS rk "
            f"FROM tb_score_overview WHERE exam_name = '{lit}' "
            f"AND COALESCE(xsxz, '') <> '市报生' AND {phy}"
            ") t WHERE rk <= 100)"
        ),
        (
            "anon_stu_id IN (SELECT anon_stu_id FROM ("
            "SELECT anon_stu_id, RANK() OVER (ORDER BY zf6m DESC NULLS LAST) AS rk "
            f"FROM tb_score_overview WHERE exam_name = '{lit}' "
            f"AND COALESCE(xsxz, '') <> '市报生' AND {his}"
            ") t WHERE rk <= 30)"
        ),
    ]
    nums: list[float] = []
    for raw in thresholds or []:
        n = _num(raw)
        if n is not None:
            nums.append(n)
    if nums:
        in_list = ", ".join(str(n) for n in nums)
        parts.append(f"zf6m IN ({in_list})")
        thr_union = " UNION ALL ".join(f"SELECT {n} AS thr" for n in nums)
        for track_pred in (phy, his):
            parts.append(
                "anon_stu_id IN ("
                f"SELECT s.anon_stu_id FROM ({thr_union}) b JOIN LATERAL ("
                "SELECT ov.anon_stu_id FROM tb_score_overview ov "
                f"WHERE ov.exam_name = '{lit}' AND COALESCE(ov.xsxz, '') <> '市报生' "
                f"AND ov.zf6m >= b.thr AND {track_pred.replace('xkkm', 'ov.xkkm')} "
                "ORDER BY ov.zf6m ASC NULLS LAST LIMIT 1"
                ") s ON TRUE)"
            )
    return cols + " AND (" + " OR ".join(parts) + ") LIMIT 5000"


def _track_of(xkkm: str) -> str:
    t = _str(xkkm)
    if t.startswith("物"):
        return "物理类"
    if t.startswith("史") or t.startswith("历"):
        return "历史类"
    return ""


def _elite_tracks(subject: str) -> tuple[str, ...]:
    """尖子生：理化生只出物理方向，政史只出历史方向；语数英地理两边都出。"""
    if subject in _SCIENCE_ONLY:
        return ("物理类",)
    if subject in _ARTS_ONLY:
        return ("历史类",)
    return ("物理类", "历史类")


def _critical_tracks(subject: str) -> tuple[str, ...]:
    """临界生：理化生只物理方向，历史只历史方向；政治有物化政，两边都出。"""
    if subject in _SCIENCE_ONLY:
        return ("物理类",)
    if subject == "历史":
        return ("历史类",)
    return ("物理类", "历史类")


def _has_subject_score(row: dict[str, Any], subject: str) -> bool:
    """选考科 overview 未考常为 0/空，不得进尖子生和临界生。"""
    key = _SUBJECT_OV.get(subject, "")
    if not key:
        return False
    val = _num(row.get(key))
    return val is not None and val != 0


def _item_label(qno: str, qtype: str) -> str:
    """Word：纯数字题号且题型是单选/多选时写成「单选1」；写作保留题型名。"""
    n = _str(qno)
    t = _str(qtype)
    if t.startswith("写作"):
        return t
    if re.fullmatch(r"\d+", n):
        if t.startswith("单选"):
            return f"单选{n}"
        if t.startswith("多选"):
            return f"多选{n}"
    return n


def _item_sort_key(item: dict[str, Any]) -> tuple:
    """选择题按题号，再主观题按题号。对齐 Word 表 2.1。"""
    label = _str(item.get("question_no"))
    choice = label.startswith("单选") or label.startswith("多选")
    m = re.search(r"(\d+)(?:-(\d+))?", label)
    major = int(m.group(1)) if m else 10**9
    minor = int(m.group(2)) if m and m.group(2) else 0
    return (0 if choice else 1, major, minor, label)


def _keep_enrolled_score(row: dict[str, Any]) -> bool:
    """均分/排序只计在籍生。无 xsxz 字段时保持旧口径（单测 fixture）。"""
    if "xsxz" not in row:
        return True
    return _str(row.get("xsxz")) == "在籍生"


def parse_overview_plain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """overview 行 → 计算结构。始终带 xm，禁止调用隐私开关。"""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        xkkm = _str(pick_col(row, "xkkm", "xkqk", "xkfx"))
        item: dict[str, Any] = {
            "anon_stu_id": _str(pick_col(row, "anon_stu_id", "student_id")),
            "xx": _str(pick_col(row, "xx", "school_name")),
            "xkkm": xkkm,
            "track": _track_of(xkkm),
            "xsxz": _str(pick_col(row, "xsxz")),
            "xm": _str(pick_col(row, "xm", "姓名", "student_name")),
            "xh": _str(pick_col(row, "xh", "学号")),
            "zf6m": _num(pick_col(row, "zf6m")),
        }
        for key in (
            "yw", "sx", "yy", "wl", "hx", "sw", "zz", "ls", "dl",
            "hxzh", "swzh", "zzzh", "dlzh",
        ):
            item[key] = _num(pick_col(row, key))
        for key in ("hxdj", "swdj", "zzdj", "dldj"):
            item[key] = _str(pick_col(row, key)).upper()
        out.append(item)
    return out


def _rank_map(avg_by_unit: dict[str, float]) -> dict[str, int]:
    ordered = sorted(avg_by_unit.items(), key=lambda kv: (-kv[1], kv[0]))
    ranks: dict[str, int] = {}
    prev_val: float | None = None
    prev_rank = 0
    for i, (key, val) in enumerate(ordered, 1):
        if prev_val is not None and val == prev_val:
            ranks[key] = prev_rank
        else:
            ranks[key] = i
            prev_rank = i
            prev_val = val
    return ranks


def _line_sitters(
    rows: list[dict[str, Any]],
    track: str,
    threshold: float,
) -> list[dict[str, Any]]:
    group = [
        r for r in rows
        if r.get("track") == track
        and r.get("zf6m") is not None
        and float(r["zf6m"]) >= threshold
    ]
    sitters = [r for r in group if abs(float(r["zf6m"]) - threshold) < 1e-6]
    if not sitters and group:
        sitters = [min(group, key=lambda r: float(r["zf6m"]))]
    return sitters


def _research_contribs(
    rows: list[dict[str, Any]],
    contribs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """本报告化生政地贡献分排除未选考 0，不改局端 aggregate_contribution。"""
    out: list[dict[str, Any]] = []
    for item in contribs or []:
        rec = dict(item)
        track = _str(rec.get("track"))
        thr = _num(rec.get("threshold"))
        if track and thr is not None:
            sitters = _line_sitters(rows, track, thr)
            for key in _SUBJECT_CONV.values():
                vals = [
                    float(r[key])
                    for r in sitters
                    if r.get(key) is not None and float(r[key]) != 0
                ]
                rec[key] = sum(vals) / len(vals) if vals else None
        out.append(rec)
    return out


def _school_index(
    schools: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    by_id: dict[str, dict[str, Any]] = {}
    type_by_xx: dict[str, str] = {}
    for sch in schools or []:
        if not isinstance(sch, dict):
            continue
        sid = _str(sch.get("id"))
        if not sid:
            continue
        rec = {
            "id": sid,
            "s_name": _str(sch.get("s_name")),
            "type": _canon_tier(sch.get("type")),
        }
        by_id[sid] = rec
        s_name = rec["s_name"]
        if s_name:
            type_by_xx[s_name] = rec["type"]
    return by_id, type_by_xx


def _is_city_paper_school(sch: dict[str, Any]) -> bool:
    return _canon_tier(sch.get("type")) == _CITY_PAPER


def _build_units(
    by_id: dict[str, dict[str, Any]],
    target_ids: set[str],
) -> dict[str, list[str]]:
    units: dict[str, list[str]] = {"TARGET": sorted(target_ids)}
    rest_by_key: dict[str, list[str]] = {}
    for sid, sch in by_id.items():
        if sid in target_ids or _is_city_paper_school(sch):
            continue
        key = school_code_of(sch["s_name"]) or sid
        rest_by_key.setdefault(key, []).append(sid)
    units.update(rest_by_key)
    return units


def _filter_subject(rows: list[dict[str, Any]], subject: str) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if _str(row.get("subject_name") or row.get("subject")) != subject:
            continue
        out.append(row)
    return out


def _unit_avgs(
    score_rows: list[dict[str, Any]],
    units: dict[str, list[str]],
    *,
    ndigits: int | None = 2,
) -> dict[str, float]:
    id_to_unit: dict[str, str] = {}
    for ukey, ids in units.items():
        for sid in ids:
            id_to_unit[sid] = ukey
    buckets: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in score_rows:
        if not _keep_enrolled_score(row):
            continue
        sid = _str(row.get("school_id"))
        ukey = id_to_unit.get(sid)
        if not ukey:
            continue
        score = _num(row.get("score"))
        if score is None:
            continue
        w = _num(row.get("n"))
        if w is None or w <= 0:
            w = 1.0
        buckets[ukey].append((score, w))
    out: dict[str, float] = {}
    for key, pairs in buckets.items():
        tw = sum(w for _, w in pairs)
        if tw <= 0:
            continue
        avg = sum(v * w for v, w in pairs) / tw
        out[key] = round(avg, ndigits) if ndigits is not None else avg
    return out


def _tier_student_avg(
    score_rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    tier: str,
) -> float | None:
    vals: list[tuple[float, float]] = []
    for row in score_rows:
        if not _keep_enrolled_score(row):
            continue
        sch = by_id.get(_str(row.get("school_id")))
        if not sch or _is_city_paper_school(sch):
            continue
        if sch["type"] != tier:
            continue
        score = _num(row.get("score"))
        if score is None:
            continue
        w = _num(row.get("n"))
        if w is None or w <= 0:
            w = 1.0
        vals.append((score, w))
    return _weighted_mean(vals)


def _tier_first_school_avg(
    avg_by_unit: dict[str, float],
    units: dict[str, list[str]],
    by_id: dict[str, dict[str, Any]],
    tier: str,
) -> float | None:
    vals: list[float] = []
    for ukey, avg in avg_by_unit.items():
        ids = units.get(ukey) or []
        types = {by_id[i]["type"] for i in ids if i in by_id}
        if tier in types:
            vals.append(avg)
    return max(vals) if vals else None


def _student_paper_sums(
    detail_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, float]]:
    """(school_id, student_id) → {paper1, paper2}。

    兼容两种入参：小题行（question_type + item_score）或库内已汇总的 paper1/paper2。
    """
    acc: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"paper1": 0.0, "paper2": 0.0, "_n": 0.0}
    )
    for row in detail_rows or []:
        if not isinstance(row, dict):
            continue
        key = (_str(row.get("school_id")), _str(row.get("student_id")))
        if "paper1" in row or "paper2" in row:
            p1 = _num(row.get("paper1"))
            p2 = _num(row.get("paper2"))
            acc[key]["paper1"] += p1 or 0.0
            acc[key]["paper2"] += p2 or 0.0
            w = _num(row.get("n"))
            acc[key]["_n"] += w if w is not None and w > 0 else 1.0
            continue
        bucket = paper_bucket(_str(row.get("question_type")))
        if bucket not in {"paper1", "paper2"}:
            continue
        score = _num(row.get("item_score") if "item_score" in row else row.get("score"))
        if score is None:
            continue
        acc[key][bucket] += score
        if acc[key]["_n"] <= 0:
            acc[key]["_n"] = 1.0
    return acc


def _paper_unit_avgs(
    sums: dict[tuple[str, str], dict[str, float]],
    units: dict[str, list[str]],
) -> dict[str, dict[str, float | None]]:
    id_to_unit = {}
    for ukey, ids in units.items():
        for sid in ids:
            id_to_unit[sid] = ukey
    buckets: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: {"paper1": [], "paper2": []}
    )
    for (sid, _stu), parts in sums.items():
        ukey = id_to_unit.get(sid)
        if not ukey:
            continue
        w = parts.get("_n") or 1.0
        if w <= 0:
            w = 1.0
        buckets[ukey]["paper1"].append((parts["paper1"], w))
        buckets[ukey]["paper2"].append((parts["paper2"], w))
    out: dict[str, dict[str, float | None]] = {}
    for ukey, parts in buckets.items():
        out[ukey] = {
            "paper1": _weighted_mean(parts["paper1"]),
            "paper2": _weighted_mean(parts["paper2"]),
        }
    return out


def _paper_tier_avg(
    sums: dict[tuple[str, str], dict[str, float]],
    by_id: dict[str, dict[str, Any]],
    tier: str,
    bucket: str,
) -> float | None:
    vals: list[tuple[float, float]] = []
    for (sid, _stu), parts in sums.items():
        sch = by_id.get(sid)
        if not sch or _is_city_paper_school(sch) or sch["type"] != tier:
            continue
        w = parts.get("_n") or 1.0
        if w <= 0:
            w = 1.0
        vals.append((parts[bucket], w))
    return _weighted_mean(vals)


def _school_student_n(score_rows: list[dict[str, Any]]) -> dict[str, int]:
    """每校本科参考人数。小题缺考按 0 分摊进均分。"""
    seen: dict[str, set[str]] = defaultdict(set)
    counted: dict[str, int] = defaultdict(int)
    for row in score_rows or []:
        if not isinstance(row, dict) or not _keep_enrolled_score(row):
            continue
        sid = _str(row.get("school_id"))
        if not sid:
            continue
        stu = _str(row.get("student_id") or row.get("anon_stu_id"))
        if stu:
            seen[sid].add(stu)
            continue
        n = _num(row.get("n"))
        if n is not None and n > 0:
            counted[sid] += int(n)
    out = {k: len(v) for k, v in seen.items()}
    for sid, n in counted.items():
        out[sid] = out.get(sid, 0) + n
    return out


def _lagging_from_school_avgs(
    rows: list[dict[str, Any]],
    units: dict[str, list[str]],
    total_rank: int,
    threshold_n: int,
    school_n: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """拖后腿：入参已是按校×题号的 AVG/MAX/MIN/人数。缺考计 0。"""
    id_to_unit: dict[str, str] = {}
    for ukey, ids in units.items():
        for sid in ids:
            id_to_unit[sid] = ukey
    unit_n: dict[str, int] = defaultdict(int)
    for ukey, ids in units.items():
        for sid in ids:
            unit_n[ukey] += int((school_n or {}).get(sid) or 0)
    stats: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"wsum": 0.0, "n": 0, "max": None, "min": None})
    )
    q_order: list[str] = []
    seen_q: set[str] = set()
    qtypes: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        qno = _str(row.get("question_no") or row.get("question_id"))
        if not qno:
            continue
        if qno not in seen_q:
            seen_q.add(qno)
            q_order.append(qno)
        qt = _str(row.get("question_type") or row.get("qtype"))
        if qt and qno not in qtypes:
            qtypes[qno] = qt
        ukey = id_to_unit.get(_str(row.get("school_id")))
        if not ukey:
            continue
        avg = _num(row.get("avg_score"))
        if avg is None:
            continue
        try:
            n = int(float(row.get("n") or 0))
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            n = 1
        cell = stats[qno][ukey]
        cell["wsum"] += avg * n
        cell["n"] += n
        mx = _num(row.get("max_score"))
        mn = _num(row.get("min_score"))
        if mx is not None:
            cell["max"] = mx if cell["max"] is None else max(cell["max"], mx)
        if mn is not None:
            cell["min"] = mn if cell["min"] is None else min(cell["min"], mn)
    out: list[dict[str, Any]] = []
    for qno in q_order:
        unit_avg: dict[str, float] = {}
        for ukey, cell in stats[qno].items():
            if cell["n"] <= 0:
                continue
            den = unit_n.get(ukey) or 0
            if den > cell["n"]:
                unit_avg[ukey] = cell["wsum"] / den
                if cell["min"] is None or cell["min"] > 0:
                    cell["min"] = 0
            else:
                unit_avg[ukey] = cell["wsum"] / cell["n"]
        if "TARGET" not in unit_avg:
            continue
        ranks = _rank_map(unit_avg)
        item_rank = ranks.get("TARGET")
        if item_rank is None:
            continue
        if not is_lagging_item(item_rank, total_rank, threshold_n):
            continue
        tcell = stats[qno]["TARGET"]
        out.append({
            "question_no": _item_label(qno, qtypes.get(qno, "")),
            "avg": round(unit_avg["TARGET"], 2),
            "rank": item_rank,
            "max": tcell["max"],
            "min": tcell["min"],
            "delta": total_rank - item_rank,
        })
    out.sort(key=_item_sort_key)
    return out


def _lagging_rows(
    detail_rows: list[dict[str, Any]],
    units: dict[str, list[str]],
    total_rank: int,
    threshold_n: int,
    school_n: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    rows = [r for r in (detail_rows or []) if isinstance(r, dict)]
    if rows and "avg_score" in rows[0]:
        return _lagging_from_school_avgs(
            rows, units, total_rank, threshold_n, school_n=school_n,
        )
    target_ids = set(units.get("TARGET") or [])
    id_to_unit = {}
    for ukey, ids in units.items():
        for sid in ids:
            id_to_unit[sid] = ukey
    unit_n: dict[str, int] = defaultdict(int)
    for ukey, ids in units.items():
        for sid in ids:
            unit_n[ukey] += int((school_n or {}).get(sid) or 0)
    by_q: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    target_vals: dict[str, list[float]] = defaultdict(list)
    q_order: list[str] = []
    seen_q: set[str] = set()
    qtypes: dict[str, str] = {}
    for row in detail_rows or []:
        if not isinstance(row, dict):
            continue
        qno = _str(row.get("question_no") or row.get("question_id"))
        if not qno:
            continue
        if qno not in seen_q:
            seen_q.add(qno)
            q_order.append(qno)
        qt = _str(row.get("question_type") or row.get("qtype"))
        if qt and qno not in qtypes:
            qtypes[qno] = qt
        score = _num(row.get("item_score") if "item_score" in row else row.get("score"))
        if score is None:
            continue
        sid = _str(row.get("school_id"))
        ukey = id_to_unit.get(sid)
        if ukey:
            by_q[qno][ukey].append(score)
        if sid in target_ids:
            target_vals[qno].append(score)
    out: list[dict[str, Any]] = []
    for qno in q_order:
        unit_avg: dict[str, float] = {}
        for ukey, vals in by_q[qno].items():
            if not vals:
                continue
            den = unit_n.get(ukey) or 0
            if den > len(vals):
                unit_avg[ukey] = sum(vals) / den
            else:
                unit_avg[ukey] = sum(vals) / len(vals)
        if "TARGET" not in unit_avg:
            continue
        ranks = _rank_map(unit_avg)
        item_rank = ranks.get("TARGET")
        if item_rank is None:
            continue
        if not is_lagging_item(item_rank, total_rank, threshold_n):
            continue
        tv = target_vals.get(qno) or []
        den = unit_n.get("TARGET") or 0
        tmin = min(tv) if tv else None
        if den > len(tv) and (tmin is None or tmin > 0):
            tmin = 0
        out.append({
            "question_no": _item_label(qno, qtypes.get(qno, "")),
            "avg": round(unit_avg["TARGET"], 2),
            "rank": item_rank,
            "max": max(tv) if tv else None,
            "min": tmin,
            "delta": total_rank - item_rank,
        })
    out.sort(key=_item_sort_key)
    return out


def _display_name(target: list[dict[str, Any]], query_name: str) -> str:
    if not target:
        return query_name or "本校"
    bases = {base_school_name(_str(s.get("s_name"))) for s in target}
    if len(bases) == 1:
        return next(iter(bases)) or query_name
    return strip_school_code(_str(target[0].get("s_name"))) or query_name


def _city_paper_xx(type_by_xx: dict[str, str]) -> set[str]:
    return {xx for xx, t in type_by_xx.items() if t == _CITY_PAPER}


def _keep_overview_student(row: dict[str, Any], city_paper_xx: set[str]) -> bool:
    if _str(row.get("xsxz")) == "市报生":
        return False
    return _str(row.get("xx")) not in city_paper_xx


def _elite_for_school(
    rows: list[dict[str, Any]],
    *,
    track: str,
    top_n: int,
    school_xx: set[str],
    city_paper_xx: set[str],
    subject: str,
) -> list[dict[str, Any]]:
    pool = [
        r for r in rows
        if r.get("track") == track
        and r.get("zf6m") is not None
        and _keep_overview_student(r, city_paper_xx)
    ]
    pool.sort(key=lambda r: (-float(r["zf6m"]), _str(r.get("anon_stu_id"))))
    key = _SUBJECT_OV.get(subject, "")
    conv = _SUBJECT_CONV.get(subject, "")
    grade_key = _SUBJECT_GRADE.get(subject, "")
    rank_key = conv or key
    # Word：同分同名次（高于本人的人数 + 1），不是排队序号。
    zf_ranks = _rank_map({
        _str(r.get("anon_stu_id")): float(r["zf6m"]) for r in pool
    })
    subj_vals = {
        _str(r.get("anon_stu_id")): float(r[rank_key])
        for r in pool
        if rank_key and r.get(rank_key) is not None and _has_subject_score(r, subject)
    }
    subj_rank = _rank_map(subj_vals)
    # 文前 30 / 理前 100：含并列，按第 N 名分数卡，不按排队序号截断。
    if top_n <= 0:
        cut: list[dict[str, Any]] = []
    elif len(pool) <= top_n:
        cut = pool
    else:
        cutoff = float(pool[top_n - 1]["zf6m"])
        cut = [r for r in pool if float(r["zf6m"]) >= cutoff]
    out: list[dict[str, Any]] = []
    use_raw_sort = subject in _ELITE_RAW
    for row in cut:
        if _str(row.get("xx")) not in school_xx:
            continue
        if not _has_subject_score(row, subject):
            continue
        sid = _str(row.get("anon_stu_id"))
        item: dict[str, Any] = {
            "rank": zf_ranks.get(sid),
            "xm": _str(row.get("xm")) or sid,
            "zf6m": row.get("zf6m"),
            "raw": row.get(key) if key else None,
            "raw_rank": subj_rank.get(sid),
        }
        if conv:
            item["conv"] = row.get(conv)
            item["grade"] = _str(row.get(grade_key))
        out.append(item)
    out.sort(key=lambda it: (
        int(it["rank"] or 10**9),
        (
            -(float(it["raw"]) if it.get("raw") is not None else float("-inf"))
            if use_raw_sort
            else 0
        ),
        _str(it.get("xm")),
    ))
    return out


def _below_contrib(subject: str, subj_val: float, cut: float, track: str = "") -> bool:
    """低于学科贡献分。

    语文：贡献分四舍五入到整数后再比（115.55→116，管文韬 114 能进）。
    本科附近（105–108）再让 1 分，避免市一中王诗言 106 误入。
    数英物：含等于贡献分（市一中英语 朱方怡 82=82）。
    化学：四舍五入到整数后再比（压线 59.10 的 59 分不出）。
    政治物理方向：向下取整（洪海燕 65 对 65.57 不出）。
    历史：压线名单再让 1 分。
    """
    if subject == "语文":
        rounded = int(cut + 0.5)
        if 105 <= cut < 108:
            return subj_val < rounded - 1
        return subj_val < rounded
    if subject == "历史":
        return subj_val < cut - 1
    if subject == "化学":
        return subj_val < int(cut + 0.5)
    if subject == "政治" and track == "物理类":
        return subj_val < int(cut)
    if subject in _SUBJECT_CONV:
        return subj_val < cut
    return subj_val <= cut


def _critical_for_school(
    rows: list[dict[str, Any]],
    contribs: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    *,
    lines: tuple[str, ...],
    school_xx: set[str],
    city_paper_xx: set[str],
    subject: str,
) -> list[dict[str, Any]]:
    from src.agent.education.line_reach import canon_line_name

    ov_key = _SUBJECT_OV.get(subject, "")
    cmp_key = _SUBJECT_CONV.get(subject) or ov_key
    thr_map: dict[tuple[str, str], float] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        line = canon_line_name(_str(bar.get("line_name")))
        track = _str(bar.get("track"))
        thr = _num(bar.get("threshold"))
        if line and track and thr is not None:
            thr_map[(track, line)] = thr
    contrib_map: dict[tuple[str, str], dict[str, Any]] = {}
    for item in contribs or []:
        contrib_map[(_str(item.get("track")), _str(item.get("line_name")))] = item
    out: list[dict[str, Any]] = []
    for row in rows:
        if _str(row.get("xx")) not in school_xx:
            continue
        if not _keep_overview_student(row, city_paper_xx):
            continue
        if not _has_subject_score(row, subject):
            continue
        zf = _num(row.get("zf6m"))
        subj_val = _num(row.get(cmp_key)) if cmp_key else None
        if zf is None or subj_val is None:
            continue
        if subj_val == 0:
            continue
        track = _str(row.get("track"))
        for line in lines:
            thr = thr_map.get((track, line))
            if thr is None:
                continue
            if not (thr - 10 <= zf < thr):
                continue
            contrib = contrib_map.get((track, line)) or {}
            cut = _num(contrib.get(cmp_key))
            if cut is None:
                continue
            if not _below_contrib(subject, subj_val, cut, track=track):
                continue
            out.append({
                "xm": _str(row.get("xm")) or _str(row.get("anon_stu_id")),
                "xh": _str(row.get("xh")),
                "zf6m": zf,
                "subject_score": subj_val,
                "yw": _num(row.get("yw")) or 0.0,
                "sx": _num(row.get("sx")) or 0.0,
                "yy": _num(row.get("yy")) or 0.0,
                "track": track,
                "line_name": line,
            })
            break
    out.sort(key=_critical_sort_key)
    return out


def _critical_sort_key(row: dict[str, Any]) -> tuple:
    """同分时按语数英、学号、姓名，避免 Word 汪/朱、潘/周对调。"""
    return (
        0 if row.get("track") == "物理类" else 1,
        _str(row.get("line_name")),
        -float(row.get("zf6m") or 0),
        -float(row.get("subject_score") or 0),
        -float(row.get("yw") or 0),
        -float(row.get("sx") or 0),
        -float(row.get("yy") or 0),
        _str(row.get("xh")),
        _str(row.get("xm")),
    )


def _line_label(line: str) -> str:
    return "特招线" if line == "特控线" else line


def _elite_block(title: str, items: list[dict[str, Any]], subject: str) -> str:
    head = f"<h4>{title}</h4>"
    if not items:
        return head + "<p class='edu-sub'>本校无人进入。</p>"
    if subject in _ELITE_RAW:
        headers = ["全市名次", "姓名", "六门总分", "学科分", "学科排名"]
        body = [
            [
                _fmt(it["rank"]), _str(it["xm"]),
                _fmt(it["zf6m"]), _fmt(it["raw"]), _fmt(it["raw_rank"]),
            ]
            for it in items
        ]
        return head + _table(headers, body, numeric_from=2)
    headers = [
        "全市名次", "姓名", "六门总分",
        "原始分", "等级分", "等级", "学科排名",
    ]
    body = [
        [
            _fmt(it["rank"]), _str(it["xm"]),
            _fmt(it["zf6m"]), _fmt(it["raw"]), _fmt(it.get("conv")),
            _str(it.get("grade")) or "—", _fmt(it["raw_rank"]),
        ]
        for it in items
    ]
    return head + _table(headers, body, numeric_from=2)


def _paired_name_table(items: list[dict[str, Any]]) -> str:
    """Word 双列：先填左列再填右列。"""
    n = len(items)
    nrows = max(1, (n + 1) // 2)
    left, right = items[:nrows], items[nrows:]
    dash = ["—", "—", "—"]

    def cells(it: dict[str, Any] | None) -> list[str]:
        if not it:
            return list(dash)
        return [_str(it["xm"]), _fmt(it["zf6m"]), _fmt(it["subject_score"])]

    body: list[list[str]] = []
    for i in range(nrows):
        lft = left[i] if i < len(left) else None
        rgt = right[i] if i < len(right) else None
        body.append(cells(lft) + cells(rgt))
    return _table(
        ["姓名", "总分", "学科分", "姓名", "总分", "学科分"],
        body,
        numeric_cols={1, 2, 4, 5},
    )


def _critical_html(
    items: list[dict[str, Any]],
    lines: tuple[str, ...],
    subject: str,
) -> str:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        buckets[(it["line_name"], it["track"])].append(it)
    cn_ord = "一二三四五六"
    parts: list[str] = []
    allowed = set(_critical_tracks(subject))
    for i, line in enumerate(lines):
        title = _line_label(line)
        ord_s = cn_ord[i] if i < len(cn_ord) else str(i + 1)
        parts.append(f"<h4>（{ord_s}）{title}临界生</h4>")
        for track, direction in (("物理类", "物理方向"), ("历史类", "历史方向")):
            if track not in allowed:
                continue
            rows = list(buckets.get((line, track)) or [])
            rows.sort(key=_critical_sort_key)
            parts.append(f"<h4>{direction}{title}临界生{subject}学科情况</h4>")
            if rows:
                parts.append(_paired_name_table(rows))
            else:
                parts.append("<p class='edu-sub'>暂无临界生。</p>")
            parts.append(
                f"<p class='edu-sub'>注：本表是{direction}{title}下 10 分学生中，"
                "成绩低于学科贡献分的学生名单。</p>"
            )
    return "".join(parts)


def _table(
    headers: list[str],
    rows: list[list[str]],
    *,
    numeric_from: int = 1,
    numeric_cols: set[int] | None = None,
) -> str:
    def _num_cls(i: int) -> str:
        use = i in numeric_cols if numeric_cols is not None else i >= numeric_from
        return "num" if use else ""

    head = "<tr>" + "".join(
        f"<th class='{_num_cls(i)}'>{h}</th>"
        for i, h in enumerate(headers)
    ) + "</tr>"
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class='{_num_cls(i)}'>{c}</td>"
            for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    inner = (
        f'<table class="edu-table"><thead>{head}</thead>'
        f"<tbody>{body}</tbody></table>"
    )
    return f'<div class="edu-table-wrap">{inner}</div>'


def _rank_change(curr: int | None, prev: int | None) -> str:
    if curr is None or prev is None:
        return "—"
    delta = prev - curr
    return str(delta)


def _suggestions(
    *,
    subject: str,
    spec: dict[str, Any],
    avg: float | None,
    tier_avg: float | None,
    rank: int | None,
    rank_chg: str,
    lagging: list[dict[str, Any]],
    elite_n: int,
    critical_n: int,
    first_avg: float | None,
) -> list[str]:
    lines: list[str] = []
    if avg is not None and tier_avg is not None:
        gap = round(avg - tier_avg, 2)
        rel = "高于" if gap >= 0 else "低于"
        rank_txt = f"全市第 {rank}" if rank else "全市名次暂缺"
        lines.append(
            f"{subject}均分 {avg:.2f}，{rel}本层级 {abs(gap):.2f} 分，{rank_txt}，"
            f"较上场排序变化 {rank_chg}。"
        )
    elif avg is not None:
        lines.append(f"{subject}均分 {avg:.2f}。")
    if spec.get("baseline_tier") != "引领校" and avg is not None and first_avg is not None:
        down = round(first_avg - avg, 2)
        if down > 0:
            lines.append(f"比本层级第 1 名低 {down:.2f} 分。")
        else:
            lines.append("为本层级第 1 名。")
    if lagging:
        nos = "、".join(str(x["question_no"]) for x in lagging[:12])
        lines.append(f"拖后腿小题：{nos}。")
    else:
        lines.append("无低于门槛的小题。")
    if spec.get("show_elite"):
        lines.append(f"本校进入理前 100 / 文前 30 本学科相关名单 {elite_n} 人。")
    lines.append(f"临界生 {critical_n} 人（线下 10 分以内且该科低于贡献分）。")
    return lines


def _empty_template(
    *,
    title: str,
    subtitle: str,
    message: str,
    exam_name: str = "",
    school_name: str = "",
) -> dict[str, Any]:
    return {
        "REPORT_TITLE": title,
        "REPORT_SUBTITLE": subtitle,
        "REPORT_TIME": "",
        "REPORT_TYPE": report_type_label(ReportType.SUBJECT_RESEARCH),
        "SCOPE": school_name or "请指定学校",
        "EXAM_NAME": exam_name or "—",
        "PREV_EXAM_NAME": "—",
        "SUBJECT_NAME": "",
        "SCHOOL_NAME": school_name,
        "SCHOOL_TIER": "",
        "SCHOOL_IDS": "",
        "KPI_GRID": "",
        "SUBJECT_HTML": f"<p class='edu-insight-line'>{message}</p>",
        "SUMMARY": f"<p>{message}</p>",
        "RECOMMENDATIONS": "<ul><li>指定学校与考试后重新生成。</li></ul>",
        "_stats": {},
    }


def build_subject_research_report_data(
    *,
    schools: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    prev_score_rows: list[dict[str, Any]] | None = None,
    detail_rows: list[dict[str, Any]] | None = None,
    paper_rows: list[dict[str, Any]] | None = None,
    item_agg_rows: list[dict[str, Any]] | None = None,
    overview_rows: list[dict[str, Any]] | None = None,
    fraction_bars: list[dict[str, Any]] | None = None,
    exam_name: str = "",
    prev_exam_name: str = "",
    school_query: str = "",
    question: str = "",
    subject_name: str = "",
    target_schools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """用已拉取的行组装模板 data。均分只读 score_rows，不用 overview 科目列。"""
    exam = _str(exam_name) or "本场考试"
    q = _str(question)
    target = list(target_schools or match_schools(schools, school_query, q))
    if not target:
        return _empty_template(
            title="学科教研分析报告",
            subtitle="请指定学校",
            message="请指定学校。本报告按一校一场生成，不默认全市、不循环多校。",
            exam_name=exam,
            school_name=_str(school_query),
        )

    by_id, type_by_xx = _school_index(schools)
    target_ids = {_str(s.get("id")) for s in target if _str(s.get("id"))}
    school_xx = {_str(s.get("s_name")) for s in target if _str(s.get("s_name"))}
    display = _display_name(target, school_query)
    raw_type = _canon_tier(target[0].get("type"))
    spec = chapter_spec(raw_type)
    units = _build_units(by_id, target_ids)
    city_paper_xx = _city_paper_xx(type_by_xx)

    want_subject = _str(subject_name)
    subjects = (want_subject,) if want_subject in SUBJECTS else SUBJECTS
    present = {
        _str(r.get("subject_name") or r.get("subject"))
        for r in (score_rows or [])
        if isinstance(r, dict)
    }
    if not want_subject:
        subjects = tuple(s for s in SUBJECTS if s in present) or SUBJECTS

    overview = parse_overview_plain(overview_rows or [])
    city_ov = [r for r in overview if _keep_overview_student(r, city_paper_xx)]
    contribs = _research_contribs(
        city_ov, aggregate_contribution(city_ov, fraction_bars or []),
    )

    sections: list[str] = []
    recs: list[str] = []
    for subject in subjects:
        curr = _filter_subject(score_rows or [], subject)
        prev = _filter_subject(prev_score_rows or [], subject)
        paper_src = paper_rows if paper_rows is not None else detail_rows
        lag_src = item_agg_rows if item_agg_rows is not None else detail_rows
        papers = _filter_subject(paper_src or [], subject)
        lag_details = _filter_subject(lag_src or [], subject)
        avg_map = _unit_avgs(curr, units)
        prev_avg_map = _unit_avgs(prev, units) if prev else {}
        ranks = _rank_map(avg_map)
        prev_ranks = (
            _rank_map(_unit_avgs(prev, units, ndigits=None)) if prev else {}
        )
        school_avg = avg_map.get("TARGET")
        school_rank = ranks.get("TARGET")
        prev_avg = prev_avg_map.get("TARGET")
        prev_rank = prev_ranks.get("TARGET")
        baseline = spec["baseline_tier"]
        tier_avg = _tier_student_avg(curr, by_id, baseline)
        prev_tier_avg = _tier_student_avg(prev, by_id, baseline) if prev else None
        first_avg = _tier_first_school_avg(avg_map, units, by_id, baseline)
        gap = (
            round(school_avg - tier_avg, 2)
            if school_avg is not None and tier_avg is not None
            else None
        )
        prev_gap = (
            round(round(prev_avg, 2) - round(prev_tier_avg, 2), 2)
            if prev_avg is not None and prev_tier_avg is not None
            else None
        )

        sums = _student_paper_sums(papers)
        paper_avgs = _paper_unit_avgs(sums, units)
        p1 = (paper_avgs.get("TARGET") or {}).get("paper1")
        p2 = (paper_avgs.get("TARGET") or {}).get("paper2")
        p1_ranks = _rank_map({
            k: v["paper1"] for k, v in paper_avgs.items() if v.get("paper1") is not None
        })
        p2_ranks = _rank_map({
            k: v["paper2"] for k, v in paper_avgs.items() if v.get("paper2") is not None
        })
        tier_p1 = _paper_tier_avg(sums, by_id, baseline, "paper1")
        tier_p2 = _paper_tier_avg(sums, by_id, baseline, "paper2")
        p1_rank_map_full = p1_ranks
        p2_rank_map_full = p2_ranks

        overall_rows = [
            [
                display,
                _fmt(school_avg),
                _fmt(school_rank),
                _fmt(gap),
                _fmt(p1),
                _fmt(p1_rank_map_full.get("TARGET")),
                _fmt(p2),
                _fmt(p2_rank_map_full.get("TARGET")),
                _fmt(prev_avg),
                _fmt(prev_rank),
                _fmt(prev_gap),
                _rank_change(school_rank, prev_rank),
            ],
            [
                f"{baseline}全体",
                _fmt(tier_avg),
                "—",
                "—",
                _fmt(tier_p1),
                "—",
                _fmt(tier_p2),
                "—",
                _fmt(prev_tier_avg),
                "—",
                "—",
                "—",
            ],
        ]
        overall_html = _table(
            [
                "学校", "均分", "排序", "差值",
                "1卷", "排序", "2卷", "排序",
                "上场均分", "上场排序", "上场差值", "排序变化",
            ],
            overall_rows,
            numeric_from=1,
        )

        total_rank = school_rank or 0
        lagging = (
            _lagging_rows(
                lag_details, units, total_rank, int(spec["lag_n"]),
                school_n=_school_student_n(curr),
            )
            if total_rank
            else []
        )
        if lagging:
            lag_html = _table(
                ["题号", "均分", "排序", "最高分", "最低分", "与总分排序的差值"],
                [
                    [
                        str(x["question_no"]),
                        _fmt(x["avg"]),
                        _fmt(x["rank"]),
                        _fmt(x["max"]),
                        _fmt(x["min"]),
                        _fmt(x["delta"]),
                    ]
                    for x in lagging
                ],
                numeric_from=1,
            )
        else:
            lag_html = "<p class='edu-sub'>无低于门槛的小题。</p>"

        elite_html = ""
        elite_n = 0
        if spec.get("show_elite"):
            allowed = _elite_tracks(subject)
            blocks: list[str] = []
            if "物理类" in allowed:
                phy = _elite_for_school(
                    overview, track="物理类", top_n=100,
                    school_xx=school_xx, city_paper_xx=city_paper_xx, subject=subject,
                )
                elite_n += len(phy)
                blocks.append(_elite_block("物理方向（理前 100）", phy, subject))
            if "历史类" in allowed:
                his = _elite_for_school(
                    overview, track="历史类", top_n=30,
                    school_xx=school_xx, city_paper_xx=city_paper_xx, subject=subject,
                )
                elite_n += len(his)
                blocks.append(_elite_block("历史方向（文前 30）", his, subject))
            elite_html = "".join(blocks)

        crit = _critical_for_school(
            overview, contribs, fraction_bars or [],
            lines=tuple(spec["lines"]),
            school_xx=school_xx,
            city_paper_xx=city_paper_xx,
            subject=subject,
        )
        crit_html = _critical_html(crit, tuple(spec["lines"]), subject)

        sug = _suggestions(
            subject=subject,
            spec=spec,
            avg=school_avg,
            tier_avg=tier_avg,
            rank=school_rank,
            rank_chg=_rank_change(school_rank, prev_rank),
            lagging=lagging,
            elite_n=elite_n,
            critical_n=len(crit),
            first_avg=first_avg,
        )
        recs.extend(sug[:2])
        sug_html = "<ul>" + "".join(f"<li>{x}</li>" for x in sug) + "</ul>"

        block = (
            f"<section class='edu-card'><h2>{subject}</h2>"
            "<h3>一、整体情况</h3>"
            + overall_html
            + "<p class='edu-sub'>卷1/卷2 由逐题明细按题型归集，均分与卷1+卷2 "
            "允许存在微小出入。排序范围为本场已入库学校（不含市报）。"
            "层级均分按 tb_school.type 全体学生平均，对比基准为"
            f"{baseline}。</p>"
            "<h3>二、小题分（仅拖后腿）</h3>"
            + f"<p class='edu-sub'>门槛：小题全市名次比总分名次低 {spec['lag_n']} 名及以上。"
            "试题立意不输出。</p>"
            + lag_html
            + "<p class='edu-sub'>小题分统计到整题粒度，主观题小问不单独列出。</p>"
        )
        if spec.get("show_elite"):
            block += "<h3>三、尖子生</h3>" + elite_html
        line_lab = "、".join(_line_label(x) for x in spec["lines"])
        block += (
            f"<h3>{'四' if spec.get('show_elite') else '三'}、临界生（{line_lab}）</h3>"
            + crit_html
            + f"<h3>{'五' if spec.get('show_elite') else '四'}、教学建议</h3>"
            + sug_html
            + "</section>"
        )
        sections.append(block)

    unspecified_note = ""
    if spec.get("unspecified"):
        unspecified_note = "未分层，对比基准用其他校。"
    elif raw_type == _CITY_PAPER:
        unspecified_note = "该校为市报，章节按其他校深度，不进入层级分母。"

    kpi = (
        f'<div class="edu-grid">'
        f'<div class="edu-kpi"><div class="label">学校</div>'
        f'<div class="value">{display}</div></div>'
        f'<div class="edu-kpi"><div class="label">层级</div>'
        f'<div class="value">{spec["tier"]}</div></div>'
        f'<div class="edu-kpi"><div class="label">考试</div>'
        f'<div class="value">{exam}</div></div>'
        f'<div class="edu-kpi"><div class="label">科目</div>'
        f'<div class="value">{want_subject or "九科"}</div></div>'
        f"</div>"
    )
    rec_html = "<ul>" + "".join(f"<li>{x}</li>" for x in recs[:8]) + "</ul>"
    summary = (
        f"<p class='edu-insight-line'>{display} · {exam} · {spec['tier']}。"
        f"{unspecified_note}</p>"
    )
    return {
        "REPORT_TITLE": f"{display} {exam}学科教研分析报告",
        "REPORT_SUBTITLE": f"{spec['tier']} · 对比基准 {spec['baseline_tier']}",
        "REPORT_TIME": "",
        "REPORT_TYPE": report_type_label(ReportType.SUBJECT_RESEARCH),
        "SCOPE": display,
        "EXAM_NAME": exam,
        "PREV_EXAM_NAME": _str(prev_exam_name) or "—",
        "SUBJECT_NAME": want_subject or "九科",
        "SCHOOL_NAME": display,
        "SCHOOL_TIER": spec["tier"],
        "SCHOOL_IDS": " ".join(sorted(target_ids)),
        "KPI_GRID": kpi,
        "SUBJECT_HTML": "".join(sections) or "<p>本场无科目成绩。</p>",
        "SUMMARY": summary,
        "RECOMMENDATIONS": rec_html or "<ul><li>结合拖后腿小题组织针对性讲评。</li></ul>",
        "_stats": {
            "school": display,
            "tier": spec["tier"],
            "exam": exam,
            "subjects": list(subjects),
        },
    }
