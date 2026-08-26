"""从自然语言问题中抽取学生/学校/考试等过滤条件。"""

from __future__ import annotations

import re
from typing import Any

_SCHOOL_SUFFIX = r"(?:中学|学校|学院|大学|附中|分校)"
# 校名后常接「在/的/高三/数学」，仅排除「附属」等续写，勿要求非汉字边界
_SCHOOL_TRAIL = r"(?!附属)"
_SCHOOL_PATTERNS = (
    re.compile(rf"[「\"'【]([^「\"'」】]+{_SCHOOL_SUFFIX})[」\"'】]"),
    re.compile(
        rf"([\u4e00-\u9fff]{{2,4}}(?:市|省|区|县)[\u4e00-\u9fff\d]{{0,12}}{_SCHOOL_SUFFIX})"
        rf"{_SCHOOL_TRAIL}"
    ),
    re.compile(
        rf"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{{2,8}}{_SCHOOL_SUFFIX}){_SCHOOL_TRAIL}"
    ),
)
_SUBJECT_NAME_TOKENS = (
    "数学", "语文", "英语", "物理", "化学", "生物", "政治", "历史", "地理", "科学",
)

_STUDENT_PATTERNS = (
    re.compile(r"[「\"'](学生\s*\d+)[」\"']"),
    re.compile(r"(?<![A-Za-z0-9_])(学生\s*\d+)(?![A-Za-z0-9_])"),
    # 「学生张三」中文名（排除学生编号；短匹配并停在学情/报告等边界）
    re.compile(
        r"(?<![A-Za-z0-9_])学生"
        r"([\u4e00-\u9fff]{2,4}?)"
        r"(?=学情|报告|成绩|考试|知识点|分析|诊断|薄弱|的|在|，|,|。|！|!|$)"
    ),
    re.compile(r"[「\"']([\u4e00-\u9fff]{2,4})[」\"']"),
)

#: 「学生XXX」中的无效姓名：疑问词/泛指，不是真实学生标识
_INVALID_STUDENT_NAME_TOKENS = frozenset(
    {
        "是谁",
        "哪个",
        "哪位",
        "什么",
        "多少",
        "如何",
        "怎样",
        "最好",
        "最差",
        "名单",
        "人数",
        "成绩",
        "考试",
        "报告",
        "学情",
        "分析",
        "诊断",
        "排名",
    }
)

#: 「班内最高分/最好的学生是谁」——事实查询，不是已知名学生的学情报告
_TOP_STUDENT_LOOKUP_HINTS = (
    "最好的学生是谁",
    "最高分是谁",
    "谁的成绩最高",
    "谁考得最好",
    "谁考第一",
    "第一名是谁",
    "成绩最好的是谁",
    "分数最高的学生",
    "最高分的学生是谁",
    "谁最高",
)

# 学号 token：字母/数字开头，允许 _.- ，且必须含数字（与「学生张三」中文名区分）
_STUDENT_ID_TOKEN = r"(?=[A-Za-z0-9_.-]*\d)[A-Za-z0-9][A-Za-z0-9_.-]{3,63}"

# 旧规则（回滚时恢复下面元组，并删除/注释新规则即可）
# 兼容 STU20240003、2024_STU20260052_YZZX_3884、学生2024_STU... 等
# _STUDENT_ID_PATTERNS = (
#     re.compile(r"学生编号[为：:\s]*([A-Za-z0-9_]{4,64})", re.I),
#     re.compile(r"学号[为：:\s]*([A-Za-z0-9_]{4,64})", re.I),
#     re.compile(r"(?:学生|学员)[为：:\s]*([0-9]{4}_STU[A-Za-z0-9_]+)", re.I),
#     re.compile(r"(?:学生|学员)([0-9]{4}_STU[A-Za-z0-9_]+)", re.I),
#     re.compile(r"\b([0-9]{4}_STU[A-Za-z0-9_]+)\b", re.I),
#     re.compile(r"\b(STU[A-Za-z0-9_]{4,})\b", re.I),
# )

# 新规则：不强制 STU/年份前缀；优先「学生/学号」语境，其次「xxx的成绩」类意图
_STUDENT_ID_PATTERNS = (
    # 高置信：显式编号/学号
    re.compile(rf"学生编号[为：:\s]*({_STUDENT_ID_TOKEN})", re.I),
    re.compile(rf"学号[为：:\s]*({_STUDENT_ID_TOKEN})", re.I),
    # 高置信：学生/学员 + 账号型 token（含 2024_20250102_GZ_… / 2024_STU…）
    re.compile(rf"(?:学生|学员)[为：:\s]*({_STUDENT_ID_TOKEN})", re.I),
    # 兼容旧 STU 裸号
    re.compile(r"\b(STU[A-Za-z0-9_]{4,})\b", re.I),
    # 中置信：像学号的串 + 成绩/报告意图（如「2024_…_5143的成绩」）
    re.compile(
        rf"({_STUDENT_ID_TOKEN})的?(?:成绩|得分|学情|报告|分析|排名|诊断|考试分析)",
        re.I,
    ),
)


def _is_plausible_student_id_token(token: str, *, contextual: bool) -> bool:
    """过滤误抽：学校编码等短下划线串；语境命中时可略宽。"""
    t = str(token or "").strip()
    if not t or len(t) < 4 or len(t) > 64:
        return False
    if not re.search(r"\d", t):
        return False
    if re.match(r"(?i)^STU", t):
        return True
    sep = t.count("_") + t.count("-")
    if contextual:
        # 「学生/学号」后：有分隔或足够长即可
        return sep >= 1 or len(t) >= 8
    # 裸串 /「xxx的成绩」：至少两段分隔，降低 GZ_E884AF1D 这类学校 ID 误伤
    return sep >= 2 and len(t) >= 10


