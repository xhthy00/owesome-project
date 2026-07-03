"""教育学情领域工具——暴露给 Agent ReAct 循环的 ``@tool()`` 接口。

三个 P1 工具 + ``select_report_template``：

- ``resolve_score_schema``：从数据源 schema 推断宽表/分表映射；
- ``compute_score_stats``：确定性统计（均分/及格率/分数段等）；
- ``build_chart_option``：生成 ECharts option JSON；
- ``select_report_template``：报告类型 → 模板名 + 所需 data keys。

遵循 ``business.py`` 的"业务失败不抛"原则：参数非法、数据为空等返回
``ToolResult(content=错误说明, data={"error": ...})``，让 LLM 在 observation
里自修正。
"""

from __future__ import annotations

from typing import Any

from src.agent.education.charts import build_chart_option as _build_chart_option
from src.agent.education.config import EducationConfig
from src.agent.education.config_store import get_config as _get_effective_config
from src.agent.education.report_types import Audience, ReportType
from src.agent.education.schema_mapping import (
    ScoreSchemaMapping,
    infer_normalized_mapping,
    infer_wide_mapping,
)
from src.agent.education.stats import (
    compute_rankings as _compute_rankings,
    compute_score_stats as _compute_stats,
    identify_at_risk_students as _identify_at_risk,
)
from src.agent.education.comprehensive import build_comprehensive_data as _build_comprehensive_data
from src.agent.education.student_exam import build_student_exam_data as _build_student_exam_data
from src.agent.education.templates import select_report_template as _select_template
from src.agent.resource.tool.base import ToolResult
from src.agent.resource.tool.function_tool import tool


# ---- 内部辅助 ------------------------------------------------------------

def _mapping_to_dict(m: ScoreSchemaMapping) -> dict[str, Any]:
    return {
        "mode": m.mode,
        "table": m.table,
        "tables": dict(m.tables),
        "fields": dict(m.fields),
        "subject_columns": dict(m.subject_columns),
        "source": m.source,
    }


def _coerce_report_type(value: str) -> ReportType | None:
    try:
        return ReportType(value)
    except ValueError:
        return None


def _coerce_audience(value: str) -> Audience:
    try:
        return Audience(value)
    except ValueError:
        return Audience.DEFAULT


# ---- 工具实现 ------------------------------------------------------------

@tool()
def resolve_score_schema(
    datasource_id: int,
    question: str = "",
    workspace_oid: int | None = None,
) -> ToolResult:
    """推断当前数据源的成绩表字段映射（宽表 / 标准分表）。

    **适用时机**：要查询成绩数据前先调用本工具，拿到 ``subject_columns`` 与
    ``fields`` 后再写 SQL，避免 LLM 凭记忆猜列名。

    Args:
        question: 可选，含班级/科目等关键词，用于在多张候选表里选最相关的一张。

    Returns:
        ``data`` 为 ``ScoreSchemaMapping`` 的字典形式（``mode`` / ``table`` /
        ``subject_columns`` / ``fields`` / ``source``）。识别不到成绩表时返回
        ``data={"error": ...}``，Agent 应回退到通用 ``find_related_tables``。
    """
    from src.agent.resource.tool.business import _load_datasource  # 复用既有加载逻辑

    try:
        from src.datasource.db.db import get_schema_info
        db_type, config, ds_name = _load_datasource(datasource_id, workspace_oid)
        schema = get_schema_info(db_type, config)
    except Exception as e:  # noqa: BLE001 - 业务失败不抛
        return ToolResult(
            content=f"resolve_score_schema 失败：{e}",
            data={"error": str(e)},
        )

    if not schema:
        return ToolResult(
            content=f"数据源 `{ds_name}` 无可见表，无法推断成绩映射。",
            data={"error": "empty schema"},
        )

    # 1) 先尝试标准分表
    norm = infer_normalized_mapping(schema)
    if norm is not None:
        lines = [
            f"已识别为标准分表模式（来源={norm.source}）。",
            f"- student 表：{norm.tables.get('student')}",
            f"- score 表：{norm.tables.get('score')}",
            f"- subject 字段：{norm.fields.get('subject')}",
            f"- score 字段：{norm.fields.get('score')}",
        ]
        return ToolResult(content="\n".join(lines), data=_mapping_to_dict(norm))

    # 2) 退化为宽表：选名字/comment 含"score/成绩"的表，否则取第一张
    keywords = ("score", "成绩", "student_score")
    candidate = None
    for t in schema:
        low = f"{t.get('name') or ''} {t.get('comment') or ''}".lower()
        if any(k in low for k in keywords):
            candidate = t
            break
    if candidate is None:
        candidate = schema[0]

    wide = infer_wide_mapping(str(candidate.get("name") or ""), candidate.get("fields") or [])
    lines = [
        f"已识别为宽表模式（来源={wide.source}，表={wide.table}）。",
        f"- 科目列：{wide.subject_columns or '（未识别，请用 describe_table 确认）'}",
        f"- 学生名列：{wide.fields.get('student_name') or '（未识别）'}",
        f"- 班级字段：{wide.fields.get('class_name') or '（未识别）'}",
        f"- 考试字段：{wide.fields.get('exam_name') or '（未识别）'}",
    ]
    return ToolResult(content="\n".join(lines), data=_mapping_to_dict(wide))


