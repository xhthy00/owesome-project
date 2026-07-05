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
    load_schema_from_config,
    validate_mapping_against_schema,
)
from src.agent.education.stats import (
    compute_rankings as _compute_rankings,
    compute_score_stats as _compute_stats,
    identify_at_risk_students as _identify_at_risk,
)
from src.agent.education.comprehensive import build_comprehensive_data as _build_comprehensive_data
from src.agent.education.student_exam import build_student_exam_data as _build_student_exam_data
from src.agent.education.subject_diagnosis import (
    build_diagnosis_recommendations,
    build_diagnosis_summary,
    build_item_table_html,
    build_knowledge_table_html,
    enrich_knowledge_rows,
)
from src.agent.education.templates import select_report_template as _select_template
from src.agent.resource.tool.base import ToolResult
from src.agent.resource.tool.function_tool import tool


# ---- 内部辅助 ------------------------------------------------------------

def _mapping_to_dict(m: ScoreSchemaMapping) -> dict[str, Any]:
    return {
        "mode": m.mode,
        "table": m.table,
        "tables": dict(m.tables),
        "joins": list(m.joins),
        "fields": dict(m.fields),
        "subject_columns": dict(m.subject_columns),
        "source": m.source,
    }


def _format_config_schema_result(bundle, warnings: list[str]) -> ToolResult:
    m = bundle.mapping
    meta = bundle.meta
    lines = [
        f"已加载教育 Schema 配置（来源={m.source}）。",
        f"- score 表：{m.tables.get('score', '')}",
        f"- school 表：{m.tables.get('school', '')}",
        f"- 满分字段：{m.fields.get('full_score', '（未配置）')}（运行时从库读取，非固定值）",
        f"- 及格/优秀比例：{meta.pass_ratio} / {meta.excellent_ratio}",
    ]
    if m.joins:
        lines.append("- 标准 JOIN（节选）：")
        for j in m.joins[:3]:
            lines.append(f"  · {j}")
        if len(m.joins) > 3:
            lines.append(f"  · …共 {len(m.joins)} 条")
    if warnings:
        lines.append("- 校验警告（不阻断）：")
        for w in warnings:
            lines.append(f"  · {w}")
    data = _mapping_to_dict(m)
    data["pass_ratio"] = meta.pass_ratio
    data["excellent_ratio"] = meta.excellent_ratio
    data["table_comments"] = dict(meta.table_comments)
    data["dimension_samples"] = dict(meta.dimension_samples)
    if warnings:
        data["warnings"] = warnings
    return ToolResult(content="\n".join(lines), data=data)


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

    # 1) 优先读取固定 Schema 配置
    bundle = load_schema_from_config()
    if bundle is not None:
        warnings = validate_mapping_against_schema(bundle.mapping, schema)
        return _format_config_schema_result(bundle, warnings)

    # 2) 启发式标准分表
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

    # 3) 退化为宽表：选名字/comment 含"score/成绩"的表，否则取第一张
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


def _extract_full_score_from_rows(
    rows: list[list[Any]] | list[Any],
    columns: list[str],
    field: str,
) -> tuple[float | None, str]:
    """从查询结果中提取卷面满分；返回 (full_score, warning_msg)。"""
    if not field or field not in columns:
        return None, f"columns 中无 `{field}`，统计将使用 default_full_score 兜底"
    idx = columns.index(field)
    seen: set[float] = set()
    for row in rows:
        try:
            v = row[idx] if isinstance(row, (list, tuple)) else row.get(field)
        except (IndexError, TypeError, AttributeError):
            continue
        if v is None or v == "":
            continue
        try:
            seen.add(float(v))
        except (TypeError, ValueError):
            continue
    if not seen:
        return None, f"`{field}` 列无有效满分值，将使用 default_full_score 兜底"
    if len(seen) > 1:
        chosen = max(seen)
        return chosen, f"警告：`{field}` 存在多个值 {sorted(seen)}，取 {chosen} 作为满分"
    return seen.pop(), ""


_SCORE_FIELD_HINTS = (
    "score",
    "avg_score",
    "student_score",
    "分数",
    "得分",
    "成绩",
)
_FULL_SCORE_FIELD_HINTS = ("exam_score", "full_score", "paper_score", "满分")