def normalize_student_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def normalize_fullwidth_parentheses(text: str) -> str:
    """将全角括号（）统一为半角 ()，使「高三（10）班」与库内「高三(10)班」一致。"""
    if not text:
        return text
    return text.replace("（", "(").replace("）", ")")


def extract_student_id_target(question: str) -> str | None:
    """从问题中抽取学号（不限定 STU/年份前缀；优先学生/学号语境）。"""
    q = (question or "").strip()
    if not q:
        return None
    for idx, pat in enumerate(_STUDENT_ID_PATTERNS):
        m = pat.search(q)
        if not m:
            continue
        token = str(m.group(1)).strip()
        # 前 3 条为显式语境；其后为裸 STU /「xxx的成绩」
        contextual = idx < 3
        if _is_plausible_student_id_token(token, contextual=contextual):
            return token
    return None


def extract_student_target(question: str) -> str | None:
    """从问题中抽取目标学生标识（学号或「学生001」/「学生张三」等）。"""
    sid = extract_student_id_target(question)
    if sid:
        return sid
    q = (question or "").strip()
    if not q:
        return None
    # 「最好的学生是谁」类：没有具名学生，禁止抽成「学生是谁」
    if is_top_student_lookup_query(q):
        return None
    for pat in _STUDENT_PATTERNS:
        m = pat.search(q)
        if not m:
            continue
        raw = re.sub(r"\s+", "", m.group(1))
        if not raw:
            continue
        name_only = raw[2:] if raw.startswith("学生") else raw
        if name_only in _INVALID_STUDENT_NAME_TOKENS or raw in _INVALID_STUDENT_NAME_TOKENS:
            continue
        # 「学生张三」捕获组仅姓名 → 归一为完整称谓，便于匹配
        if (
            not raw.startswith("学生")
            and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", raw)
            and "学生" + raw in re.sub(r"\s+", "", q)
        ):
            return "学生" + raw
        return raw
    return None


def is_top_student_lookup_query(question: str) -> bool:
    """是否为「某班/某科成绩最好的学生是谁」这类排名事实查询（非具名学情报告）。"""
    q = (question or "").strip()
    if not q:
        return False
    if any(h in q for h in _TOP_STUDENT_LOOKUP_HINTS):
        return True
    if re.search(r"(?:最好|最高|第一名).{0,6}(?:学生|成绩|分数).{0,4}谁", q):
        return True
    if re.search(r"谁.{0,6}(?:最好|最高|第一)", q):
        return True
    return False


def extract_exam_name_hint(question: str) -> str | None:
    """从问题中抽取考试名线索（如「XX联考」「区域名+考试」等任意简称）。"""
    q = normalize_fullwidth_parentheses((question or "").strip())
    if not q:
        return None
    # 「2026届高三3月」类批次简称（可无期末/模拟后缀）
    m = re.search(
        r"(\d{4}届(?:高[一二三]|初[一二三])\d{1,2}月(?:期末|期中|模拟|月考|摸底)?)",
        q,
    )
    if m:
        return m.group(1)
    # 完整/半正式考试名（清洗班级/科目误吞，避免「班数学期末考试」）
    m = re.search(
        r"([\u4e00-\u9fff]{2,30}?(?:质量检测|模拟考试|学情检测|单元测验|期末考试|期中考试|检测试卷|调研测试))",
        q,
    )
    if m:
        cleaned = _clean_exam_name_candidate(m.group(1))
        if cleaned:
            return cleaned
    for token in ("期中", "期末", "月考", "摸底", "模拟", "单元测验"):
        if token in q:
            return token
    for pat in (
        re.compile(r"在([\u4e00-\u9fffA-Za-z0-9]{2,40}?)(?:考试|测试|检测|调研)"),
        # 「高三(10)班的XX考试」——任意简称；后缀不用单独的「调研」（避免「苏北调研数学考试」被截成「苏北」）
        re.compile(r"班的?([\u4e00-\u9fff]{2,20}?)(?:考试|测试|检测)"),
        # 「高三(10)班连淮扬镇数学成绩总览」——班后考试简称 + 科目，可无「考试」二字
        re.compile(
            r"班的?([\u4e00-\u9fff]{2,20}?)"
            r"(?:数学|语文|英语|物理|化学|生物|政治|历史|地理|科学)"
            r"(?:成绩|总览|分析|报告|考试|测试|检测)?"
        ),
        re.compile(r"([\u4e00-\u9fff]{2,20}(?:联考|统考|调研|模拟))(?:考试|测试)?"),
        re.compile(r"([\u4e00-\u9fff]{2,16})考试"),
    ):
        m = pat.search(q)
        if m:
            name = _clean_exam_name_candidate(m.group(1))
            if name:
                return name
    return None