@tool()
def compute_score_stats_tool(
    scores: list[float] | None = None,
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    score_field: str = "",
    pass_threshold: float | None = None,
    excellent_threshold: float | None = None,
    full_score: float | None = None,
) -> ToolResult:
    """对一组分数计算报告级统计（均分/中位数/标准差/及格率/优秀率/分数段）。

    **两种入参**（二选一）：

    - ``scores``：直接传数值列表，例如 ``[85, 92, 78, 60, 55]``；
    - ``rows`` + ``columns`` + ``score_field``：传 execute_sql 的结果，本工具按
      ``score_field`` 列名从 ``columns`` 取下标抽分数。

    阈值默认 60/85，可用 ``pass_threshold`` / ``excellent_threshold`` 覆盖。

    Returns:
        ``data`` 含 ``count`` / ``avg`` / ``median`` / ``stdev`` / ``min`` /
        ``max`` / ``pass_rate`` / ``excellent_rate`` / ``fail_rate`` /
        ``full_score`` / ``segments``（每段 ``{label, count, ratio}``）。
        空数据返回 ``count=0`` 占位结构，不抛。
    """
    cfg = _get_effective_config()
    if pass_threshold is not None:
        cfg.pass_threshold = float(pass_threshold)
    if excellent_threshold is not None:
        cfg.excellent_threshold = float(excellent_threshold)

    values: list[float] = []
    if scores is not None:
        values = [float(s) for s in scores if s is not None]
    elif rows is not None and columns is not None and score_field:
        try:
            idx = columns.index(score_field)
        except ValueError:
            return ToolResult(
                content=f"compute_score_stats_tool 失败：score_field `{score_field}` 不在 columns {columns} 中。",
                data={"error": "score_field not found", "columns": columns},
            )
        for row in rows:
            try:
                v = row[idx]
            except (IndexError, TypeError):
                continue
            if v is None or v == "":
                continue
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
    else:
        return ToolResult(
            content="compute_score_stats_tool 失败：需提供 scores 或 (rows + columns + score_field)。",
            data={"error": "missing input"},
        )

    stats = _compute_stats(values, cfg, full_score)
    content = (
        f"成绩统计完成：共 {stats['count']} 人，均分 {stats['avg']}，"
        f"及格率 {stats['pass_rate']}%，优秀率 {stats['excellent_rate']}%，"
        f"标准差 {stats['stdev']}。"
    )
    return ToolResult(content=content, data=stats)


