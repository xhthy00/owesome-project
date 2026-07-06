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

import re
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


def _run_edu_sql(
    sql: str,
    *,
    datasource_id: int,
    workspace_oid: int | None,
    user_id: int | None,
) -> tuple[bool, str, dict[str, Any] | None, str]:
    """教育工具查数：走行列权限 + edu 模板谓词。返回 (success, msg, result, sql_run)。"""
    from src.datasource.service.execute_with_permission import execute_sql_with_permission_by_user_id

    success, msg, result, sql_run = execute_sql_with_permission_by_user_id(
        user_id, datasource_id, workspace_oid, sql
    )
    return success, msg, result, sql_run or sql


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
    lines.append(
        "- 权限/过滤列：school_id、class、student_id 均在 tb_score（别名 sc）；"
        "tb_student 主键为 id（学号），JOIN 须写 sc.student_id = st.id，禁止 st.student_id；"
        "查 tb_score_detail 须 JOIN tb_score sc。"
    )
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
    """从 exec_result 或 dict 行补全 rows/columns。

    支持三种 ``exec_result`` 形态：
    - execute_sql 的直接结果：``{"columns": [...], "rows": [...]}``
    - fetch_subject_diagnosis_data_tool 的 data：``{"score_result": {"columns": ..., "rows": ...}, ...}``
    - dict 行列表：``[{...}, {...}]``
    """
    if exec_result and isinstance(exec_result, dict):
        if rows is None:
            rows = exec_result.get("rows")
        if columns is None:
            columns = exec_result.get("columns")
        # fetch_subject_diagnosis_data_tool 返回的 data 顶层无 rows/columns，
        # 但有嵌套 score_result——自动提取。
        if rows is None and "score_result" in exec_result:
            nested = exec_result.get("score_result")
            if isinstance(nested, dict):
                rows = nested.get("rows")
                columns = nested.get("columns") if columns is None else columns
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
    user_id: int | None = None,
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

    try:
        db_type, _config, ds_name = _load_datasource(datasource_id, workspace_oid)
    except Exception as e:
        return ToolResult(
            content=f"fetch_subject_diagnosis_data_tool 失败：{e}",
            data={"error": str(e)},
        )

    bundle = _fetch_subject_diagnosis_rows(
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
        user_id=user_id,
        school_name=school_name,
        subject_name=subject_name,
        exam_name=exam_name,
        class_name=class_name,
        db_type=db_type,
    )
    item_rows = bundle["item_rows"]
    knowledge_rows = bundle["knowledge_rows"]
    score_values = bundle["score_values"]
    warnings = bundle.get("warnings") or []
    errors = bundle.get("errors") or []
    sql_logs = bundle.get("sql_logs") or []
    score_result = {
        "columns": ["score", "exam_score"],
        "rows": [[v, bundle.get("full_score")] for v in score_values],
    }

    if not item_rows and not knowledge_rows and not score_values:
        err_detail = "\n".join(errors[:5]) if errors else "无额外错误信息"
        return ToolResult(
            content=(
                f"fetch_subject_diagnosis_data_tool 未查到数据（ds={ds_name}，"
                f"school={school_name}，subject={subject_name}，exam={exam_name}，class={class_name}）。\n"
                f"SQL 执行记录：\n{_format_diagnosis_sql_logs(sql_logs)}\n"
                f"错误：{err_detail}"
            ),
            data={"error": "no data", "item_rows": [], "knowledge_rows": [], "sql_logs": sql_logs},
        )

    content = (
        f"【小题明细查询】fetch_subject_diagnosis_data_tool（ds={ds_name}，db={db_type}）\n"
        f"- 小题明细（tb_score_detail）：{len(item_rows)} 题\n"
        f"- 知识点汇总：{len(knowledge_rows)} 个\n"
        f"- 学生成绩（tb_score）：{len(score_values)} 条\n"
        f"SQL 执行记录：\n{_format_diagnosis_sql_logs(sql_logs)}\n"
        "下一步（**勿重复调用本工具**）：将 item_rows / knowledge_rows 传给 "
        "build_subject_diagnosis_sections_tool，或将 score_result 传给 "
        "compute_score_stats_tool(exec_result=...)，然后 render_html_report → terminate。"
    )
    if errors:
        content += "\n部分 SQL 失败：\n" + "\n".join(errors[:5])
    if warnings:
        content += "\n" + "\n".join(warnings)
    return ToolResult(
        content=content,
        data={
            "item_rows": item_rows,
            "knowledge_rows": knowledge_rows,
            "score_result": score_result,
            "warnings": warnings,
            "errors": errors,
            "sql_logs": sql_logs,
        },
    )