def _clean_exam_name_candidate(raw: str) -> str | None:
    """清洗考试名候选项：去掉班级前缀/科目后缀，过滤无效碎片。"""
    name = str(raw or "").strip("的于对在")
    if not name:
        return None
    # 连续汉字匹配可能把「班」尾巴吃进考试名，先剥掉班级前缀
    name = re.sub(r"^[\u4e00-\u9fff]{0,12}班的?", "", name)
    # 若仍含「…班的XXX」，取最后一段（贪婪吃入学校+班级时）
    tail = re.search(r"班的?([\u4e00-\u9fffA-Za-z0-9]{2,20})$", name)
    if tail:
        name = tail.group(1)
    name = name.strip("的于对在")
    for subj in _SUBJECT_NAME_TOKENS:
        if name.endswith(subj) and len(name) > len(subj):
            name = name[: -len(subj)]
            break
    # 「班数学期末考试」剥班后剩「数学期末考试」→ 再剥科目前缀
    for subj in _SUBJECT_NAME_TOKENS:
        if name.startswith(subj) and len(name) > len(subj):
            rest = name[len(subj) :]
            if rest and (
                any(t in rest for t in ("期中", "期末", "月考", "摸底", "模拟", "联考", "统考", "检测", "考试"))
            ):
                name = rest
            break
    name = name.strip("的于对在")
    if not name or is_vague_exam_name(name):
        return None
    # 丢弃班级语境误吃入的碎片（如「班所有」「所有」「三次」）
    if re.match(
        r"^(?:班|该|本|此|一次|几次|几场|所有|全部|历次|多次|三次|两次)",
        name,
    ):
        return None
    if len(name) < 2:
        return None
    # 像「扬州中学」这种被贪婪扫进考试名的学校串不算考试
    if re.search(r"(?:中学|学校|学院|大学|附中|分校)$", name):
        return None
    if re.fullmatch(r"高[一二三]|初[一二三]|年级|班级", name):
        return None
    return name


def is_vague_exam_name(name: str) -> bool:
    """是否为不可用于 SQL 过滤的模糊考试表述（这几次/本次考试等）。"""
    n = str(name or "").strip()
    if not n:
        return True
    if n in {
        "本次",
        "该次",
        "此次",
        "一次",
        "哪次",
        "本次考试",
        "该次考试",
        "这几次",
        "最近几次",
        "这几场",
        "最近几场",
        "历次考试",
        "多次考试",
        "几次考试",
        "这几次考试",
        "这几次成绩",
    }:
        return True
    if re.fullmatch(r"(?:这|最近|近)?几[次场](?:考试|成绩|的)?", n):
        return True
    if re.fullmatch(r"(?:历次|多次|各次|各场|所有|全部)(?:考试)?", n):
        return True
    return False