@tool()
def build_chart_option_tool(
    chart_type: str,
    data: dict[str, Any] | None = None,
    title: str = "",
) -> ToolResult:
    """生成 ECharts option JSON 字符串，供报告模板用 ``setOption`` 渲染。

    支持 ``chart_type``：

    - ``score_distribution``：``data={"segments": [{label,count},...], "pass_rate": 80}``
    - ``subject_radar``：``data={"subjects": [...], "values": [...], "full_score": 100}``
    - ``class_compare_bar``：``data={"classes": [...], "values": [...]}``
    - ``subject_bar``：``data={"subjects": [...], "metrics": [{name, values}]}``
    - ``trend_line``：``data={"x_labels": [...], "series": [{name, values}]}``
    - ``pie``：``data={"items": [{name, value, color?}, ...]}``
    - ``correlation_bar``：``data={"subjects": [...], "series": [{name, values:[r,...]}]}``
    - ``progress_regress_bar``：``data={"items": [{name, value, color?}, ...]}``
    - ``trajectory_line``：``data={"x_labels": [...], "series": [{name, values}]}``

    Returns:
        ``content`` 为可读说明，``data`` 为 ``{"option": "<JSON 字符串>"}``。
        未知 chart_type 返回 ``data={"error": ..., "option": ""}``，Agent 应换类型或放弃图表。
    """
    option = _build_chart_option(chart_type, data or {}, title)
    if not option:
        return ToolResult(
            content=f"build_chart_option_tool 失败：未知 chart_type `{chart_type}`。"
                    f" 可选：score_distribution / subject_radar / class_compare_bar / subject_bar。",
            data={"error": "unknown chart_type", "chart_type": chart_type, "option": ""},
        )
    return ToolResult(
        content=f"已生成 ECharts option（{chart_type}），可直接填入模板 CHART 字段。",
        data={"option": option, "chart_type": chart_type},
    )


@tool()
def select_report_template_tool(
    report_type: str,
    audience: str = "default",
) -> ToolResult:
    """根据报告类型 + 受众返回模板名与所需 data keys。

    Args:
        report_type: ``class_overview`` / ``grade_comparison`` / ``subject_diagnosis`` /
            ``student_profile`` / ``trend_tracking`` / ``tier_alert`` /
            ``group_feature`` / ``comprehensive``（多次考试综合分析）。
        audience: ``principal`` / ``grade_head`` / ``head_teacher`` /
            ``subject_teacher`` / ``parent`` / ``default``。

    Returns:
        ``data`` 含 ``template_name``（相对 templates 目录）与 ``data_keys``
        （模板期望的 ``{{KEY}}`` 清单）。未实现的类型 ``template_name`` 为空串。
    """
    rt = _coerce_report_type(report_type)
    if rt is None:
        return ToolResult(
            content=f"select_report_template_tool 失败：未知 report_type `{report_type}`。",
            data={"error": "unknown report_type", "report_type": report_type},
        )
    aud = _coerce_audience(audience)
    info = _select_template(rt, aud)
    if not info["template_name"]:
        return ToolResult(
            content=f"报告类型 `{report_type}` 模板尚未实现，请回退 score_analysis_report 或 inline html。",
            data={"template_name": "", "data_keys": [], "report_type": report_type},
        )
    return ToolResult(
        content=(
            f"已选定模板 `{info['template_name']}`，需填 data keys："
            f"{', '.join(info['data_keys'])}"
        ),
        data=info,
    )


@tool()
def compute_rankings_tool(
    items: list[dict[str, Any]] | None = None,
    value_key: str = "value",
    name_key: str = "name",
) -> ToolResult:
    """对聚合结果（班级均分 / 学生总分等）计算排名与百分位。

    Args:
        items: ``[{"name": "初三1班", "value": 78.5}, ...]``；
        value_key / name_key: 值与名称字段名。

    Returns:
        ``data`` 为按值降序排列的列表，每项含原字段 + ``rank``（1 起，同值并列）
        + ``percentile``（0–100）。空列表返回 ``[]``。
    """
    if not items:
        return ToolResult(
            content="compute_rankings_tool 失败：items 为空。",
            data={"error": "empty items", "ranking": []},
        )
    ranking = _compute_rankings(items, value_key=value_key, name_key=name_key)
    top = ranking[0]
    return ToolResult(
        content=f"排名完成：共 {len(ranking)} 项，第一为 {top.get(name_key)}（{top.get(value_key)}）。",
        data={"ranking": ranking},
    )