def _coerce_exec_result(
    exec_result: dict[str, Any] | None,
    rows: list[Any] | None,
    columns: list[str] | None,
) -> tuple[list[Any] | None, list[str] | None]:
    """从 exec_result 或 dict 行补全 rows/columns。"""
    if exec_result and isinstance(exec_result, dict):
        if rows is None:
            rows = exec_result.get("rows")
        if columns is None:
            columns = exec_result.get("columns")
    if rows and not columns and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    return rows, columns


def _guess_field(columns: list[str], hints: tuple[str, ...]) -> str:
    if not columns:
        return ""
    lower_map = {str(c).lower(): str(c) for c in columns}
    for hint in hints:
        h = hint.lower()
        if h in lower_map:
            return lower_map[h]
    for col in columns:
        cl = str(col).lower()
        for hint in hints:
            if hint.lower() in cl:
                return str(col)
    return ""


@tool()
def compute_score_stats_tool(
    scores: list[float] | None = None,
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    score_field: str = "",
    full_score_field: str = "exam_score",
    pass_threshold: float | None = None,
    excellent_threshold: float | None = None,
    full_score: float | None = None,
    exec_result: dict[str, Any] | None = None,
) -> ToolResult:
    """对一组分数计算报告级统计（均分/中位数/标准差/及格率/优秀率/分数段）。

    **三种入参**（任选其一）：

    - ``scores``：直接传数值列表，例如 ``[85, 92, 78, 60, 55]``；
    - ``exec_result``：传 ``execute_sql`` 返回的 ``data`` 整包（含 columns/rows）；
    - ``rows`` + ``columns`` + ``score_field``：分列传入；``score_field`` 可省略，
      工具会按列名自动猜测（优先 ``score`` / ``avg_score``）。

    **推荐写法**（接 execute_sql 之后）::

        compute_score_stats_tool(
            exec_result={"columns": ["score", "exam_score"], "rows": [[84, 150], ...]},
            score_field="score",
            full_score_field="exam_score",
        )

    **满分与阈值**：优先使用 ``full_score`` 参数；否则从 ``rows`` 的
    ``full_score_field``（默认 ``exam_score``，对应 tb_exam/tb_score 卷面满分）
    读取。有 ``full_score`` 时及格/优秀线 = 满分 × pass_ratio(0.6) / excellent_ratio(0.85)，
    禁止写死 60/85/150。无满分列时回退 ``default_full_score``(100) 并在 observation 中 warning。

    Returns:
        ``data`` 含 ``count`` / ``avg`` / ``median`` / ``stdev`` / ``min`` /
        ``max`` / ``pass_rate`` / ``excellent_rate`` / ``fail_rate`` /
        ``full_score`` / ``segments``（每段 ``{label, count, ratio}``）。
        空数据返回 ``count=0`` 占位结构，不抛。
    """
    cfg = _get_effective_config()
    bundle = load_schema_from_config()
    if bundle is not None:
        cfg.pass_ratio = bundle.meta.pass_ratio
        cfg.excellent_ratio = bundle.meta.excellent_ratio
        if bundle.meta.score_segment_ratios:
            cfg.score_segment_ratios = list(bundle.meta.score_segment_ratios)
    if pass_threshold is not None:
        cfg.pass_threshold = float(pass_threshold)
    if excellent_threshold is not None:
        cfg.excellent_threshold = float(excellent_threshold)

    rows, columns = _coerce_exec_result(exec_result, rows, columns)
    if not score_field and columns:
        score_field = _guess_field(columns, _SCORE_FIELD_HINTS)
    if full_score_field == "exam_score" and columns:
        guessed_full = _guess_field(columns, _FULL_SCORE_FIELD_HINTS)
        if guessed_full:
            full_score_field = guessed_full

    warnings: list[str] = []
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
                v = row[idx] if isinstance(row, (list, tuple)) else row.get(score_field)
            except (IndexError, TypeError, AttributeError):
                continue
            if v is None or v == "":
                continue
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
        if full_score is None:
            resolved, warn = _extract_full_score_from_rows(rows, columns, full_score_field)
            if resolved is not None:
                full_score = resolved
            if warn:
                warnings.append(warn)
    else:
        hint = (
            "compute_score_stats_tool 失败：需提供 scores、exec_result，"
            "或 (rows + columns [+ score_field])。"
        )
        if rows is not None and not columns:
            hint += " 提示：可传 exec_result=execute_sql 的 data，或 rows 为 dict 列表时省略 columns。"
        elif rows is not None and columns and not score_field:
            hint += f" 未能从 columns {columns} 猜测 score_field，请显式传入 score_field。"
        return ToolResult(
            content=hint,
            data={"error": "missing input", "columns": columns or []},
        )

    if full_score is None and values:
        warnings.append(
            f"未提供满分（请 SQL SELECT 带出 `{full_score_field}`），"
            f"使用 default_full_score={cfg.default_full_score} 计算 KPI"
        )

    stats = _compute_stats(values, cfg, full_score)
    if warnings:
        stats["warnings"] = warnings
    content = (
        f"成绩统计完成：共 {stats['count']} 人，均分 {stats['avg']}，"
        f"满分 {stats['full_score']}，"
        f"及格率 {stats['pass_rate']}%，优秀率 {stats['excellent_rate']}%，"
        f"标准差 {stats['stdev']}。"
    )
    if warnings:
        content += "\n" + "\n".join(warnings)
    return ToolResult(content=content, data=stats)