def is_individual_student_analysis_query(question: str) -> bool:
    """问题是否针对单个学生（含学号/学生名 + 分析/得分/报告意图）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q):
        return False
    # 班内最高分是谁：走 SQL 事实查询，不走个人学情报告
    if is_top_student_lookup_query(q):
        return False
    if not extract_student_target(q):
        return False
    hints = (
        "成绩分析",
        "分析报告",
        "学情",
        "个人画像",
        "学生画像",
        "个体画像",
        "画像",
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


# 可选吃掉「3月」，避免「3月广陵区」抽成「月广陵区」；区名本身不以「月」开头
_DISTRICT_RE = re.compile(
    r"(?:[0-9]{1,2}月)?((?!月)[\u4e00-\u9fff]{2,6}(?:区|县))"
)
_DISTRICT_BARE_RE = re.compile(
    r"(?:[0-9]{1,2}月)?([\u4e00-\u9fff]{2,3})(?=本科线|特控线|上线)"
)
_DISTRICT_BARE_REJECT = frozenset(
    {
        "高一",
        "高二",
        "高三",
        "初一",
        "初二",
        "初三",
        "南大",
        "清北",
        "本科",
        "特控",
        "体育",
        "美术",
        "音乐",
    }
)


def extract_district_target(question: str) -> str | None:
    """从问题中抽取区县名（如「鼓楼区」）。"""
    q = (question or "").strip()
    if not q:
        return None
    m = _DISTRICT_RE.search(q)
    if m:
        return m.group(1)
    # 「3月广陵本科线」口语省略「区」；不用「达线」以免「班南大达线」误抽
    m = _DISTRICT_BARE_RE.search(q)
    if not m:
        return None
    bare = m.group(1)
    if bare in _DISTRICT_BARE_REJECT or "班" in bare:
        return None
    return bare + "区"


_CITYWIDE_MARKERS = ("全市", "全域", "市域", "全区县", "各区县")
_CITYWIDE_ANALYSIS_HINTS = (
    "成绩分析",
    "详细报告",
    "详细分析",
    "质量检测",
    "期末",
    "分析报告",
    "形成报告",
)
_LINE_REACH_HINTS = (
    "达线",
    "预测线",
    "分数线",
    "特控线",
    "本科线",
    "体育线",
    "美术线",
    "音乐线",
    "211线",
    "985线",
    "特招线",
    "特招",
)


def is_line_reach_query(question: str) -> bool:
    """是否涉及达线/预测线/分数线（事实问或报告都先命中本判定）。"""
    q = (question or "").strip()
    if not q:
        return False
    return any(h in q for h in _LINE_REACH_HINTS)


_LINE_REACH_CITY_SCOPE = ("全市", "全域", "市域", "各区", "各县", "各区县", "全区县")
_LINE_REACH_REPORT_STRONG = ("分析", "报告", "情况", "对比", "环比")
_LINE_REACH_NARROW = ("人数", "达线率", "上线率", "上线人数")
_CLASS_TARGET_RE = re.compile(
    r"高[一二三]\(\d+\)班|"
    r"(初三|初二|初一|高三|高二|高一|九年级|八年级|七年级|六年级|五年级|四年级|三年级)"
    r"[\d班]*\d?班"
)


def extract_class_target(question: str) -> str | None:
    """从问题中抽取班级名（如「高三(18)班」）。"""
    text = normalize_fullwidth_parentheses(question or "")
    if not text:
        return None
    m = _CLASS_TARGET_RE.search(text)
    return m.group(0) if m else None


def is_line_reach_citywide_scope(question: str) -> bool:
    """问句是否明确要求全市/各区对比（LINE_REACH 模板粒度）。"""
    q = (question or "").strip()
    return any(h in q for h in _LINE_REACH_CITY_SCOPE)


def is_line_reach_report_query(question: str) -> bool:
    """全市/各区达线情况分析报告；班/校/单区县或窄问人数/率仍走事实查询。"""
    q = (question or "").strip()
    if not is_line_reach_query(q):
        return False
    if extract_class_target(q):
        return False
    if not is_line_reach_citywide_scope(q):
        return False
    if any(h in q for h in _LINE_REACH_NARROW) and not any(
        h in q for h in _LINE_REACH_REPORT_STRONG
    ):
        return False
    return True


def is_overview_total_query(question: str) -> bool:
    """语数外三门/四门/六门/全科总分均分：走 tb_score_overview，不是单科 tb_score。"""
    q = (question or "").strip()
    if not q:
        return False
    if any(h in q for h in ("语数外", "语数英", "三门均分", "三门总均分", "三门总分")):
        return True
    if "三门" in q and any(h in q for h in ("均分", "排名", "总分")):
        return True
    if any(h in q for h in ("四门总均分", "四门均分", "四门总分", "zf4m")):
        return True
    if any(h in q for h in ("六门总均分", "六门均分", "六门总分", "全科总分", "zf6m", "zf3m")):
        return True
    return False


def _has_reportish(question: str) -> bool:
    return any(h in (question or "") for h in ("分析", "报告", "情况", "对比"))


def is_subject_avg_report_query(question: str) -> bool:
    """区县/学校均分情况报告；单班均分仍走班级总览。"""
    q = (question or "").strip()
    if not q or not _has_reportish(q):
        return False
    if not any(h in q for h in ("均分情况", "各科均分", "三门总均分", "六门总均分", "转换均分")):
        return False
    if any(h in q for h in ("各区", "各县", "各地区", "各校", "各学校", "全市", "尖子生班", "尖子班")):
        return True
    return "均分情况" in q


def is_assign_grade_report_query(question: str) -> bool:
    q = (question or "").strip()
    if not q or not _has_reportish(q):
        return False
    return any(h in q for h in ("ABCDE", "选考等级", "等级赋分", "选考学科ABC"))


def is_rank_bucket_report_query(question: str) -> bool:
    q = (question or "").strip()
    if not q or not _has_reportish(q):
        return False
    return "位次" in q


def is_contribution_report_query(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    return "贡献分" in q and _has_reportish(q)


def is_combo_reach_report_query(question: str) -> bool:
    q = (question or "").strip()
    if not q or not _has_reportish(q):
        return False
    if "选科组合达线" in q or "各选择组合达线" in q:
        return True
    return "物化生" in q and "达线" in q and _has_reportish(q)


def is_elite_roster_report_query(question: str) -> bool:
    q = (question or "").strip()
    if not q or not _has_reportish(q):
        return False
    return any(h in q for h in ("理前100", "文前30", "冲刺清北", "冲刺南大", "高分名单"))


def is_bureau_report_query(question: str) -> bool:
    q = (question or "").strip()
    return any(
        fn(q)
        for fn in (
            is_subject_avg_report_query,
            is_assign_grade_report_query,
            is_rank_bucket_report_query,
            is_contribution_report_query,
            is_combo_reach_report_query,
            is_elite_roster_report_query,
        )
    )


def is_citywide_analysis_query(question: str) -> bool:
    """判断是否为全市/全域范围的考试成绩分析类问题。"""
    q = (question or "").strip()
    if not q:
        return False
    if not any(m in q for m in _CITYWIDE_MARKERS):
        return False
    if extract_school_target(q):
        return False
    if is_line_reach_query(q):
        return False
    if is_bureau_report_query(q):
        return False
    return any(h in q for h in _CITYWIDE_ANALYSIS_HINTS) or ("分析" in q and "考试" in q)


_SCHOOL_REPORT_HINTS = (
    "成绩分析",
    "分析报告",
    "形成报告",
    "多维分析",
    "多维对比",
    "横向对比",
    "横向分析",
    "学情报告",
    "诊断报告",
    "详细报告",
    "详细分析",
    "质量检测",
    "整体分析",
    "班级整体",
    "班级分析",
    "班级报告",
    "科目分析",
    "数学分析",
    "语文分析",
    "英语分析",
)

_STRUCTURED_DIAGNOSTIC_HINTS = (
    "结构化诊断",
    "区域诊断报告",
    "诊断报告三节",
    "全市区县诊断",
)

_CLASS_COMPARISON_HINTS = (
    "各个班级",
    "各班级",
    "各班对比",
    "各班横向",
    "班级对比",
    "班级横向",
    "横向对比",
    "横向分析",
    "横向多维",
    "年级对比",
    "班级排名",
    "年级排名",
    "多维对比",
)

_MULTI_EXAM_HINTS = (
    "所有考试",
    "全部考试",
    "历次考试",
    "多次考试",
    "几次考试",
    "三次考试",
    "两次考试",
    "各次考试",
    "每场考试",
    "各场考试",
    "这几次考试",
    "这几次的",
    "最近几次",
    "这几场",
)


def is_multi_exam_student_analysis_query(question: str) -> bool:
    """单个学生 + 多次/这几次考试（应走学生多次考试趋势报告，非单场诊断）。"""
    q = (question or "").strip()
    if not is_individual_student_analysis_query(q):
        return False
    if any(h in q for h in _MULTI_EXAM_HINTS):
        return True
    if ("这几次" in q or "最近几次" in q or "这几场" in q) and "考试" in q:
        return True
    if ("历次" in q or "多次" in q) and "考试" in q:
        return True
    return False


#: 走势/趋势报告口径（优先于「历次考试→综合分析」）
_TREND_TRACKING_HINTS = (
    "成绩趋势",
    "历次成绩趋势",
    "趋势报告",
    "成绩走势",
    "走势与进退步",
    "进退步分析",
    "历次趋势",
    "折线",
)
_TREND_TRACKING_BLOCKERS = (
    "综合分析",
    "综合报告",
    "综合复盘",
)


def is_trend_tracking_query(question: str) -> bool:
    """班级/主体成绩走势、趋势、进退步 → trend_tracking（非综合 9 维）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if any(b in q for b in _TREND_TRACKING_BLOCKERS):
        return False
    if any(h in q for h in _TREND_TRACKING_HINTS):
        return True
    if "趋势" in q and any(h in q for h in ("成绩", "考试", "班", "均分")):
        return True
    if "走势" in q and any(h in q for h in ("成绩", "考试", "班", "均分")):
        return True
    if "进退步" in q and ("班" in q or "成绩" in q or "考试" in q):
        return True
    return False