@tool()
def build_subject_diagnosis_sections_tool(
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    score_result: dict[str, Any] | None = None,
    fetch_data: dict[str, Any] | None = None,
    school_name: str = "",
    exam_name: str = "",
    subject_name: str = "",
    class_name: str = "",
    weak_threshold: float = 60.0,
    render: bool = True,
) -> ToolResult:
    """组装科目诊断报告中的 ITEM_TABLE / KNOWLEDGE_TABLE / SUMMARY / RECOMMENDATIONS。

    **适用时机**：ToolExpert 组装 ``education/subject_diagnosis.html`` 时，上游
    DataAnalyst 已分别查出小题明细与知识点汇总，调用本工具**确定性**生成表格与
    薄弱知识点分析文案，避免 LLM 漏写知识点建议。

    默认 ``render=True``：在组装完成后**直接渲染 HTML 并推送到前端**（与
    ``build_subject_diagnosis_report_tool`` 相同载荷），LLM 调完只需 ``terminate``，
    **无需**再调 ``select_report_template_tool`` / ``build_chart_option_tool`` /
    ``render_html_report``。

    **入参简化**：可直接传 ``fetch_data=fetch_subject_diagnosis_data_tool 返回的 data``，
    本工具会自动提取 item_rows / knowledge_rows / score_result 并内部计算 stats，
    **无需**先调 ``compute_score_stats_tool``。

    Args:
        item_rows: 小题行，含 question_no / knowledge_name / score_rate 等。
        knowledge_rows: 知识点行，含 knowledge_name / score_rate / question_count。
        stats: 可选整体 KPI（count/avg/pass_rate/excellent_rate/segments）。
            未传但提供了 score_result/fetch_data 时自动计算。
        score_result: fetch 工具返回的 ``{"columns": [...], "rows": [...]}``。
        fetch_data: fetch 工具返回的完整 data 字典（优先级最高，自动提取上述字段）。
        school_name / exam_name / subject_name / class_name: 用于报告标题与范围。
        weak_threshold: 得分率低于该值视为薄弱知识点（默认 60）。
        render: True（默认）渲染 HTML；False 仅返回 data 字典（调试或手动 render）。
    """
    # fetch_data 优先：自动提取 item_rows / knowledge_rows / score_result
    if isinstance(fetch_data, dict):
        if item_rows is None:
            item_rows = fetch_data.get("item_rows")
        if knowledge_rows is None:
            knowledge_rows = fetch_data.get("knowledge_rows")
        if score_result is None:
            score_result = fetch_data.get("score_result")
    items = list(item_rows or [])
    knowledge = enrich_knowledge_rows(list(knowledge_rows or []))

    # stats 未传但有 score_result/fetch_data 时，内部计算 KPI
    if stats is None and score_result:
        cfg = _get_effective_config()
        bundle_cfg = load_schema_from_config()
        if bundle_cfg is not None:
            cfg.pass_ratio = bundle_cfg.meta.pass_ratio
            cfg.excellent_ratio = bundle_cfg.meta.excellent_ratio
        rows_s, cols_s = _coerce_exec_result(score_result, None, None)
        values: list[float] = []
        fs_val: float | None = None
        if cols_s and rows_s:
            sf = _guess_field(cols_s, _SCORE_FIELD_HINTS) or "score"
            try:
                si = cols_s.index(sf)
            except ValueError:
                si = 0
            for row in rows_s:
                try:
                    v = row[si] if isinstance(row, (list, tuple)) else row.get(sf)
                    if v is not None and v != "":
                        values.append(float(v))
                except (TypeError, ValueError, IndexError):
                    continue
            resolved_fs, _ = _extract_full_score_from_rows(
                rows_s, cols_s,
                _guess_field(cols_s, _FULL_SCORE_FIELD_HINTS) or "exam_score",
            )
            fs_val = resolved_fs
        stats = _compute_stats(values, cfg, fs_val)

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
            "knowledge_bar",
            {
                "categories": [str(r.get("knowledge_name") or "") for r in knowledge[:12]],
                "values": [float(r.get("score_rate") or 0) for r in knowledge[:12]],
            },
            title="知识点得分率",
        )
    else:
        data["KNOWLEDGE_CHART"] = ""
    weak_cnt = sum(1 for r in knowledge if r.get("level") == "需加强")

    if not render:
        content = (
            f"科目诊断区块已组装：小题 {len(items)} 条，知识点 {len(knowledge)} 个"
            f"（薄弱 {weak_cnt} 个）。请将返回 data 与 KPI 字段合并后填入 render_html_report。"
        )
        return ToolResult(content=content, data=data)

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    st = stats or {}
    segments = st.get("segments") or []
    has_scores = bool(st.get("count"))
    scope_label = class_name or school_name or "全年级"
    report_data: dict[str, Any] = {
        "REPORT_TITLE": f"{subject_name or '科目'}诊断报告",
        "REPORT_SUBTITLE": f"{school_name} {class_name}".strip(),
        "REPORT_TIME": _now_str(),
        "SUBJECT_NAME": subject_name or "全科",
        "EXAM_NAME": exam_name or "本次考试",
        "SCOPE": scope_label,
        "AVG_SCORE": _fmt_val(st.get("avg")),
        "PASS_RATE": _fmt_val(st.get("pass_rate")),
        "EXCELLENT_RATE": _fmt_val(st.get("excellent_rate")),
        "STDEV": _fmt_val(st.get("stdev")),
    }
    report_data.update(data)
    if not has_scores:
        report_data["SUMMARY"] = (
            report_data.get("SUMMARY", "")
            + "<p class='edu-sub' style='color:#ff4d4f'>⚠️ 成绩表（tb_score）未查到匹配记录，"
            "KPI 与分数段分布为空。</p>"
        )
    if segments and has_scores:
        report_data["SCORE_DIST_CHART"] = _build_chart_option(
            "score_distribution",
            {
                "segments": [
                    {"label": s.get("label", ""), "count": s.get("count", 0)} for s in segments
                ],
                "pass_rate": st.get("pass_rate") or 0,
            },
            title="分数段分布",
        )
        report_data["SEGMENT_TABLE"] = _segment_table_html(segments)
    else:
        report_data.setdefault("SCORE_DIST_CHART", "")
        report_data.setdefault("SEGMENT_TABLE", "")

    template_name = "education/subject_diagnosis.html"
    title = str(report_data.get("REPORT_TITLE") or "科目诊断报告")
    try:
        raw_html = _render_template_html(template_name, report_data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:
        return ToolResult(
            content=f"科目诊断报告渲染失败：{e}（sections data 已组装，可改 render=False 后手动 render_html_report）",
            data={"error": str(e), **data},
        )
    if not safe_html.strip():
        return ToolResult(
            content="科目诊断报告渲染失败：HTML 为空。",
            data={"error": "empty html", **data},
        )

    payload: dict[str, Any] = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    score_note = (
        f"{int(st.get('count') or 0)} 条成绩"
        if has_scores
        else "⚠️ 成绩为空——KPI 将显示为空"
    )
    return ToolResult(
        content=(
            f"科目诊断报告已渲染完成（小题 {len(items)} 条，知识点 {len(knowledge)} 个，"
            f"薄弱 {weak_cnt} 个、{score_note}、HTML {len(safe_html)} 字符）。\n"
            "报告已自动推送到前端，直接调 terminate 结束即可。\n"
            "**禁止**再调 select_report_template / build_chart_option / render_html_report。"
        ),
        data=payload,
    )


@tool()
def build_subject_diagnosis_report_tool(
    datasource_id: int,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    class_name: str = "",
    audience: str = "default",
    workspace_oid: int | None = None,
    user_id: int | None = None,
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

    # ---------- 1. 查数 ----------
    try:
        db_type, _config, ds_name = _load_datasource(datasource_id, workspace_oid)
    except Exception as e:
        return ToolResult(
            content=f"build_subject_diagnosis_report_tool 失败：{e}",
            data={"error": str(e)},
        )

    bundle = _fetch_subject_diagnosis_rows(
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
        user_id=user_id,
        school_name=school_name,
        subject_name=subject_name,
        exam_name=exam_name,
        class_name=class_name,
        db_type=db_type,
    )
    item_rows = bundle["item_rows"]
    knowledge_rows = bundle["knowledge_rows"]
    score_values = bundle["score_values"]
    full_score = bundle.get("full_score")
    diag_warnings = bundle.get("warnings") or []
    diag_errors = bundle.get("errors") or []
    sql_logs = bundle.get("sql_logs") or []

    if not item_rows and not knowledge_rows and not score_values:
        return ToolResult(
            content=(
                f"build_subject_diagnosis_report_tool 未查到数据（ds={ds_name}，"
                f"school={school_name}，subject={subject_name}，exam={exam_name}，class={class_name}）。\n"
                f"SQL 执行记录：\n{_format_diagnosis_sql_logs(sql_logs)}\n"
                + ("\n".join(diag_errors[:5]) if diag_errors else "")
            ),
            data={"error": "no data", "sql_logs": sql_logs},
        )

    # ---------- 2. 统计 ----------
    cfg = _get_effective_config()
    stats = _compute_stats(score_values, cfg, full_score)

    # ---------- 3. 组装区块 ----------
    knowledge_enriched = enrich_knowledge_rows(knowledge_rows)
    weak_threshold = 60.0
    summary_html = build_diagnosis_summary(
        school_name=school_name,
        exam_name=exam_name,
        subject_name=subject_name,
        stats=stats,
        item_rows=item_rows,
        knowledge_rows=knowledge_enriched,
        weak_threshold=weak_threshold,
    )
    if diag_warnings:
        summary_html += "<p class='edu-sub'>" + "；".join(diag_warnings) + "</p>"
    if diag_errors and not item_rows:
        summary_html += "<p class='edu-sub'>小题 SQL 错误：" + "；".join(diag_errors[:3]) + "</p>"
    if not score_values:
        summary_html += (
            "<p class='edu-sub' style='color:#ff4d4f'>⚠️ 成绩表（tb_score）未查到匹配记录，"
            "KPI 与分数段分布为空。可能原因：tb_score.class 与传入班级名不一致、"
            "tb_score.subject_name 与科目不一致、或成绩尚未导入。</p>"
        )
    section_data: dict[str, Any] = {
        "ITEM_TABLE": build_item_table_html(item_rows),
        "KNOWLEDGE_TABLE": build_knowledge_table_html(knowledge_enriched),
        "WEAK_KNOWLEDGE_LIST": "、".join(
            str(r.get("knowledge_name") or "")
            for r in knowledge_enriched
            if r.get("level") == "需加强"
        )[:500],
        "SUMMARY": summary_html,
        "RECOMMENDATIONS": build_diagnosis_recommendations(
            knowledge_rows=knowledge_enriched,
            item_rows=item_rows,
            weak_threshold=weak_threshold,
        ),
    }
    if knowledge_enriched:
        section_data["KNOWLEDGE_CHART"] = _build_chart_option(
            "knowledge_bar",
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
    sql_note = _format_diagnosis_sql_logs(sql_logs)
    score_note = (
        f"{len(score_values)} 条成绩"
        if score_values
        else "⚠️ 成绩(tb_score)为空——KPI 与分数段将显示为空，请检查 tb_score 是否有匹配记录"
    )
    return ToolResult(
        content=(
            f"科目诊断报告已渲染完成（{len(item_rows)} 题、{len(knowledge_enriched)} 知识点、"
            f"薄弱 {weak_cnt} 个、{score_note}、HTML {len(safe_html)} 字符）。\n"
            f"小题查询 SQL 记录：\n{sql_note}\n"
            "报告已自动推送到前端，直接调 terminate 结束即可。"
        ),
        data=payload,
    )


def _format_diagnosis_sql_logs(logs: list[dict[str, Any]]) -> str:
    if not logs:
        return "（无 SQL 记录）"
    lines: list[str] = []
    for entry in logs:
        status = "成功" if entry.get("success") else "失败"
        lines.append(
            f"- [{entry.get('phase')}/{entry.get('label')}] {status}，"
            f"{entry.get('row_count', 0)} 行"
            + (f"：{entry.get('message')}" if entry.get("message") else "")
        )
    return "\n".join(lines)


def _esc(value: str) -> str:
    return value.replace("'", "''")


def exam_name_like_candidates(exam_name: str) -> list[str]:
    """从完整考试名生成多个 LIKE 候选（去掉省级冠名等），提高匹配率。"""
    raw = (exam_name or "").strip()
    if not raw:
        return []

    out: list[str] = []

    def add(s: str) -> None:
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)

    add(raw)
    add(re.sub(r"^[\u4e00-\u9fff]{2,12}(?:省|市|自治区)", "", raw).strip())
    m = re.search(r"((?:高|初)[一二三四五六七八九].+)$", raw)
    if m:
        add(m.group(1).strip())
    if "期末" in raw:
        add("期末质量检测")
    return out


def _diagnosis_where_parts(
    *,
    school_name: str = "",
    class_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    exam_ids: list[str] | None = None,
    skip_exam_name: bool = False,
    exam_id_expr: str = "sc.exam_id",
) -> list[str]:
    parts: list[str] = []
    if school_name:
        parts.append(f"sch.name = '{_esc(school_name)}'")
    if class_name:
        parts.append(f"sc.class = '{_esc(class_name)}'")
    if subject_name:
        parts.append(f"sc.subject_name = '{_esc(subject_name)}'")
    if exam_ids:
        lits = ", ".join(f"'{_esc(str(x))}'" for x in exam_ids if str(x).strip())
        if lits:
            parts.append(f"{exam_id_expr} IN ({lits})")
    elif exam_name and not skip_exam_name:
        cands = exam_name_like_candidates(exam_name)
        if cands:
            ors = " OR ".join(f"e.exam_name LIKE '%{_esc(c)}%'" for c in cands)
            parts.append(f"({ors})")
    return parts


def _diagnosis_where_clause(**kwargs: Any) -> str:
    parts = _diagnosis_where_parts(**kwargs)
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def _diagnosis_where_clause_pair(**kwargs: Any) -> tuple[str, str]:
    """返回 (detail_where, score_where)；小题 SQL 用 sd.exam_id，成绩 SQL 用 sc.exam_id。"""
    detail = _diagnosis_where_clause(**kwargs, exam_id_expr="sd.exam_id")
    score = _diagnosis_where_clause(**kwargs, exam_id_expr="sc.exam_id")
    return detail, score


def _score_rate_sql(avg_expr: str, denom_expr: str, db_type: str) -> str:
    if db_type == "mysql":
        return f"ROUND({avg_expr} * 100.0 / NULLIF({denom_expr}, 0), 2)"
    return f"ROUND(({avg_expr})::numeric / NULLIF({denom_expr}, 0) * 100, 2)"


def _diagnosis_sql_bundle(
    where_clause_detail: str,
    where_clause_score: str,
    db_type: str = "pg",
) -> tuple[str, str, str, str]:
    """返回 (item_sql, knowledge_sql, score_sql, exam_id_sql)。"""
    full_score_expr = "COALESCE(eq.question_score, sd.question_score)"
    item_rate = _score_rate_sql(f"AVG(sd.score)", full_score_expr, db_type)
    know_rate = _score_rate_sql(
        "SUM(sd.score)",
        f"SUM({full_score_expr})",
        db_type,
    )
    item_sql = (
        "SELECT sd.question_no,\n"
        "       COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        f"       {full_score_expr} AS full_score,\n"
        "       ROUND(AVG(sd.score), 2) AS avg_score,\n"
        f"       {item_rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        "LEFT JOIN tb_knowledge k ON eq.knowledge_id = k.id\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_detail
        + f"\nGROUP BY sd.question_no, COALESCE(k.knowledge_name, '未关联知识点'), {full_score_expr}\n"
        "ORDER BY sd.question_no\nLIMIT 1000"
    )
    knowledge_sql = (
        "SELECT COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        "       COUNT(DISTINCT sd.question_no) AS question_count,\n"
        f"       {know_rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        "LEFT JOIN tb_knowledge k ON eq.knowledge_id = k.id\n"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_detail
        + "\nGROUP BY COALESCE(k.knowledge_name, '未关联知识点')\n"
        "ORDER BY score_rate ASC\nLIMIT 1000"
    )
    score_sql = (
        "SELECT sc.score AS score, sc.exam_score AS exam_score, sc.exam_id AS exam_id\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_score
        + "\nLIMIT 1000"
    )
    exam_id_sql = (
        "SELECT DISTINCT sc.exam_id AS exam_id\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_score
        + "\nLIMIT 50"
    )
    return item_sql, knowledge_sql, score_sql, exam_id_sql


def _rows_to_dicts(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    cols = result.get("columns") or []
    raw_rows = result.get("rows") or []
    return [dict(zip(cols, row)) for row in raw_rows]


def _parse_score_result(result: dict[str, Any] | None) -> tuple[list[float], float | None, list[str]]:
    score_values: list[float] = []
    full_score: float | None = None
    exam_ids: list[str] = []
    if not isinstance(result, dict):
        return score_values, full_score, exam_ids
    cols = result.get("columns") or []
    raw_rows = result.get("rows") or []
    score_idx = cols.index("score") if "score" in cols else 0
    fs_idx = cols.index("exam_score") if "exam_score" in cols else -1
    exam_idx = cols.index("exam_id") if "exam_id" in cols else -1
    seen_exams: set[str] = set()
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
        if exam_idx >= 0:
            eid = str(row[exam_idx]).strip()
            if eid and eid not in seen_exams:
                seen_exams.add(eid)
                exam_ids.append(eid)
    return score_values, full_score, exam_ids


def _fetch_subject_diagnosis_rows(
    *,
    datasource_id: int,
    workspace_oid: int | None,
    user_id: int | None,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    class_name: str = "",
    db_type: str = "pg",
) -> dict[str, Any]:
    """查小题/知识点/成绩，带考试名放宽与 exam_id 回退。"""
    base_kw = {
        "school_name": school_name,
        "class_name": class_name,
        "subject_name": subject_name,
        "exam_name": exam_name,
    }
    item_rows: list[dict[str, Any]] = []
    knowledge_rows: list[dict[str, Any]] = []
    score_values: list[float] = []
    full_score: float | None = None
    warnings: list[str] = []
    errors: list[str] = []
    sql_logs: list[dict[str, Any]] = []

    def run_bundle(
        where_clause_detail: str,
        where_clause_score: str,
        phase: str,
    ) -> tuple[list[dict], list[dict], list[float], float | None, list[str]]:
        item_sql, knowledge_sql, score_sql, exam_id_sql = _diagnosis_sql_bundle(
            where_clause_detail,
            where_clause_score,
            db_type,
        )
        ir: list[dict[str, Any]] = []
        kr: list[dict[str, Any]] = []
        sv: list[float] = []
        fs: float | None = None
        eids: list[str] = []
        for label, sql in (
            ("item_detail", item_sql),
            ("knowledge", knowledge_sql),
            ("score", score_sql),
        ):
            success, msg, result, sql_run = _run_edu_sql(
                sql,
                datasource_id=datasource_id,
                workspace_oid=workspace_oid,
                user_id=user_id,
            )
            row_count = len(result.get("rows") or []) if isinstance(result, dict) else 0
            sql_logs.append(
                {
                    "phase": phase,
                    "label": label,
                    "success": success,
                    "row_count": row_count,
                    "message": msg if not success else "",
                    "sql_preview": (sql_run or sql)[:600],
                }
            )
            if not success:
                errors.append(f"[{phase}/{label}] {msg}")
                continue
            if not isinstance(result, dict):
                continue
            if label == "item_detail":
                ir = _rows_to_dicts(result)
            elif label == "knowledge":
                kr = _rows_to_dicts(result)
            else:
                sv, fs, eids = _parse_score_result(result)
        if not eids:
            success, msg, result, sql_run = _run_edu_sql(
                exam_id_sql,
                datasource_id=datasource_id,
                workspace_oid=workspace_oid,
                user_id=user_id,
            )
            sql_logs.append(
                {
                    "phase": phase,
                    "label": "exam_ids",
                    "success": success,
                    "row_count": len(result.get("rows") or []) if isinstance(result, dict) else 0,
                    "message": msg if not success else "",
                    "sql_preview": (sql_run or exam_id_sql)[:600],
                }
            )
            if not success:
                errors.append(f"[{phase}/exam_ids] {msg}")
            elif isinstance(result, dict):
                for row in result.get("rows") or []:
                    if row and str(row[0]).strip():
                        eids.append(str(row[0]).strip())
        return ir, kr, sv, fs, eids

    detail_wc, score_wc = _diagnosis_where_clause_pair(**base_kw)
    item_rows, knowledge_rows, score_values, full_score, exam_ids = run_bundle(
        detail_wc, score_wc, "primary"
    )

    if not item_rows and score_values and exam_ids:
        detail_wc2, score_wc2 = _diagnosis_where_clause_pair(
            **base_kw, exam_ids=exam_ids, skip_exam_name=True
        )
        item_rows, knowledge_rows, _sv2, _fs2, _ = run_bundle(
            detail_wc2, score_wc2, "fallback_exam_id"
        )
        if item_rows:
            warnings.append("小题明细已按成绩记录的 exam_id 回退查询（考试名与库中不完全一致）")

    if not item_rows and exam_name:
        detail_wc3, score_wc3 = _diagnosis_where_clause_pair(**base_kw, skip_exam_name=True)
        item_rows, knowledge_rows, score_values, full_score, exam_ids = run_bundle(
            detail_wc3, score_wc3, "fallback_no_exam_name"
        )
        if not item_rows and score_values and exam_ids:
            detail_wc4, score_wc4 = _diagnosis_where_clause_pair(
                **base_kw, exam_ids=exam_ids, skip_exam_name=True
            )
            item_rows, knowledge_rows, _, _, _ = run_bundle(
                detail_wc4, score_wc4, "fallback_exam_id_only"
            )
            if item_rows:
                warnings.append("小题明细已按班级/科目范围回退查询")

    if score_values and not item_rows:
        warnings.append(
            "已查到总分但无小题明细：请确认 tb_score_detail 是否已导入，"
            "且 question_id 能关联 tb_exam_question（或存在 question_score 列）"
        )

    # 有小题但成绩为空：放宽 class 过滤重试一次（tb_score.class 可能与传入值不一致）
    if not score_values and item_rows:
        relaxed_kw = {**base_kw, "class_name": ""}
        detail_wc_r, score_wc_r = _diagnosis_where_clause_pair(**relaxed_kw, skip_exam_name=True)
        _, _, sv_r, fs_r, _ = run_bundle(detail_wc_r, score_wc_r, "relaxed_score_no_class")
        if sv_r:
            score_values = sv_r
            full_score = fs_r if fs_r is not None else full_score
            warnings.append(
                f"成绩记录按班级 `{class_name}` 未查到，已放宽班级过滤重试"
                f"（命中 {len(sv_r)} 条）。建议核查 tb_score.class 实际值。"
            )

    return {
        "item_rows": item_rows,
        "knowledge_rows": knowledge_rows,
        "score_values": score_values,
        "full_score": full_score,
        "warnings": warnings,
        "errors": errors,
        "sql_logs": sql_logs,
    }


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
    - ``knowledge_bar``：``data={"categories": [...], "values": [...]}``（知识点得分率）
    - ``trend_line``：``data={"x_labels": [...], "series": [{name, values}]}``
    - ``pie`` / ``correlation_bar`` / ``progress_regress_bar`` / ``trajectory_line`` 等同上。

    **别名（自动映射）**：``bar`` / ``column`` 按 data 结构推断具体类型；
    ``line``→``trend_line``，``radar``→``subject_radar``。
    例如知识点柱图可写 ``chart_type="bar"`` + ``categories``/``values``。

    Returns:
        ``content`` 为可读说明，``data`` 为 ``{"option": "<JSON 字符串>", "chart_type": "..."}``。
        未知 chart_type 返回 ``data={"error": ..., "option": ""}``。
    """
    from src.agent.education.charts import SUPPORTED_CHART_TYPES, resolve_chart_type

    resolved = resolve_chart_type(chart_type, data or {}, title)
    option = _build_chart_option(chart_type, data or {}, title)
    if not option:
        supported = " / ".join(SUPPORTED_CHART_TYPES)
        return ToolResult(
            content=(
                f"build_chart_option_tool 失败：未知 chart_type `{chart_type}`。"
                f" 可选：{supported}；别名 bar/column/line/radar 亦支持。"
            ),
            data={"error": "unknown chart_type", "chart_type": chart_type, "option": ""},
        )
    return ToolResult(
        content=f"已生成 ECharts option（{resolved}），可直接填入模板 CHART 字段。",
        data={"option": option, "chart_type": resolved},
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