@tool()
def fetch_subject_diagnosis_data_tool(
    datasource_id: int,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    class_name: str = "",
    workspace_oid: int | None = None,
) -> ToolResult:
    """直接从数据库查询科目诊断所需的小题明细与知识点汇总（确定性 SQL，无需 LLM 写 JOIN）。

    **适用时机**：科目诊断报告中需要「每一小题 + 知识点」和「知识点得分率」时，
    **优先调用本工具**，而不是自己写 SQL——本工具内部已固定 JOIN ``tb_knowledge``
    （通过 ``tb_exam_question.knowledge_id`` 关联），确保知识点名称来自数据库而非臆造。

    返回的 ``data`` 含：
    - ``item_rows``：每题一行，含 question_no / knowledge_name / full_score / avg_score / score_rate
    - ``knowledge_rows``：每个知识点一行，含 knowledge_name / question_count / score_rate
    - ``score_rows``：每个学生一行的原始成绩（含 exam_score），可直接传给 compute_score_stats_tool

    Args:
        datasource_id: 数据源 ID（必填）。
        school_name: 学校名（如「南京市第一中学」），用于 WHERE 过滤。
        subject_name: 科目名（如「数学」）。
        exam_name: 考试名关键字（如「期末质量检测」），可选。
        class_name: 班级名，可选。
    """
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.db.db import execute_sql as db_execute_sql

    try:
        db_type, config, ds_name = _load_datasource(datasource_id, workspace_oid)
    except Exception as e:
        return ToolResult(
            content=f"fetch_subject_diagnosis_data_tool 失败：{e}",
            data={"error": str(e)},
        )

    where_parts: list[str] = []
    if school_name:
        where_parts.append(f"sch.name = '{school_name.replace(chr(39), chr(39)+chr(39))}'")
    if class_name:
        where_parts.append(f"sc.class = '{class_name.replace(chr(39), chr(39)+chr(39))}'")
    if subject_name:
        where_parts.append(f"sc.subject_name = '{subject_name.replace(chr(39), chr(39)+chr(39))}'")
    if exam_name:
        where_parts.append(f"e.exam_name LIKE '%{exam_name.replace(chr(39), chr(39)+chr(39))}%'")
    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # 小题明细 SQL：通过 tb_exam_question.knowledge_id JOIN tb_knowledge 获取知识点名
    item_sql = (
        "SELECT sd.question_no,\n"
        "       k.knowledge_name,\n"
        "       eq.question_score AS full_score,\n"
        "       ROUND(AVG(sd.score), 2) AS avg_score,\n"
        "       ROUND(AVG(sd.score)::numeric / NULLIF(eq.question_score, 0) * 100, 2) AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "JOIN tb_knowledge k ON eq.knowledge_id = k.id\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause
        + "\nGROUP BY sd.question_no, k.knowledge_name, eq.question_score\n"
        "ORDER BY sd.question_no\nLIMIT 1000"
    )

    # 知识点汇总 SQL
    knowledge_sql = (
        "SELECT k.knowledge_name,\n"
        "       COUNT(DISTINCT sd.question_no) AS question_count,\n"
        "       ROUND(SUM(sd.score)::numeric / NULLIF(SUM(eq.question_score), 0) * 100, 2) AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "JOIN tb_knowledge k ON eq.knowledge_id = k.id\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause
        + "\nGROUP BY k.knowledge_name\nORDER BY score_rate ASC\nLIMIT 1000"
    )

    # 整体成绩 SQL（供 compute_score_stats_tool 用）
    score_sql = (
        "SELECT sc.score, sc.exam_score\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause
        + "\nLIMIT 1000"
    )

    item_rows: list[dict[str, Any]] = []
    knowledge_rows: list[dict[str, Any]] = []
    score_result: dict[str, Any] = {"columns": [], "rows": []}

    for label, sql in (("item", item_sql), ("knowledge", knowledge_sql), ("score", score_sql)):
        success, msg, result = db_execute_sql(db_type=db_type, config=config, sql=sql)
        if not success or not isinstance(result, dict):
            continue
        cols = result.get("columns") or []
        raw_rows = result.get("rows") or []
        dict_rows = [dict(zip(cols, row)) for row in raw_rows]
        if label == "item":
            item_rows = dict_rows
        elif label == "knowledge":
            knowledge_rows = dict_rows
        else:
            score_result = {"columns": cols, "rows": raw_rows}

    if not item_rows and not knowledge_rows:
        return ToolResult(
            content=(
                f"fetch_subject_diagnosis_data_tool 未查到数据（ds={ds_name}，"
                f"school={school_name}，subject={subject_name}）。"
                "请检查学校名/科目名是否正确，或先调 describe_table 确认表数据。"
            ),
            data={"error": "no data", "item_rows": [], "knowledge_rows": []},
        )

    content = (
        f"科目诊断数据已查询（ds={ds_name}）：\n"
        f"- 小题明细：{len(item_rows)} 题\n"
        f"- 知识点汇总：{len(knowledge_rows)} 个知识点\n"
        f"- 学生成绩：{len(score_result.get('rows', []))} 条\n"
        "下一步：将 item_rows 与 knowledge_rows 传给 build_subject_diagnosis_sections_tool，"
        "将 score_result 传给 compute_score_stats_tool(exec_result=...)。"
    )
    return ToolResult(
        content=content,
        data={
            "item_rows": item_rows,
            "knowledge_rows": knowledge_rows,
            "score_result": score_result,
        },
    )