def is_multi_exam_class_analysis_query(question: str) -> bool:
    """班级范围 + 多场/历次考试综合分析（应走 comprehensive，非单科诊断）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    # 走势/趋势报告独立路由，不占综合
    if is_trend_tracking_query(q):
        return False
    # 须含班级语境（班 / 高三(11)班 等）
    if "班" not in q and "班级" not in q:
        return False
    if any(h in q for h in _MULTI_EXAM_HINTS):
        return True
    if "所有" in q and "考试" in q:
        return True
    if "历次" in q and "考试" in q:
        return True
    return False


def is_school_class_comparison_query(question: str) -> bool:
    """学校范围 + 各班/横向对比（应走全校班级对比，禁止缩成单班科目诊断）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if is_multi_exam_class_analysis_query(q):
        return False
    # 「成绩总览 / 班级总览」独立路由，不占用班级横向对比
    if is_class_overview_query(q):
        return False
    # 「群体特征」口径独立路由，不占用班级横向对比
    if is_group_feature_query(q):
        return False
    if not extract_school_target(q):
        return False
    # 否定语境：「无各班 / 不要各班级」≠ 要做各班对比
    if re.search(r"(?:无|不要|别看|别对|非|不是).{0,8}(?:各个班级|各班级|各班)", q):
        return False
    # 已点名具体班级且无「各班/横向」意图 → 单班分析，不走班级对比
    has_named_class = bool(
        re.search(
            r"高[一二三]\(\d+\)班|"
            r"(?:初三|初二|初一|高三|高二|高一|九年级|八年级|七年级)"
            r"[\d班]*\d?班",
            q,
        )
    )
    if has_named_class and not any(
        h in q for h in ("各个", "各班", "横向", "对比", "排名")
    ):
        return False
    if any(h in q for h in _CLASS_COMPARISON_HINTS):
        return True
    return ("各班" in q or "各个班级" in q) and ("对比" in q or "分析" in q)


def is_structured_diagnostic_query(question: str) -> bool:
    """结构化/区县诊断报告（非普通科目诊断）。"""
    q = (question or "").strip()
    return bool(q) and any(h in q for h in _STRUCTURED_DIAGNOSTIC_HINTS)


_TIER_ALERT_HINTS = (
    "临界生预警",
    "分层预警",
    "预警报告",
    "临界生报告",
    "退步生预警",
    "偏科预警",
    "临界生",
    "退步生",
    "分层预警报告",
)


def is_tier_alert_query(question: str) -> bool:
    """临界生/退步/偏科分层预警报告（优先于学校科目诊断）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if any(h in q for h in _TIER_ALERT_HINTS):
        return True
    # 「…预警报告」且含临界/退步/偏科/分层
    if "预警" in q and any(h in q for h in ("临界", "退步", "偏科", "分层", "报告")):
        return True
    return False


#: 班内后十/倒数十 + 中位 + 知识点 → 强制走 compare_knowledge_cohort_tool
_KNOWLEDGE_COHORT_BOTTOM_HINTS = (
    "最后十",
    "最后10",
    "后十名",
    "后10名",
    "倒数十",
    "倒数10",
    "最后十名",
    "后十",
)
_KNOWLEDGE_COHORT_MEDIAN_HINTS = ("中位数", "中位", "中位组", "中位水平")
_KNOWLEDGE_COHORT_KNOWLEDGE_HINTS = ("知识点", "知识掌握", "掌握方面")


def is_knowledge_cohort_gap_query(question: str) -> bool:
    """后十名与中位组在知识点掌握上的差距对比。"""
    q = (question or "").strip()
    if not q:
        return False
    has_bottom = any(h in q for h in _KNOWLEDGE_COHORT_BOTTOM_HINTS)
    has_median = any(h in q for h in _KNOWLEDGE_COHORT_MEDIAN_HINTS)
    has_kn = any(h in q for h in _KNOWLEDGE_COHORT_KNOWLEDGE_HINTS)
    return has_bottom and has_median and has_kn


#: 仅匹配明确「群体特征」口径，避免夺走「各班横向对比」等既有路由。
_GROUP_FEATURE_HINTS = (
    "群体特征",
    "群体对比特征",
    "对比特征",
    "按班级群体",
    "群体特征报告",
    "群体对比分析",
)
#: 出现这些时优先班级横向对比，禁止被群体特征抢走
_GROUP_FEATURE_YIELD_TO_CLASS_COMPARE = (
    "横向对比",
    "横向分析",
    "横向多维",
    "班级横向",
    "各班对比",
    "各班横向",
    "各个班级",
    "各班级",
    "年级对比",
    "班级排名",
    "年级排名",
)


def is_group_feature_query(question: str) -> bool:
    """按维度做群体特征对比（如按班级群体对比特征）→ group_feature。

    「班级横向对比 / 各班对比」优先走 grade_comparison，即使句子里也有「对比」。
    仅当明确出现群体特征口径（或「按X群体」）时才命中。
    """
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if is_tier_alert_query(q):
        return False
    # 「扬州中学…班级横向对比学情分析」不得被群体特征抢走
    has_class_compare = any(h in q for h in _GROUP_FEATURE_YIELD_TO_CLASS_COMPARE)
    has_explicit_group = any(
        h in q
        for h in (
            "群体特征",
            "群体对比特征",
            "按班级群体",
            "群体特征报告",
            "群体对比分析",
        )
    )
    if has_class_compare and not has_explicit_group:
        return False
    if any(h in q for h in _GROUP_FEATURE_HINTS):
        return True
    # 「按X群体」+ 对比/特征
    if re.search(r"按.{0,6}群体", q) and any(h in q for h in ("对比", "特征", "分析")):
        return True
    return False


#: 仅匹配明确「总览」口径，避免夺走科目诊断 / 横向对比等。
_CLASS_OVERVIEW_HINTS = (
    "成绩总览",
    "班级总览",
    "班级成绩总览",
    "总览报告",
    "班级成绩概览",
    "成绩概览",
)
_CLASS_OVERVIEW_BLOCKERS = (
    "详细分析",
    "科目诊断",
    "学科诊断",
    "小题",
    "逐题",
    "知识点",
    "横向",
    "各班",
    "各个班级",
    "预警",
    "临界生",
    "群体特征",
    "对比特征",
    "综合分析",
    "结构化诊断",
    "多维分析",
    "多维对比",
)


def is_class_overview_query(question: str) -> bool:
    """班级成绩总览 / 班级总览 → class_overview（窄化，不抢科目诊断等）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if is_multi_exam_class_analysis_query(q):
        return False
    if is_tier_alert_query(q) or is_group_feature_query(q):
        return False
    if any(b in q for b in _CLASS_OVERVIEW_BLOCKERS):
        return False
    if any(h in q for h in _CLASS_OVERVIEW_HINTS):
        return True
    # 「…总览」且点名班级（高三(10)班 / 初三1班）
    if "总览" in q and re.search(
        r"高[一二三]\(\d+\)班|"
        r"(?:初三|初二|初一|高三|高二|高一|九年级|八年级|七年级)[\d班]*\d?班",
        q,
    ):
        return True
    return False