@tool()
def identify_at_risk_students_tool(
    students: list[dict[str, Any]] | None = None,
    pass_threshold: float | None = None,
    critical_margin: float | None = None,
    regression_threshold: float | None = None,
    score_key: str = "score",
    name_key: str = "name",
    subject_key: str = "subject",
    prev_score_key: str = "prev_score",
) -> ToolResult:
    """识别需要关注的学生：临界生 / 大幅退步 / 偏科生。

    Args:
        students: 每条 ``{name, subject, score, prev_score?}``；同一学生可多科多行。
        阈值参数为 None 时用配置默认值（及格 60 / 临界 ±5 / 退步 -10）。

    Returns:
        ``data`` 含 ``critical`` / ``regression`` / ``imbalanced`` 三个列表，
        每条含原始字段 + ``reason``。
    """
    if not students:
        return ToolResult(
            content="identify_at_risk_students_tool 失败：students 为空。",
            data={"error": "empty students", "critical": [], "regression": [], "imbalanced": []},
        )
    cfg = _get_effective_config()
    if pass_threshold is not None:
        cfg.pass_threshold = float(pass_threshold)
    if critical_margin is not None:
        cfg.critical_margin = float(critical_margin)
    if regression_threshold is not None:
        cfg.regression_threshold = float(regression_threshold)

    result = _identify_at_risk(
        students, cfg,
        score_key=score_key, name_key=name_key,
        subject_key=subject_key, prev_score_key=prev_score_key,
    )
    content = (
        f"预警识别完成：临界生 {len(result['critical'])} 人，"
        f"大幅退步 {len(result['regression'])} 人，偏科 {len(result['imbalanced'])} 人。"
    )
    return ToolResult(content=content, data=result)