@tool()
def build_subject_diagnosis_sections_tool(
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    school_name: str = "",
    exam_name: str = "",
    subject_name: str = "",
    weak_threshold: float = 60.0,
) -> ToolResult:
    """组装科目诊断报告中的 ITEM_TABLE / KNOWLEDGE_TABLE / SUMMARY / RECOMMENDATIONS。

    **适用时机**：ToolExpert 组装 ``education/subject_diagnosis.html`` 时，上游
    DataAnalyst 已分别查出小题明细与知识点汇总，调用本工具**确定性**生成表格与
    薄弱知识点分析文案，避免 LLM 漏写知识点建议。

    Args:
        item_rows: 小题行，含 question_no / knowledge_name / score_rate 等。
        knowledge_rows: 知识点行，含 knowledge_name / score_rate / question_count。
        stats: 可选整体 KPI（count/avg/pass_rate/excellent_rate）。
        school_name / exam_name / subject_name: 用于 SUMMARY 范围描述。
        weak_threshold: 得分率低于该值视为薄弱知识点（默认 60）。
    """
    items = list(item_rows or [])
    knowledge = enrich_knowledge_rows(list(knowledge_rows or []))
    data = {
        "ITEM_TABLE": build_item_table_html(items),
        "KNOWLEDGE_TABLE": build_knowledge_table_html(knowledge),
        "WEAK_KNOWLEDGE_LIST": "、".join(
            str(r.get("knowledge_name") or "")
            for r in knowledge
            if r.get("level") == "需加强"
        )[:500],
        "SUMMARY": build_diagnosis_summary(
            school_name=school_name,
            exam_name=exam_name,
            subject_name=subject_name,
            stats=stats,
            item_rows=items,
            knowledge_rows=knowledge,
            weak_threshold=weak_threshold,
        ),
        "RECOMMENDATIONS": build_diagnosis_recommendations(
            knowledge_rows=knowledge,
            item_rows=items,
            weak_threshold=weak_threshold,
        ),
    }
    if knowledge:
        data["KNOWLEDGE_CHART"] = _build_chart_option(
            "subject_bar",
            {
                "categories": [str(r.get("knowledge_name") or "") for r in knowledge[:12]],
                "values": [float(r.get("score_rate") or 0) for r in knowledge[:12]],
            },
            title="知识点得分率",
        )
    else:
        data["KNOWLEDGE_CHART"] = ""
    weak_cnt = sum(1 for r in knowledge if r.get("level") == "需加强")
    content = (
        f"科目诊断区块已组装：小题 {len(items)} 条，知识点 {len(knowledge)} 个"
        f"（薄弱 {weak_cnt} 个）。请将返回 data 填入 render_html_report。"
    )
    return ToolResult(content=content, data=data)