def infer_group_feature_dimension(question: str) -> str:
    """从问句推断群体特征聚合维度，默认班级。"""
    q = (question or "").strip()
    if any(h in q for h in ("区县", "区域", "城区")):
        return "district"
    if "年级" in q and "班级" not in q:
        return "grade"
    if any(h in q for h in ("科目", "学科", "各科")) and "班级" not in q:
        return "subject"
    if "学校" in q and "班级" not in q and extract_school_target(q) is None:
        return "school"
    return "class"


def is_school_exam_report_query(question: str) -> bool:
    """学校/班级范围 + 考试 + 分析报告类问题（非全市、非个人学生、非多场综合）。"""
    q = (question or "").strip()
    if not q or is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if is_multi_exam_class_analysis_query(q):
        return False
    if is_structured_diagnostic_query(q):
        return False
    if is_tier_alert_query(q):
        return False
    if is_group_feature_query(q):
        return False
    if is_class_overview_query(q):
        return False
    if not extract_school_target(q):
        return False
    if is_school_class_comparison_query(q):
        return True
    if any(h in q for h in _SCHOOL_REPORT_HINTS) or ("分析" in q and "报告" in q):
        return True
    # 「扬州中学全校数学报告」类：有校名 + 报告 + 科目/全校/成绩
    if "报告" in q and any(h in q for h in ("全校", "成绩", "数学", "语文", "英语", "诊断")):
        return True
    # 「扬州中学高三(10)班 · 连淮扬镇数学考试 · 班级整体分析」：校名 + 考试 + 分析
    if "分析" in q and "考试" in q and any(
        h in q for h in ("班", "数学", "语文", "英语", "成绩", "学情", "诊断")
    ):
        return True
    return False


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
            # 「3月扬州中学」勿把月份的「月」拼进校名
            if name.startswith("月") and re.search(rf"\d月{re.escape(name[1:])}", q):
                name = name[1:]
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
    try:
        from src.agent.education.prompt_context import (
            build_education_sql_hint_text,
            is_education_question,
        )

        if is_education_question(question or ""):
            hint = build_education_sql_hint_text(question or "")
            if hint:
                ctx["edu_sql_hint"] = hint
    except Exception:
        pass
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
    from src.agent.education.privacy_mode import privacy_sql_instruction

    raw = constraints if isinstance(constraints, dict) else {}
    parts: list[str] = []
    edu = raw.get("edu_scope")
    if isinstance(edu, dict) and edu.get("edu_role"):
        role = edu.get("edu_role_label") or edu.get("edu_role")
        parts.append(f"当前用户教育角色={role}")
        school_name = str(edu.get("school_name") or "").strip()
        school_id = str(edu.get("school_id") or "").strip()
        if school_name:
            parts.append(
                f"权限绑定学校名={school_name}（工具参数 school_name 用此中文全称，对应 sch.name；"
                "禁止把问题里的「江苏省/南京市」等省市区统考冠名当作学校名）"
            )
        elif school_id:
            parts.append(
                f"权限绑定学校ID={school_id}（工具参数 school_name 可传此 ID，内部按 sc.school_id 过滤，"
                "勿当作 sch.name；禁止把问题里的「江苏省/南京市」等省市区统考冠名当作学校名）"
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
    edu_sql_hint = str(raw.get("edu_sql_hint") or "").strip()
    if edu_sql_hint:
        parts.append(edu_sql_hint)
    if not parts:
        return "（无额外范围约束，按当前子任务描述理解即可）"
    return (
        "报告/SQL 范围必须与用户数据权限及子任务描述一致；"
        "查成绩明细（tb_score / tb_score_detail）时 WHERE 须含学校/班级/学生等过滤，"
        "禁止默认查全量学生、全校或多校合并数据。"
        "探查维表（tb_exam / tb_exam_batch / tb_fraction_bar / tb_knowledge / tb_school 等）无需手写 school_id/class——"
        "系统仅在成绩表上自动注入行级权限。"
        f"{privacy_sql_instruction()}"
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
    含 LIMIT/OFFSET 且行数像预览上限（3/5/10/20）的结果不计入，避免误拦全量报告。
    """
    if not report_data:
        return None
    from src.agent.education.summary_context import sql_looks_row_capped

    sql_counts: list[int] = []
    text_counts: list[int] = []
    preview_like = frozenset({3, 5, 10, 20})
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
                n = int(m.group(1))
                # 文案里的预览规模人数也降权：仅作候选，后面若有更大 sql_counts 仍取 max
                text_counts.append(n)
        er = st.get("exec_result") or {}
        cols = [str(c).lower() for c in (er.get("columns") or [])]
        col_blob = "".join(cols)
        rc = er.get("row_count")
        sql_text = str(st.get("sql") or st.get("last_sql") or "")
        if isinstance(rc, int) and rc > 0:
            if any(
                k in col_blob
                for k in ("student", "学生", "姓名", "name", "学号", "score", "分数")
            ):
                # LIMIT 20 预览行数不能当作全体参考人数
                if sql_looks_row_capped(sql_text) and rc in preview_like:
                    continue
                sql_counts.append(rc)
        cached = st.get("score_rows")
        if isinstance(cached, list) and cached:
            sql_counts.append(len(cached))
    if sql_counts:
        # 预览规模行数不当作拦截权威（真·20 人全量班也不应用来拦更大报告）
        credible_sql = [n for n in sql_counts if n not in preview_like]
        return max(credible_sql) if credible_sql else None
    # 仅文案时：丢掉预览规模，避免「共 20 人」误拦 829 人全量报告 → 落入自主分析
    credible = [n for n in text_counts if n not in preview_like]
    if credible:
        return max(credible)
    return None


_SCORE_COL_HINTS = ("score", "分数", "成绩", "得分")
_SCORE_COL_EXACT = ("score", "分数", "成绩", "得分", "avg_score", "均分")
_SCORE_COL_SKIP = frozenset(
    {
        "exam_score",
        "full_score",
        "prev_score",
        "paper_score",
        "question_score",
        "满分",
        "上次得分",
        "上次成绩",
        "上次分数",
    }
)
_FULL_SCORE_COL_HINTS = ("exam_score", "full_score", "满分")
_DISTRICT_COL_HINTS = ("district", "区县")
_CLASS_COL_HINTS = ("class_name", "班级名称", "班级", "class", "cls")
_SCHOOL_COL_HINTS = ("school_name", "学校")
_SUBJECT_COL_HINTS = ("subject", "subject_name", "科目")
_STUDENT_COL_HINTS = ("student_id", "学号", "学生")
_NAME_COL_HINTS = ("姓名", "student_name")
_PREV_SCORE_COL_HINTS = ("prev_score", "上次得分", "上次成绩", "上次分数")


def _col_index(cols: list[str], hints: tuple[str, ...]) -> int | None:
    lower = [str(c).lower() for c in cols]
    for h in hints:
        hl = h.lower()
        for i, name in enumerate(lower):
            if name == hl:
                return i
    for i, name in enumerate(lower):
        if any(h.lower() in name for h in hints):
            return i
    return None


def _score_col_index(cols: list[str]) -> int | None:
    """优先精确匹配 score/分数，避免命中 exam_score / prev_score。"""
    lower = [str(c).lower() for c in cols]
    for exact in ("score", "分数", "成绩", "得分"):
        if exact in lower:
            return lower.index(exact)
    for i, name in enumerate(lower):
        if name in _SCORE_COL_SKIP:
            continue
        if name in ("avg_score", "均分"):
            return i
        if any(h in name for h in ("score", "分数", "成绩", "得分")):
            if any(skip in name for skip in ("exam", "full", "prev", "上次", "满分", "question")):
                continue
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


def _parse_score_rows_from_exec(er: dict[str, Any]) -> list[dict[str, Any]]:
    cols = list(er.get("columns") or [])
    raw_rows = list(er.get("rows") or [])
    if not cols or not raw_rows:
        return []
    si = _score_col_index(cols)
    if si is None:
        return []
    fs_i = _col_index(cols, _FULL_SCORE_COL_HINTS)
    di = _col_index(cols, _DISTRICT_COL_HINTS)
    ci = _col_index(cols, _CLASS_COL_HINTS)
    sch_i = _col_index(cols, _SCHOOL_COL_HINTS)
    sub_i = _col_index(cols, _SUBJECT_COL_HINTS)
    stu_i = _col_index(cols, _STUDENT_COL_HINTS)
    name_i = _col_index(cols, _NAME_COL_HINTS)
    if name_i is None:
        lower_cols = [str(c).lower() for c in cols]
        if "name" in lower_cols:
            name_i = lower_cols.index("name")
    prev_i = _col_index(cols, _PREV_SCORE_COL_HINTS)
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
            ("subject_name", sub_i),
            ("student_id", stu_i),
            ("name", name_i),
            ("prev_score", prev_i),
        )
        for key, idx in field_map:
            val = drow.get(key)
            if val is None and key == "name":
                val = drow.get("姓名") or drow.get("student_name")
            if val is None and key == "class":
                val = drow.get("班级") or drow.get("班级名称") or drow.get("class_name")
            if val is None and key == "prev_score":
                val = drow.get("上次得分") or drow.get("上次成绩")
            if val is None and idx is not None:
                val = _cell(row, idx)
            if val is not None and val != "":
                out[key] = val
        if not out.get("name") and out.get("student_id") is not None:
            out["name"] = str(out["student_id"])
        if out.get("class") and not out.get("class_name"):
            out["class_name"] = out["class"]
        if out.get("class_name") and not out.get("class"):
            out["class"] = out["class_name"]
        parsed.append(out)
    return parsed


def _group_feature_row_quality(rows: list[dict[str, Any]], dimension: str) -> tuple[int, int, int]:
    """(有维度值人数, 有学生标识人数, 总行数) —— 越大越好。"""
    if not rows:
        return (0, 0, 0)
    dim_keys = {
        "class": ("class", "class_name"),
        "district": ("district",),
        "grade": ("grade", "class", "class_name"),
        "subject": ("subject", "subject_name"),
        "school": ("school_name", "school"),
    }.get(dimension, ("class", "class_name"))
    has_dim = 0
    has_stu = 0
    unique_dim: set[str] = set()
    for r in rows:
        vals = [str(r.get(k) or "").strip() for k in dim_keys]
        vals = [v for v in vals if v and not v.startswith("未知")]
        if vals:
            has_dim += 1
            unique_dim.add(vals[0])
        if r.get("student_id") or r.get("name"):
            has_stu += 1
    # 多组优先（unique 组数加权到 has_dim）
    return (len(unique_dim) * 1000 + has_dim, has_stu, len(rows))


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
            dicts = [dict(x) for x in cached if isinstance(x, dict)]
            if len(dicts) > len(best):
                best = dicts
        er = st.get("exec_result") or st.get("last_exec_result") or {}
        if not isinstance(er, dict):
            continue
        parsed = _parse_score_rows_from_exec(er)
        if len(parsed) > len(best):
            best = parsed
    return best


def resolve_group_feature_score_rows(
    *,
    score_rows: list[dict[str, Any]] | None = None,
    report_data: dict[str, Any] | None = None,
    last_exec_result: dict[str, Any] | None = None,
    dimension: str = "class",
) -> list[dict[str, Any]]:
    """群体特征专用：优先「带分组维度 + 学生明细」的上游结果，避免吃到最终 KPI 单行。"""
    candidates: list[list[dict[str, Any]]] = []
    if score_rows:
        candidates.append([dict(r) for r in score_rows if isinstance(r, dict)])
    if isinstance(last_exec_result, dict):
        parsed = _parse_score_rows_from_exec(last_exec_result)
        if parsed:
            candidates.append(parsed)
    if report_data:
        for st in report_data.get("sub_tasks") or []:
            if st.get("sub_task_agent") == "ToolExpert":
                continue
            cached = st.get("score_rows")
            if isinstance(cached, list) and cached:
                candidates.append([dict(x) for x in cached if isinstance(x, dict)])
            er = st.get("exec_result") or st.get("last_exec_result") or {}
            if isinstance(er, dict):
                parsed = _parse_score_rows_from_exec(er)
                if parsed:
                    candidates.append(parsed)
        # 兼容通用抽取
        upstream = extract_score_rows_from_report_data(report_data)
        if upstream:
            candidates.append(upstream)
    if not candidates:
        return []
    return max(candidates, key=lambda rows: _group_feature_row_quality(rows, dimension))


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
    """HTML 中是否出现与上游参考人数明显矛盾的数字。

    只拦「报告人数明显小于上游」（报告缩水）；
    报告人数大于上游时放行（上游常为 LIMIT/预览 20，全量报告为真）。
    """
    if expected <= 0:
        return False
    blob = html[:12000]
    patterns = (
        r"参考人数\s*(\d+)\s*人",
        r"(?:参考|参与|合计|共)\s*(\d+)\s*人",
        r"(\d+)\s*名?学生",
        r"TOTAL_COUNT[^0-9]*(\d+)",
        r'class="label"[^>]*>\s*参考人数\s*</div>\s*<div[^>]*class="value"[^>]*>\s*(\d+)',
    )
    preview_like = frozenset({3, 5, 10, 20})
    for pat in patterns:
        for m in re.finditer(pat, blob, flags=re.I):
            found = int(m.group(1))
            if found == expected:
                continue
            # 上游是预览规模、报告更大 → 不拦
            if expected in preview_like and found > expected:
                continue
            # 报告比上游更全 → 不拦
            if found > expected:
                continue
            # 报告明显缩水
            if expected - found >= max(5, int(expected * 0.05)):
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
    "is_top_student_lookup_query",
    "is_multi_exam_student_analysis_query",
    "is_vague_exam_name",
    "extract_student_target",
    "format_scope_constraints",
    "is_citywide_analysis_query",
    "extract_class_target",
    "is_line_reach_citywide_scope",
    "is_line_reach_query",
    "is_line_reach_report_query",
    "is_subject_avg_report_query",
    "is_assign_grade_report_query",
    "is_rank_bucket_report_query",
    "is_contribution_report_query",
    "is_combo_reach_report_query",
    "is_elite_roster_report_query",
    "is_bureau_report_query",
    "is_overview_total_query",
    "is_multi_exam_class_analysis_query",
    "is_trend_tracking_query",
    "is_school_class_comparison_query",
    "is_school_exam_report_query",
    "is_structured_diagnostic_query",
    "is_tier_alert_query",
    "is_knowledge_cohort_gap_query",
    "is_group_feature_query",
    "is_class_overview_query",
    "infer_group_feature_dimension",
    "resolve_group_feature_score_rows",
    "normalize_fullwidth_parentheses",
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