@tool()
def build_comprehensive_report_data_tool(
    records: list[dict[str, Any]] | None = None,
    exam_order: list[str] | None = None,
    class_name: str = "",
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    exam_field: str = "exam",
    student_field: str = "student",
    subject_field: str = "subject",
    score_field: str = "score",
    total_field: str = "total",
    full_score: float | None = None,
    render: bool = True,
) -> ToolResult:
    """一次性组装「多次考试综合分析报告」并**直接渲染 HTML**（9 个维度）。

    **这是综合报告的关键工具**：内部完成 数据组装 → 模板渲染 → HTML 消毒，
    返回 ``data={"output_type":"html","html":...,"title":...,"mode":"template"}``，
    报告会由上层 ``_maybe_emit_report`` 自动推送到前端。LLM 调完本工具只需
    ``terminate``，**无需再调 ``render_html_report``、也无需回填大 data 字典**
    （回填巨大字典会因 JSON 过长被截断，触发"必须是 JSON 对象"错误）。

    **两种入参**（二选一）：

    - ``records``：已结构化的列表，每条
      ``{exam, student, subjects: {科目: 分数}, total}``；
    - ``rows`` + ``columns`` + 字段名：传 execute_sql 的长表结果（每行一次考试
      一名学生一个科目一条分数），本工具自动按 (exam, student) 聚合 ``subjects``
      与 ``total``。``total_field`` 列若不存在则按学生各科求和。

    Args:
        exam_order: 考试顺序（最早→最近）；为空时按出现顺序去重。
        class_name: 班级名，用于封面标题。
        full_score: 单科满分；用于水平分布阈值。
        render: True（默认）直接渲染 HTML 并上报；False 仅返回 data 字典（调试用）。

    Returns:
        render=True 时 ``data`` 为 HTML 报告载荷（``output_type=html``），可直接
        推送前端；render=False 时 ``data`` 为模板字段字典。
    """
    if not records and not (rows and columns):
        return ToolResult(
            content="build_comprehensive_report_data_tool 失败：需提供 records 或 (rows + columns)。",
            data={"error": "missing input"},
        )

    if not records:
        # 从长表 rows + columns 聚合
        if not columns:
            return ToolResult(
                content="build_comprehensive_report_data_tool 失败：columns 为空。",
                data={"error": "empty columns"},
            )
        idx = {c: i for i, c in enumerate(columns)}
        def _cell(row, key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        exam_seen: list[str] = []
        exam_set: set[str] = set()
        for row in rows or []:
            exam = str(_cell(row, exam_field, "") or "")
            student = str(_cell(row, student_field, "") or "")
            subject = str(_cell(row, subject_field, "") or "")
            if not exam or not student:
                continue
            if exam not in exam_set:
                exam_set.add(exam)
                exam_seen.append(exam)
            key = (exam, student)
            slot = agg.setdefault(key, {"exam": exam, "student": student, "subjects": {}, "total": 0})
            if subject:
                try:
                    slot["subjects"][subject] = float(_cell(row, score_field))
                except (TypeError, ValueError):
                    pass
            total_val = _cell(row, total_field)
            if total_val is not None:
                try:
                    tv = float(total_val)
                    # total 列存在但为 0 时仍应累加各科（常见：长表每行 total 列填 0）
                    if tv > 0:
                        slot["total"] = tv
                except (TypeError, ValueError):
                    pass
            elif subject and subject not in (slot.get("_summed") or set()):
                slot.setdefault("_summed", set()).add(subject)
                try:
                    slot["total"] = slot.get("total", 0) + float(_cell(row, score_field) or 0)
                except (TypeError, ValueError):
                    pass
        for slot in agg.values():
            subs = slot.get("subjects") or {}
            if subs:
                slot["total"] = sum(float(v) for v in subs.values())
            slot.pop("_summed", None)
        records = [v for v in agg.values()]
        exam_order = exam_order or exam_seen

    data = _build_comprehensive_data(records, exam_order or [], class_name=class_name, full_score=full_score)

    if not render:
        return ToolResult(
            content=f"综合报告 data 已组装（{len(records)} 条记录、{len(exam_order or [])} 次考试）。render=False，未渲染。",
            data=data,
        )

    # 直接渲染 HTML 并返回上报载荷——避免 LLM 回填巨大 data 字典。
    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    template_name = "education/comprehensive.html"
    title = data.get("COVER_TITLE") or "综合分析报告"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001 - 业务失败不抛
        return ToolResult(
            content=f"综合报告渲染失败：{e}",
            data={"error": str(e)},
        )
    if not safe_html.strip():
        return ToolResult(
            content="综合报告渲染失败：HTML 为空。",
            data={"error": "empty html"},
        )
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    return ToolResult(
        content=(
            f"综合分析报告已渲染完成（{len(records)} 条记录、{len(exam_order or [])} 次考试、"
            f"HTML {len(safe_html)} 字符）。报告已自动推送到前端，直接调 terminate 结束即可。"
        ),
        data=payload,
    )


@tool()
def build_student_exam_report_data_tool(
    student_name: str,
    records: list[dict[str, Any]] | None = None,
    exam_order: list[str] | None = None,
    class_name: str = "",
    class_size: int | None = None,
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    exam_field: str = "exam",
    student_field: str = "student",
    subject_field: str = "subject",
    score_field: str = "score",
    total_field: str = "total",
    render: bool = True,
) -> ToolResult:
    """组装**单个学生**多次考试深度分析报告并直接渲染 HTML（对齐 Word 样例结构）。

    **这是个体学生多次考试分析的关键工具**：内部完成数据组装 + 模板渲染 +
    HTML 上报。LLM 调完只需 ``terminate``，无需再调 ``render_html_report``。

    ``records`` 应包含**全班**历次考试数据（用于排名/班级均分），工具会按
    ``student_name`` 过滤出目标学生并生成**一份**报告。

    Args:
        student_name: 目标学生（如「学生001」），必填。
        records / rows+columns: 与 ``build_comprehensive_report_data_tool`` 相同。
        exam_order: 考试顺序（最早→最近）。
        class_name: 班级名。
        class_size: 班级人数；缺省时从数据推断。
        render: True（默认）直接渲染 HTML；False 仅返回 data 字典。
    """
    if not student_name or not str(student_name).strip():
        return ToolResult(
            content="build_student_exam_report_data_tool 失败：student_name 为空。",
            data={"error": "missing student_name"},
        )
    if not records and not (rows and columns):
        return ToolResult(
            content="build_student_exam_report_data_tool 失败：需提供 records 或 (rows + columns)。",
            data={"error": "missing input"},
        )

    if not records:
        if not columns:
            return ToolResult(
                content="build_student_exam_report_data_tool 失败：columns 为空。",
                data={"error": "empty columns"},
            )
        idx = {c: i for i, c in enumerate(columns)}

        def _cell(row, key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]

        agg: dict[tuple[str, str], dict[str, Any]] = {}
        exam_seen: list[str] = []
        exam_set: set[str] = set()
        for row in rows or []:
            exam = str(_cell(row, exam_field, "") or "")
            student = str(_cell(row, student_field, "") or "")
            subject = str(_cell(row, subject_field, "") or "")
            if not exam or not student:
                continue
            if exam not in exam_set:
                exam_set.add(exam)
                exam_seen.append(exam)
            key = (exam, student)
            slot = agg.setdefault(key, {"exam": exam, "student": student, "subjects": {}, "total": 0})
            if subject:
                try:
                    slot["subjects"][subject] = float(_cell(row, score_field))
                except (TypeError, ValueError):
                    pass
            total_val = _cell(row, total_field)
            if total_val is not None:
                try:
                    tv = float(total_val)
                    if tv > 0:
                        slot["total"] = tv
                except (TypeError, ValueError):
                    pass
        for slot in agg.values():
            subs = slot.get("subjects") or {}
            if subs:
                slot["total"] = sum(float(v) for v in subs.values())
        records = list(agg.values())
        exam_order = exam_order or exam_seen

    data = _build_student_exam_data(
        records,
        student_name=str(student_name).strip(),
        exam_order=exam_order or [],
        class_name=class_name,
        class_size=class_size,
    )

    if not render:
        return ToolResult(
            content=f"学生考试分析报告 data 已组装（学生={student_name}，{len(exam_order or [])} 次考试）。",
            data=data,
        )

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    template_name = "education/student_exam_analysis.html"
    title = data.get("REPORT_TITLE") or f"{student_name} 考试分析报告"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"学生考试分析报告渲染失败：{e}", data={"error": str(e)})
    if not safe_html.strip():
        return ToolResult(content="学生考试分析报告渲染失败：HTML 为空。", data={"error": "empty html"})
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    return ToolResult(
        content=(
            f"{student_name} 考试分析报告已渲染完成（HTML {len(safe_html)} 字符）。"
            "报告已自动推送到前端，直接调 terminate 结束即可。"
        ),
        data=payload,
    )


# 暴露给 ``build_default_toolpack`` 的列表（与 business.py 工具并列）。
EDUCATION_TOOLS = [
    resolve_score_schema,
    compute_score_stats_tool,
    compute_rankings_tool,
    identify_at_risk_students_tool,
    build_chart_option_tool,
    select_report_template_tool,
    build_comprehensive_report_data_tool,
    build_student_exam_report_data_tool,
]


__all__ = [
    "EDUCATION_TOOLS",
    "build_chart_option_tool",
    "build_comprehensive_report_data_tool",
    "build_student_exam_report_data_tool",
    "compute_rankings_tool",
    "compute_score_stats_tool",
    "identify_at_risk_students_tool",
    "resolve_score_schema",
    "select_report_template_tool",
]