@tool()
def build_subject_diagnosis_report_tool(
    datasource_id: int,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    class_name: str = "",
    audience: str = "default",
    workspace_oid: int | None = None,
    render: bool = True,
) -> ToolResult:
    """一键生成「科目逐题/知识点诊断报告」HTML 并**直接推送前端**。

    **这是科目诊断报告的关键工具**：内部一次性完成 查数（小题+知识点+成绩）
    → 统计（均分/及格率/分数段）→ 组装（ITEM_TABLE/KNOWLEDGE_TABLE/SUMMARY/
    RECOMMENDATIONS）→ 模板渲染 → HTML 消毒 → 上报。LLM 调完只需 ``terminate``，
    **无需再调 ``fetch_subject_diagnosis_data_tool`` / ``build_subject_diagnosis_sections_tool``
    / ``render_html_report``**，也**无需回填大 data 字典**（回填巨大字典会因 JSON
    过长被截断，触发"必须是 JSON 对象"错误）。

    知识点名称由 ``tb_knowledge.knowledge_name`` 通过 ``tb_exam_question.knowledge_id``
    LEFT JOIN 确定，**不会臆造**。

    Args:
        datasource_id: 数据源 ID（由 ToolPack bindings 自动注入，LLM 无需填写）。
        school_name: 学校名（如「南京市第一中学」），必填。
        subject_name: 科目名（如「数学」），必填。
        exam_name: 考试名关键字（如「期末质量检测」），可选，用于过滤。
        class_name: 班级名，可选。
        audience: ``default`` / ``principal`` / ``subject_teacher`` / ``parent``。
        render: True（默认）直接渲染 HTML 并上报；False 仅返回 data 字典（调试用）。
    """
    from src.agent.education.subject_diagnosis import (
        build_diagnosis_recommendations,
        build_diagnosis_summary,
        build_item_table_html,
        build_knowledge_table_html,
        enrich_knowledge_rows,
    )
    from src.agent.resource.tool.business import (
        _load_datasource,
        _render_template_html,
        _sanitize_report_html,
    )
    from src.datasource.db.db import execute_sql as db_execute_sql

    # ---------- 1. 查数 ----------
    try:
        db_type, config, ds_name = _load_datasource(datasource_id, workspace_oid)
    except Exception as e:
        return ToolResult(
            content=f"build_subject_diagnosis_report_tool 失败：{e}",
            data={"error": str(e)},
        )

    where_parts: list[str] = []
    if school_name:
        where_parts.append(f"sch.name = '{_esc(school_name)}'")
    if class_name:
        where_parts.append(f"sc.class = '{_esc(class_name)}'")
    if subject_name:
        where_parts.append(f"sc.subject_name = '{_esc(subject_name)}'")
    if exam_name:
        where_parts.append(f"e.exam_name LIKE '%{_esc(exam_name)}%'")
    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    item_sql = (
        "SELECT sd.question_no,\n"
        "       COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        "       eq.question_score AS full_score,\n"
        "       ROUND(AVG(sd.score), 2) AS avg_score,\n"
        "       ROUND(AVG(sd.score)::numeric / NULLIF(eq.question_score, 0) * 100, 2) AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "LEFT JOIN tb_knowledge k ON eq.knowledge_id = k.id\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause
        + "\nGROUP BY sd.question_no, k.knowledge_name, eq.question_score\n"
        "ORDER BY sd.question_no\nLIMIT 1000"
    )
    knowledge_sql = (
        "SELECT COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        "       COUNT(DISTINCT sd.question_no) AS question_count,\n"
        "       ROUND(SUM(sd.score)::numeric / NULLIF(SUM(eq.question_score), 0) * 100, 2) AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "LEFT JOIN tb_knowledge k ON eq.knowledge_id = k.id\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause
        + "\nGROUP BY k.knowledge_name\nORDER BY score_rate ASC\nLIMIT 1000"
    )
    score_sql = (
        "SELECT sc.score, sc.exam_score\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause
        + "\nLIMIT 1000"
    )

    item_rows: list[dict[str, Any]] = []
    knowledge_rows: list[dict[str, Any]] = []
    score_values: list[float] = []
    full_score: float | None = None

    for label, sql in (("item", item_sql), ("knowledge", knowledge_sql), ("score", score_sql)):
        success, _msg, result = db_execute_sql(db_type=db_type, config=config, sql=sql)
        if not success or not isinstance(result, dict):
            continue
        cols = result.get("columns") or []
        raw_rows = result.get("rows") or []
        if label in ("item", "knowledge"):
            dict_rows = [dict(zip(cols, row)) for row in raw_rows]
            if label == "item":
                item_rows = dict_rows
            else:
                knowledge_rows = dict_rows
        else:
            score_idx = cols.index("score") if "score" in cols else 0
            fs_idx = cols.index("exam_score") if "exam_score" in cols else -1
            for row in raw_rows:
                try:
                    score_values.append(float(row[score_idx]))
                except (TypeError, ValueError, IndexError):
                    continue
                if fs_idx >= 0 and full_score is None:
                    try:
                        full_score = float(row[fs_idx])
                    except (TypeError, ValueError, IndexError):
                        pass

    if not item_rows and not knowledge_rows and not score_values:
        return ToolResult(
            content=(
                f"build_subject_diagnosis_report_tool 未查到数据（ds={ds_name}，"
                f"school={school_name}，subject={subject_name}，exam={exam_name}）。"
                "请检查学校名/科目名/考试名是否正确。"
            ),
            data={"error": "no data"},
        )

    # ---------- 2. 统计 ----------
    cfg = _get_effective_config()
    stats = _compute_stats(score_values, cfg, full_score)

    # ---------- 3. 组装区块 ----------
    knowledge_enriched = enrich_knowledge_rows(knowledge_rows)
    weak_threshold = 60.0
    section_data: dict[str, Any] = {
        "ITEM_TABLE": build_item_table_html(item_rows),
        "KNOWLEDGE_TABLE": build_knowledge_table_html(knowledge_enriched),
        "WEAK_KNOWLEDGE_LIST": "、".join(
            str(r.get("knowledge_name") or "")
            for r in knowledge_enriched
            if r.get("level") == "需加强"
        )[:500],
        "SUMMARY": build_diagnosis_summary(
            school_name=school_name,
            exam_name=exam_name,
            subject_name=subject_name,
            stats=stats,
            item_rows=item_rows,
            knowledge_rows=knowledge_enriched,
            weak_threshold=weak_threshold,
        ),
        "RECOMMENDATIONS": build_diagnosis_recommendations(
            knowledge_rows=knowledge_enriched,
            item_rows=item_rows,
            weak_threshold=weak_threshold,
        ),
    }
    if knowledge_enriched:
        section_data["KNOWLEDGE_CHART"] = _build_chart_option(
            "subject_bar",
            {
                "categories": [str(r.get("knowledge_name") or "") for r in knowledge_enriched[:12]],
                "values": [float(r.get("score_rate") or 0) for r in knowledge_enriched[:12]],
            },
            title="知识点得分率",
        )
    else:
        section_data["KNOWLEDGE_CHART"] = ""

    segments = stats.get("segments") or []
    section_data["SCORE_DIST_CHART"] = _build_chart_option(
        "score_distribution",
        {
            "segments": [{"label": s.get("label", ""), "count": s.get("count", 0)} for s in segments],
            "pass_rate": stats.get("pass_rate") or 0,
        },
        title="分数段分布",
    )
    section_data["SEGMENT_TABLE"] = _segment_table_html(segments)

    # ---------- 4. 填充模板字段 ----------
    scope_label = class_name or school_name or "全年级"
    report_data: dict[str, Any] = {
        "REPORT_TITLE": f"{subject_name or '科目'}诊断报告",
        "REPORT_SUBTITLE": f"{school_name} {class_name}".strip(),
        "REPORT_TIME": _now_str(),
        "SUBJECT_NAME": subject_name or "全科",
        "EXAM_NAME": exam_name or "本次考试",
        "SCOPE": scope_label,
        "AVG_SCORE": _fmt_val(stats.get("avg")),
        "PASS_RATE": _fmt_val(stats.get("pass_rate")),
        "EXCELLENT_RATE": _fmt_val(stats.get("excellent_rate")),
        "STDEV": _fmt_val(stats.get("stdev")),
    }
    report_data.update(section_data)

    if not render:
        return ToolResult(
            content=(
                f"科目诊断 data 已组装（{len(item_rows)} 题、{len(knowledge_enriched)} 知识点、"
                f"{len(score_values)} 条成绩）。render=False，未渲染。"
            ),
            data=report_data,
        )

    # ---------- 5. 渲染 HTML ----------
    template_name = "education/subject_diagnosis.html"
    title = report_data.get("REPORT_TITLE") or "科目诊断报告"
    try:
        raw_html = _render_template_html(template_name, report_data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:
        return ToolResult(
            content=f"科目诊断报告渲染失败：{e}",
            data={"error": str(e)},
        )
    if not safe_html.strip():
        return ToolResult(
            content="科目诊断报告渲染失败：HTML 为空。",
            data={"error": "empty html"},
        )
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    weak_cnt = sum(1 for r in knowledge_enriched if r.get("level") == "需加强")
    return ToolResult(
        content=(
            f"科目诊断报告已渲染完成（{len(item_rows)} 题、{len(knowledge_enriched)} 知识点、"
            f"薄弱 {weak_cnt} 个、HTML {len(safe_html)} 字符）。报告已自动推送到前端，"
            "直接调 terminate 结束即可。"
        ),
        data=payload,
    )


def _esc(value: str) -> str:
    return value.replace("'", "''")


def _fmt_val(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _segment_table_html(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return "<p class='edu-sub'>暂无分数段数据</p>"
    rows = "".join(
        f"<tr><td>{s.get('label', '')}</td><td>{s.get('count', 0)}</td>"
        f"<td>{_fmt_val(s.get('ratio'))}%</td></tr>"
        for s in segments
    )
    return (
        "<table class='edu-table'><thead><tr><th>分数段</th><th>人数</th><th>占比</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


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
    fetch_subject_diagnosis_data_tool,
    build_subject_diagnosis_sections_tool,
    build_subject_diagnosis_report_tool,
]


__all__ = [
    "EDUCATION_TOOLS",
    "build_chart_option_tool",
    "build_comprehensive_report_data_tool",
    "build_student_exam_report_data_tool",
    "build_subject_diagnosis_report_tool",
    "build_subject_diagnosis_sections_tool",
    "compute_rankings_tool",
    "compute_score_stats_tool",
    "fetch_subject_diagnosis_data_tool",
    "identify_at_risk_students_tool",
    "resolve_score_schema",
    "select_report_template_tool",
]
