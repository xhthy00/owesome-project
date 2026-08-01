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

import logging
import re
from typing import Any

from src.agent.education.charts import build_chart_option as _build_chart_option
from src.agent.education.config import EducationConfig
from src.agent.education.config_store import get_config as _get_effective_config
from src.agent.education.report_types import Audience, ReportType, report_type_label
from src.agent.education.schema_mapping import (
    ScoreSchemaMapping,
    infer_normalized_mapping,
    infer_wide_mapping,
    load_schema_from_config,
    validate_mapping_against_schema,
)

logger = logging.getLogger(__name__)
from src.agent.education.aggregation import DIMENSIONS, aggregate_by as _aggregate_by
from src.agent.education.capability import detect_available_dimensions
from src.agent.education.cross_analysis import cross_analyze as _cross_analyze
from src.agent.education.diagnostic_report import build_diagnostic_data as _build_diagnostic_data
from src.agent.education.knowledge_tier import (
    ABILITY_LABELS,
    build_ability_tier_insight,
    build_ability_tier_summary,
    build_ability_tier_table_html as _build_ability_tier_table_html,
    build_question_type_compare_chart_payload as _build_question_type_compare_chart_payload,
    build_question_type_table_html as _build_question_type_table_html,
)
from src.agent.education.stats import (
    compute_item_metrics as _compute_item_metrics,
    compute_rankings as _compute_rankings,
    compute_score_stats as _compute_stats,
    identify_at_risk_students as _identify_at_risk,
    normalize_segments as _normalize_segments,
)
from src.agent.education.comprehensive import build_comprehensive_data as _build_comprehensive_data
from src.agent.education.student_exam import build_student_exam_data as _build_student_exam_data
from src.agent.education.school_intervention import (
    build_class_compare_table_html,
    build_intervention_section_html,
    build_school_intervention_insights,
)
from src.agent.education.subject_diagnosis import (
    build_diagnosis_recommendations,
    build_diagnosis_summary,
    build_item_table_html,
    build_knowledge_compare_chart_payload,
    build_knowledge_table_html,
    build_segment_table_html,
    collect_class_names,
    enrich_knowledge_rows,
    knowledge_names_subquery_join,
    knowledge_weighted_join,
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
    # 及格/优秀以异常规则库表为准，不用 schema.json 里的默认 0.6/0.85
    try:
        cfg = _get_effective_config()
        pass_ratio = float(cfg.pass_ratio)
        excellent_ratio = float(cfg.excellent_ratio)
    except Exception:
        pass_ratio = float(meta.pass_ratio)
        excellent_ratio = float(meta.excellent_ratio)
    lines = [
        f"已加载教育 Schema 配置（来源={m.source}）。",
        f"- score 表：{m.tables.get('score', '')}",
        f"- school 表：{m.tables.get('school', '')}",
        f"- 满分字段：{m.fields.get('full_score', '（未配置）')}（运行时从库读取，非固定值）",
        f"- 及格/优秀比例（异常规则配置）：{pass_ratio} / {excellent_ratio}"
        f"（即 {round(pass_ratio * 100, 2)}% / {round(excellent_ratio * 100, 2)}%）",
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
    data["pass_ratio"] = pass_ratio
    data["excellent_ratio"] = excellent_ratio
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
_EXAM_FIELD_HINTS = ("exam_name", "exam", "考试名称", "考试")
_STUDENT_FIELD_HINTS = (
    "student_name",
    "student_id",
    "student",
    "姓名",
    "学生姓名",
    "学号",
    "学生",
)
_SUBJECT_FIELD_HINTS = ("subject_name", "subject", "科目")
_TOTAL_FIELD_HINTS = ("total_score", "total", "总分")


def _guess_long_table_fields(
    columns: list[str],
    *,
    exam_field: str,
    student_field: str,
    subject_field: str,
    score_field: str,
    total_field: str,
) -> tuple[str, str, str, str, str]:
    """按列名猜测长表字段；缺省名不在 columns 时自动替换。"""
    col_set = {str(c) for c in columns}
    if exam_field not in col_set:
        exam_field = _guess_field(columns, _EXAM_FIELD_HINTS) or exam_field
    if student_field not in col_set:
        student_field = _guess_field(columns, _STUDENT_FIELD_HINTS) or student_field
    if subject_field not in col_set:
        subject_field = _guess_field(columns, _SUBJECT_FIELD_HINTS) or subject_field
    if score_field not in col_set:
        score_field = _guess_field(columns, _SCORE_FIELD_HINTS) or score_field
    if total_field not in col_set:
        total_field = _guess_field(columns, _TOTAL_FIELD_HINTS) or total_field
    return exam_field, student_field, subject_field, score_field, total_field


def _aggregate_long_table_records(
    rows: list[list[Any]],
    columns: list[str],
    *,
    exam_field: str,
    student_field: str,
    subject_field: str,
    score_field: str,
    total_field: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """长表 (exam×student×subject) → records；返回 (records, exam_order)。"""
    idx = {c: i for i, c in enumerate(columns)}

    def _cell(row: list[Any], key: str, default: Any = None) -> Any:
        i = idx.get(key)
        if i is None or i >= len(row):
            return default
        return row[i]

    agg: dict[tuple[str, str], dict[str, Any]] = {}
    exam_seen: list[str] = []
    exam_set: set[str] = set()
    for row in rows:
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
        score_raw = _cell(row, score_field)
        if subject:
            try:
                slot["subjects"][subject] = float(score_raw)
            except (TypeError, ValueError):
                pass
        total_val = _cell(row, total_field) if total_field in idx else None
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
                slot["total"] = slot.get("total", 0) + float(score_raw or 0)
            except (TypeError, ValueError):
                pass
        elif not subject and score_raw is not None:
            # 单科宽表：无 subject 列时用 score 作为总分（含 0 分）
            try:
                sv = float(score_raw)
                slot["total"] = sv
                slot["subjects"].setdefault("成绩", sv)
            except (TypeError, ValueError):
                pass
    for slot in agg.values():
        subs = slot.get("subjects") or {}
        if subs:
            slot["total"] = sum(float(v) for v in subs.values())
        slot.pop("_summed", None)
    return list(agg.values()), exam_seen


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


def _normalize_aggregate_rows(
    rows: Any = None,
    *,
    exec_result: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    report_data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """将 execute_sql / fetch / LLM 多种形态统一为 dict 行列表。"""
    warnings: list[str] = []

    if isinstance(rows, dict):
        sr = rows.get("score_rows")
        if isinstance(sr, list) and sr and isinstance(sr[0], dict):
            return [dict(r) for r in sr], warnings
        if "columns" in rows or "rows" in rows or "score_result" in rows:
            exec_result = rows if exec_result is None else exec_result
            rows = None
        else:
            rows = None

    coerced_rows, coerced_cols = _coerce_exec_result(
        exec_result,
        rows if isinstance(rows, list) else None,
        columns,
    )
    if coerced_rows:
        if isinstance(coerced_rows[0], dict):
            return [dict(r) for r in coerced_rows], warnings
        if isinstance(coerced_rows[0], (list, tuple)) and coerced_cols:
            return [dict(zip(coerced_cols, r)) for r in coerced_rows], warnings

    if isinstance(rows, list) and rows:
        if all(isinstance(r, dict) for r in rows):
            return [dict(r) for r in rows], warnings
        if len(rows) == 1 and isinstance(rows[0], dict):
            nested = rows[0]
            if nested.get("rows") and nested.get("columns"):
                cols = list(nested.get("columns") or [])
                return [dict(zip(cols, r)) for r in (nested.get("rows") or [])], warnings
        if rows and isinstance(rows[0], (list, tuple)):
            warnings.append(
                "rows 为二维数组但未提供 columns/exec_result，无法解析；"
                "请传 exec_result={columns, rows} 或 columns 参数"
            )

    if report_data:
        from src.agent.education.query_parse import resolve_diagnostic_score_rows

        upstream = resolve_diagnostic_score_rows(report_data=report_data)
        if upstream:
            warnings.append("已从上游 report_data 自动读取 score_rows")
            return upstream, warnings

    return [], warnings


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
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
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
    # 分数段可取 schema；及格/优秀比例以异常规则库表为准，不覆盖。
    bundle = load_schema_from_config()
    if bundle is not None and bundle.meta.score_segment_ratios:
        cfg.score_segment_ratios = list(bundle.meta.score_segment_ratios)
    if pass_threshold is not None:
        cfg.pass_threshold = float(pass_threshold)
    if excellent_threshold is not None:
        cfg.excellent_threshold = float(excellent_threshold)

    from src.agent.education.query_parse import resolve_stats_input

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    scores, exec_result = resolve_stats_input(
        scores=scores,
        exec_result=exec_result,
        last_exec_result=ctx.get("last_exec_result"),
        report_data=report_data or ctx.get("report_data"),
    )

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
    pass_line = stats.get("pass_line")
    excellent_line = stats.get("excellent_line")
    pr = stats.get("pass_ratio")
    er = stats.get("excellent_ratio")
    content = (
        f"成绩统计完成：共 {stats['count']} 人，均分 {stats['avg']}，"
        f"满分 {stats['full_score']}，"
        f"及格线 {pass_line}（按异常规则 {stats['full_score']}×{round(float(pr or 0) * 100, 2)}%），"
        f"优秀线 {excellent_line}（按异常规则 {stats['full_score']}×{round(float(er or 0) * 100, 2)}%），"
        f"及格率 {stats['pass_rate']}%，优秀率 {stats['excellent_rate']}%，"
        f"标准差 {stats['stdev']}。"
        "撰写结论时必须使用上述及格线/优秀线，禁止改用惯例 60%/85%。"
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
    report_data: dict[str, Any] | None = None,
    sub_task: str = "",
) -> ToolResult:
    """直接从数据库查询科目诊断所需的小题明细与知识点汇总（确定性 SQL，无需 LLM 写 JOIN）。

    **适用时机**：科目诊断报告中需要「每一小题 + 知识点」和「知识点得分率」时，
    **优先调用本工具**，而不是自己写 SQL——本工具内部已固定经
    ``tb_exam_question_knowledge`` JOIN ``tb_knowledge``（掌握度按 weight 归一化拆分），
    确保知识点名称来自数据库而非臆造。

    **Team 分工**：仅 **fetch 子任务** 调用本工具；**组装子任务**（含
    ``build_diagnostic_report_data_tool``）**禁止**再调本工具——fetch 结果已由
    运行时 ``report_data`` 自动注入组装工具。

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
    from src.agent.education.query_parse import (
        find_upstream_fetch_data,
        sub_task_primary_tool,
    )

    task_text = (sub_task or "").strip()
    primary_tool = sub_task_primary_tool(task_text)
    if primary_tool == "build_diagnostic_report_data_tool" or (
        not primary_tool and find_upstream_fetch_data(report_data)
    ):
        return ToolResult(
            content=(
                "本子任务应组装诊断报告，**禁止重复 fetch**。"
                "请直接调 build_diagnostic_report_data_tool("
                "scope_label=全市, exam_name=..., subject_name=..., render=true)；"
                "score_rows 与 fetch_data 将由工具自动从 report_data 读取。"
            ),
            data={"error": "fetch_not_allowed_in_build_subtask"},
        )

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
    item_class_rows = bundle.get("item_class_rows") or []
    knowledge_class_rows = bundle.get("knowledge_class_rows") or []
    score_values = bundle["score_values"]
    score_rows = bundle.get("score_rows") or []
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
    )
    if item_class_rows or knowledge_class_rows:
        content += (
            f"- 班级横向对比小题：{len(item_class_rows)} 行\n"
            f"- 班级横向对比知识点：{len(knowledge_class_rows)} 行\n"
        )
    content += (
        f"SQL 执行记录：\n{_format_diagnosis_sql_logs(sql_logs)}\n"
        "下一步：**本步请 terminate**（禁止同子任务渲染报告）。"
        "组装留给后续子任务："
        "科目诊断调 `build_subject_diagnosis_sections_tool(render=true)`；"
        "全市诊断调 `build_diagnostic_report_data_tool(render=true)`——"
        "工具会自动读取本步 fetch 结果。"
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
            "item_class_rows": item_class_rows,
            "knowledge_class_rows": knowledge_class_rows,
            "score_rows": score_rows,
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
    report_data: dict[str, Any] | None = None,
    sub_task: str = "",
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """组装科目诊断报告中的 ITEM_TABLE / KNOWLEDGE_TABLE / SUMMARY / RECOMMENDATIONS。

    **适用时机**：ToolExpert 组装 ``education/subject_diagnosis.html`` 时，上游
    DataAnalyst 已分别查出小题明细与知识点汇总，调用本工具**确定性**生成表格与
    薄弱知识点分析文案，避免 LLM 漏写知识点建议。

    默认 ``render=True``：在组装完成后**直接渲染 HTML 并推送到前端**（与
    ``build_subject_diagnosis_report_tool`` 相同载荷），LLM 调完只需 ``terminate``，
    **无需**再调 ``select_report_template_tool`` / ``build_chart_option_tool`` /
    ``render_html_report``。

    **入参简化**：运行时会自动从本轮 ``last_fetch_data`` / 上游 ``report_data``
    读取 fetch 结果并计算 stats；**禁止**手抄 ``fetch_data`` / ``item_rows``
   （易 JSON 截断成空表）。只需传 school/exam/subject/class + ``render=true``。

    Args:
        item_rows: 小题行，含 question_no / knowledge_name / score_rate 等。
        knowledge_rows: 知识点行，含 knowledge_name / score_rate / question_count。
        stats: 可选整体 KPI（count/avg/pass_rate/excellent_rate/segments）。
            未传但提供了 score_result/fetch_data 时自动计算。
        score_result: fetch 工具返回的 ``{"columns": [...], "rows": [...]}``。
        fetch_data: fetch 工具返回的完整 data 字典（通常由运行时自动注入）。
        school_name / exam_name / subject_name / class_name: 用于报告标题与范围。
        weak_threshold: 得分率低于该值视为薄弱知识点（默认 60）。
        render: True（默认）渲染 HTML；False 仅返回 data 字典（调试或手动 render）。
    """
    from src.agent.education.query_parse import (
        resolve_subject_diagnosis_fetch_data,
        sub_task_primary_tool,
    )

    task_text = (sub_task or "").strip()
    if render and sub_task_primary_tool(task_text) == "fetch_subject_diagnosis_data_tool":
        return ToolResult(
            content=(
                "本子任务禁止渲染科目诊断报告。若任务是 fetch_subject_diagnosis_data_tool，"
                "请仅调 fetch 后 terminate；build_subject_diagnosis_sections_tool(render=true) "
                "留给下一步组装子任务。"
            ),
            data={"error": "render_not_allowed_in_fetch_subtask"},
        )

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    fetch_data = resolve_subject_diagnosis_fetch_data(
        fetch_data if isinstance(fetch_data, dict) else None,
        report_data=report_data or ctx.get("report_data"),
        tool_runtime_ctx=ctx,
    )

    # fetch_data 优先：空 list / 空 dict 视为未传，避免 LLM 截断空参盖掉真实数据
    if isinstance(fetch_data, dict):
        if not item_rows:
            item_rows = fetch_data.get("item_rows")
        if not knowledge_rows:
            knowledge_rows = fetch_data.get("knowledge_rows")
        if not score_result:
            score_result = fetch_data.get("score_result")
    items = list(item_rows or [])
    knowledge = enrich_knowledge_rows(list(knowledge_rows or []))

    # stats 未传但有 score_result/fetch_data 时，内部计算 KPI
    if (not stats or not stats.get("count")) and score_result:
        cfg = _get_effective_config()
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
        if values:
            stats = _compute_stats(values, cfg, fs_val)

    if (stats is None or not stats.get("count")) and isinstance(fetch_data, dict):
        cfg = _get_effective_config()
        stats_from_fetch = _stats_from_fetch_bundle(fetch_data, cfg)
        if stats_from_fetch:
            stats = stats_from_fetch

    cfg = _get_effective_config()

    score_rows: list[dict[str, Any]] = []
    if isinstance(fetch_data, dict):
        score_rows = list(fetch_data.get("score_rows") or [])
    if not score_rows and report_data:
        from src.agent.education.query_parse import resolve_diagnostic_score_rows

        score_rows = resolve_diagnostic_score_rows(
            score_rows=None,
            report_data=report_data,
            fetch_data=fetch_data if isinstance(fetch_data, dict) else None,
        )
    if not score_rows and score_result:
        rows_s, cols_s = _coerce_exec_result(score_result, None, None)
        if cols_s and rows_s:
            score_rows = [
                dict(zip(cols_s, row)) if isinstance(row, (list, tuple)) else dict(row)
                for row in rows_s
                if isinstance(row, (list, tuple, dict))
            ]

    intervention_insights: dict[str, Any] = {}
    intervention_html = ""
    class_compare_html = ""
    if school_name and score_rows and not class_name:
        intervention_insights = build_school_intervention_insights(
            score_rows=score_rows,
            stats=stats,
            knowledge_rows=knowledge,
            item_rows=items,
            config=cfg,
            weak_threshold=weak_threshold,
        )
        if intervention_insights.get("stats"):
            stats = intervention_insights["stats"]
        intervention_html = build_intervention_section_html(intervention_insights)
        if intervention_insights.get("class_compare"):
            class_compare_html = build_class_compare_table_html(
                intervention_insights["class_compare"],
                intervention_insights.get("weak_classes"),
                school_stats=stats,
            )

    summary_html = build_diagnosis_summary(
        school_name=school_name,
        exam_name=exam_name,
        subject_name=subject_name,
        stats=stats,
        item_rows=items,
        knowledge_rows=knowledge,
        score_rows=score_rows,
        weak_threshold=weak_threshold,
        intervention_insights=intervention_insights or None,
    )

    rec_html = build_diagnosis_recommendations(
        knowledge_rows=knowledge,
        item_rows=items,
        weak_threshold=weak_threshold,
        intervention_insights=intervention_insights or None,
        stats=stats,
    )

    data = {
        "WEAK_KNOWLEDGE_LIST": "、".join(
            str(r.get("knowledge_name") or "")
            for r in knowledge
            if r.get("level") == "需加强"
        )[:500],
        "SUMMARY": summary_html,
        "RECOMMENDATIONS": rec_html,
        "INTERVENTION_SECTION": intervention_html,
        "CLASS_COMPARE_TABLE": class_compare_html,
    }
    item_class_rows: list[dict[str, Any]] = []
    knowledge_class_rows: list[dict[str, Any]] = []
    if isinstance(fetch_data, dict):
        item_class_rows = list(fetch_data.get("item_class_rows") or [])
        knowledge_class_rows = list(fetch_data.get("knowledge_class_rows") or [])
    is_grade_compare = bool(school_name and not class_name)
    _apply_grade_compare_section_tables(
        data,
        items=items,
        knowledge=knowledge,
        item_class_rows=item_class_rows,
        knowledge_class_rows=knowledge_class_rows,
        is_grade_compare=is_grade_compare,
    )
    tier = build_ability_tier_summary(knowledge, weak_threshold=weak_threshold)
    tier_table = _build_ability_tier_table_html(knowledge)
    if tier_table:
        data["ABILITY_TIER_TABLE"] = tier_table
        levels = [s.get("ability_level") for s in tier.get("by_ability_level") or []]
        values = [float(s.get("avg_score_rate") or 0) for s in tier.get("by_ability_level") or []]
        if levels and values:
            data["ABILITY_TIER_CHART"] = _build_chart_option(
                "ability_radar",
                {
                    "levels": [ABILITY_LABELS.get(str(level), str(level)) for level in levels],
                    "values": values,
                },
                title="能力层级得分率",
            )
    weak_cnt = sum(1 for r in knowledge if r.get("level") == "需加强")

    # 班级横向对比报告不展示「每位学生详细档案与个性化建议」
    if is_grade_compare:
        data["STUDENT_ARCHIVE_TABLE"] = ""
    else:
        archive_html = _subject_diagnosis_student_archive(
            score_rows=score_rows,
            item_rows=items,
            exam_name=exam_name,
            subject_name=subject_name,
            school_name=school_name,
            class_name=class_name,
            full_score=(stats or {}).get("full_score") if isinstance(stats, dict) else None,
            weak_threshold=weak_threshold,
            datasource_id=datasource_id,
            workspace_oid=workspace_oid,
        )
        data["STUDENT_ARCHIVE_TABLE"] = archive_html

    if not render:
        content = (
            f"科目诊断区块已组装：小题 {len(items)} 条，知识点 {len(knowledge)} 个"
            f"（薄弱 {weak_cnt} 个）。请将返回 data 与 KPI 字段合并后填入 render_html_report。"
        )
        return ToolResult(content=content, data=data)

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    st = stats or {}
    segments = _normalize_segments(
        st.get("segments") or [],
        full_score=st.get("full_score"),
    )
    has_scores = bool(st.get("count"))
    scope_label = class_name or school_name or "全年级"
    rt = _resolve_subject_diagnosis_report_type(school_name=school_name, class_name=class_name)
    type_label = report_type_label(rt)
    if school_name and not class_name:
        report_title = f"{subject_name or '科目'}班级横向对比分析"
    else:
        report_title = f"{subject_name or '科目'}诊断报告"
    template_payload: dict[str, Any] = {
        "REPORT_TITLE": report_title,
        "REPORT_TYPE": type_label,
        "REPORT_SUBTITLE": _report_scope_subtitle(school_name, class_name) or scope_label,
        "REPORT_TIME": _now_str(),
        "SUBJECT_NAME": subject_name or "全科",
        "EXAM_NAME": exam_name or "本次考试",
        "SCOPE": scope_label,
        "AVG_SCORE": _fmt_val(st.get("avg")),
        "PASS_RATE": _fmt_val(st.get("pass_rate")),
        "EXCELLENT_RATE": _fmt_val(st.get("excellent_rate")),
        "STDEV": _fmt_val(st.get("stdev")),
        "VARIANCE": _fmt_val(st.get("variance")),
    }
    try:
        from src.agent.education.stats import describe_score_dispersion

        info = describe_score_dispersion(
            st.get("stdev"),
            full_score=st.get("full_score"),
            variance=st.get("variance"),
        )
        template_payload["STDEV_LEVEL"] = info["level"]
        template_payload["STDEV_LEVEL_CLASS"] = info["level_class"]
        template_payload["STDEV_HINT"] = info["stdev_hint"]
        template_payload["VARIANCE_HINT"] = info["variance_hint"]
        template_payload["DISPERSION_TIP"] = info["tip"]
        if template_payload.get("VARIANCE") in (None, "", "-") and info["variance"] != "-":
            template_payload["VARIANCE"] = _fmt_val(info["variance"])
    except Exception:
        pass
    template_payload.update(data)
    if not has_scores:
        template_payload["SUMMARY"] = (
            template_payload.get("SUMMARY", "")
            + "<p class='edu-sub' style='color:#ff4d4f'>⚠️ 成绩表（tb_score）未查到匹配记录，"
            "KPI 与分数段分布为空。</p>"
        )
    if segments and has_scores:
        template_payload["SCORE_DIST_CHART"] = _build_chart_option(
            "score_distribution",
            {
                "segments": [
                    {"label": s.get("label", ""), "count": s.get("count", 0)} for s in segments
                ],
                "pass_rate": st.get("pass_rate") or 0,
            },
            title="分数段分布",
        )
        template_payload["SEGMENT_TABLE"] = _segment_table_html(segments, full_score=st.get("full_score"))
    else:
        template_payload.setdefault("SCORE_DIST_CHART", "")
        template_payload.setdefault("SEGMENT_TABLE", "")

    template_name = "education/subject_diagnosis.html"
    title = str(template_payload.get("REPORT_TITLE") or "科目诊断报告")
    try:
        raw_html = _render_template_html(template_name, template_payload)
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
    return _html_report_tool_result(
        (
            f"科目诊断报告已渲染完成（小题 {len(items)} 条，知识点 {len(knowledge)} 个，"
            f"薄弱 {weak_cnt} 个、{score_note}、HTML {len(safe_html)} 字符）。\n"
            "报告已自动推送到前端。\n"
            "**禁止**再调 select_report_template / build_chart_option / render_html_report。"
        ),
        payload,
        report_type=rt,
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

    知识点名称由 ``tb_knowledge.knowledge_name`` 经 ``tb_exam_question_knowledge``
    LEFT JOIN 确定（掌握度按 weight 归一化拆分），**不会臆造**。

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
    score_rows = list(bundle.get("score_rows") or [])
    intervention_insights: dict[str, Any] = {}
    intervention_html = ""
    class_compare_html = ""
    if school_name and score_rows and not class_name:
        intervention_insights = build_school_intervention_insights(
            score_rows=score_rows,
            stats=stats,
            knowledge_rows=knowledge_enriched,
            item_rows=item_rows,
            config=cfg,
            weak_threshold=weak_threshold,
        )
        if intervention_insights.get("stats"):
            stats = intervention_insights["stats"]
        intervention_html = build_intervention_section_html(intervention_insights)
        if intervention_insights.get("class_compare"):
            class_compare_html = build_class_compare_table_html(
                intervention_insights["class_compare"],
                intervention_insights.get("weak_classes"),
                school_stats=stats,
            )

    summary_html = build_diagnosis_summary(
        school_name=school_name,
        exam_name=exam_name,
        subject_name=subject_name,
        stats=stats,
        item_rows=item_rows,
        knowledge_rows=knowledge_enriched,
        score_rows=score_rows,
        weak_threshold=weak_threshold,
        intervention_insights=intervention_insights or None,
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

    rec_html = build_diagnosis_recommendations(
        knowledge_rows=knowledge_enriched,
        item_rows=item_rows,
        weak_threshold=weak_threshold,
        intervention_insights=intervention_insights or None,
        stats=stats,
    )

    section_data: dict[str, Any] = {
        "WEAK_KNOWLEDGE_LIST": "、".join(
            str(r.get("knowledge_name") or "")
            for r in knowledge_enriched
            if r.get("level") == "需加强"
        )[:500],
        "SUMMARY": summary_html,
        "RECOMMENDATIONS": rec_html,
        "INTERVENTION_SECTION": intervention_html,
        "CLASS_COMPARE_TABLE": class_compare_html,
    }
    is_grade_compare = bool(school_name and not class_name)
    _apply_grade_compare_section_tables(
        section_data,
        items=item_rows,
        knowledge=knowledge_enriched,
        item_class_rows=list(bundle.get("item_class_rows") or []),
        knowledge_class_rows=list(bundle.get("knowledge_class_rows") or []),
        is_grade_compare=is_grade_compare,
    )

    segments = _normalize_segments(
        stats.get("segments") or [],
        full_score=stats.get("full_score"),
    )
    section_data["SCORE_DIST_CHART"] = _build_chart_option(
        "score_distribution",
        {
            "segments": [{"label": s.get("label", ""), "count": s.get("count", 0)} for s in segments],
            "pass_rate": stats.get("pass_rate") or 0,
        },
        title="分数段分布",
    )
    section_data["SEGMENT_TABLE"] = _segment_table_html(segments, full_score=stats.get("full_score"))

    # ---------- 4. 填充模板字段 ----------
    scope_label = class_name or school_name or "全年级"
    rt = _resolve_subject_diagnosis_report_type(school_name=school_name, class_name=class_name)
    type_label = report_type_label(rt)
    if school_name and not class_name:
        report_title = f"{subject_name or '科目'}班级横向对比分析"
        subtitle = school_name
    else:
        report_title = f"{subject_name or '科目'}诊断报告"
        subtitle = f"{school_name} {class_name}".strip()
    report_data: dict[str, Any] = {
        "REPORT_TITLE": report_title,
        "REPORT_TYPE": type_label,
        "REPORT_SUBTITLE": subtitle,
        "REPORT_TIME": _now_str(),
        "SUBJECT_NAME": subject_name or "全科",
        "EXAM_NAME": exam_name or "本次考试",
        "SCOPE": scope_label,
        "AVG_SCORE": _fmt_val(stats.get("avg")),
        "PASS_RATE": _fmt_val(stats.get("pass_rate")),
        "EXCELLENT_RATE": _fmt_val(stats.get("excellent_rate")),
        "STDEV": _fmt_val(stats.get("stdev")),
        "VARIANCE": _fmt_val(stats.get("variance")),
    }
    try:
        from src.agent.education.stats import describe_score_dispersion

        info = describe_score_dispersion(
            stats.get("stdev") if isinstance(stats, dict) else None,
            full_score=stats.get("full_score") if isinstance(stats, dict) else None,
            variance=stats.get("variance") if isinstance(stats, dict) else None,
        )
        report_data["STDEV_LEVEL"] = info["level"]
        report_data["STDEV_LEVEL_CLASS"] = info["level_class"]
        report_data["STDEV_HINT"] = info["stdev_hint"]
        report_data["VARIANCE_HINT"] = info["variance_hint"]
        report_data["DISPERSION_TIP"] = info["tip"]
        if report_data.get("VARIANCE") in (None, "", "-") and info["variance"] != "-":
            report_data["VARIANCE"] = _fmt_val(info["variance"])
    except Exception:
        pass
    report_data.update(section_data)

    if is_grade_compare:
        report_data["STUDENT_ARCHIVE_TABLE"] = ""
    else:
        report_data["STUDENT_ARCHIVE_TABLE"] = _subject_diagnosis_student_archive(
            score_rows=score_rows,
            item_rows=item_rows,
            exam_name=exam_name,
            subject_name=subject_name,
            school_name=school_name,
            class_name=class_name,
            full_score=stats.get("full_score") if isinstance(stats, dict) else None,
            weak_threshold=weak_threshold,
            datasource_id=datasource_id,
            workspace_oid=workspace_oid,
        )

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
    return _html_report_tool_result(
        (
            f"科目诊断报告已渲染完成（{len(item_rows)} 题、{len(knowledge_enriched)} 知识点、"
            f"薄弱 {weak_cnt} 个、{score_note}、HTML {len(safe_html)} 字符）。\n"
            f"小题查询 SQL 记录：\n{sql_note}\n"
            "报告已自动推送到前端，直接调 terminate 结束即可。"
        ),
        payload,
        report_type=rt,
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


_SCHOOL_FULL_SUFFIXES = ("中学", "学校", "学院", "大学", "附中", "分校")
#: school_id 形态（如 YZZX、SCH001），不可当作 sch.name 精确匹配。
_SCHOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _is_unsafe_school_name_filter(school_name: str) -> bool:
    """学校简称（如「南京一中」）与 sch.name 全称不一致时，精确 WHERE 会查不到数据。"""
    n = (school_name or "").strip()
    if not n:
        return False
    return not any(n.endswith(s) for s in _SCHOOL_FULL_SUFFIXES)


def _looks_like_school_id(value: str) -> bool:
    """判断是否为学校编号（school_id），而非中文校名。"""
    n = (value or "").strip()
    return bool(n and _SCHOOL_ID_RE.fullmatch(n))


def _school_scope_predicate(school_name: str) -> str | None:
    """生成学校范围 WHERE 片段。

    - 规范中文全称 → ``sch.name = ...``
    - school_id 形态（如 ``YZZX``）→ ``sc.school_id = ...``
    - 中文简称等不安全值 → 仍尝试 ``sch.name``（由上层按需重试放宽）
    """
    n = (school_name or "").strip()
    if not n:
        return None
    if _looks_like_school_id(n):
        return f"sc.school_id = '{_esc(n)}'"
    return f"sch.name = '{_esc(n)}'"


def _keep_school_filter_arg(school_name: str) -> str:
    """决定是否把 school_name 传给 WHERE 构造。

    school_id 代码应保留；中文简称交给「查不到再放宽」路径，部分调用方会预先丢弃。
    """
    n = (school_name or "").strip()
    if not n:
        return ""
    if _looks_like_school_id(n):
        return n
    if _is_unsafe_school_name_filter(n):
        return ""
    return n


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
    student_id: str = "",
    exam_ids: list[str] | None = None,
    skip_exam_name: bool = False,
    exam_id_expr: str = "sc.exam_id",
) -> list[str]:
    parts: list[str] = []
    if student_id:
        sid = _esc(student_id.strip())
        # 精确 + 模糊：兼容完整学号与短号
        parts.append(f"(sc.student_id = '{sid}' OR sc.student_id LIKE '%{sid}%')")
    school_pred = _school_scope_predicate(school_name)
    if school_pred:
        parts.append(school_pred)
    if class_name:
        from src.agent.education.query_parse import normalize_fullwidth_parentheses

        cls = normalize_fullwidth_parentheses(class_name.strip())
        parts.append(f"sc.class = '{_esc(cls)}'")
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
    *,
    student_id: str = "",
) -> tuple[str, str, str, str]:
    """返回 (item_sql, knowledge_sql, score_sql, exam_id_sql)。"""
    full_score_expr = "COALESCE(eq.question_score, sd.question_score)"
    kn_join = knowledge_names_subquery_join(db_type)
    w_join = knowledge_weighted_join()
    w_score = f"sd.score * COALESCE(eqk.w_norm, 1)"
    w_full = f"{full_score_expr} * COALESCE(eqk.w_norm, 1)"
    if student_id:
        item_rate = _score_rate_sql("sd.score", full_score_expr, db_type)
        item_sql = (
            "SELECT sd.question_no,\n"
            "       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,\n"
            "       eq.question_type AS question_type,\n"
            f"       {full_score_expr} AS full_score,\n"
            "       sd.score AS avg_score,\n"
            f"       {item_rate} AS score_rate\n"
            "FROM tb_score_detail sd\n"
            "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
            "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
            f"{kn_join}"
            "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
            "JOIN tb_school sch ON sc.school_id = sch.id\n"
            "JOIN tb_exam e ON sc.exam_id = e.id"
            + where_clause_detail
            + "\nORDER BY sd.question_no\nLIMIT 1000"
        )
    else:
        item_rate = _score_rate_sql("AVG(sd.score)", full_score_expr, db_type)
        item_sql = (
            "SELECT sd.question_no,\n"
            "       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,\n"
            "       eq.question_type AS question_type,\n"
            f"       {full_score_expr} AS full_score,\n"
            "       ROUND(AVG(sd.score), 2) AS avg_score,\n"
            f"       {item_rate} AS score_rate\n"
            "FROM tb_score_detail sd\n"
            "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
            "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
            f"{kn_join}"
            "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
            "JOIN tb_school sch ON sc.school_id = sch.id\n"
            "JOIN tb_exam e ON sc.exam_id = e.id"
            + where_clause_detail
            + f"\nGROUP BY sd.question_no, COALESCE(kn.knowledge_name, '未关联知识点'), "
            f"eq.question_type, {full_score_expr}\n"
            "ORDER BY sd.question_no\nLIMIT 1000"
        )
    know_rate = _score_rate_sql(f"SUM({w_score})", f"SUM({w_full})", db_type)
    knowledge_sql = (
        "SELECT COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        "       k.ability_level AS ability_level,\n"
        "       COUNT(DISTINCT sd.question_no) AS question_count,\n"
        f"       {know_rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        f"{w_join}"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_detail
        + "\nGROUP BY COALESCE(k.knowledge_name, '未关联知识点'), k.ability_level\n"
        "ORDER BY score_rate ASC\nLIMIT 1000"
    )
    score_sql = (
        "SELECT sc.score AS score, sc.exam_score AS exam_score, sc.exam_id AS exam_id,\n"
        "       sc.class AS class, sc.class AS class_name, sc.student_id AS student_id,\n"
        "       sc.subject_name AS subject, sch.name AS school_name,\n"
        "       sch.district AS district\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_score
        + "\nLIMIT 5000"
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


def _diagnosis_class_compare_sql(
    where_clause_detail: str,
    db_type: str = "pg",
) -> tuple[str, str]:
    """各班小题均分 / 知识点得分率 SQL（用于班级横向对比）。"""
    full_score_expr = "COALESCE(eq.question_score, sd.question_score)"
    kn_join = knowledge_names_subquery_join(db_type)
    w_join = knowledge_weighted_join()
    w_score = f"sd.score * COALESCE(eqk.w_norm, 1)"
    w_full = f"{full_score_expr} * COALESCE(eqk.w_norm, 1)"
    item_rate = _score_rate_sql("AVG(sd.score)", full_score_expr, db_type)
    item_sql = (
        "SELECT sc.class AS class_name,\n"
        "       sd.question_no,\n"
        "       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        "       eq.question_type AS question_type,\n"
        f"       {full_score_expr} AS full_score,\n"
        "       ROUND(AVG(sd.score), 2) AS avg_score,\n"
        f"       {item_rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        f"{kn_join}"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_detail
        + f"\nGROUP BY sc.class, sd.question_no, COALESCE(kn.knowledge_name, '未关联知识点'), "
        f"eq.question_type, {full_score_expr}\n"
        "ORDER BY sd.question_no, sc.class\nLIMIT 10000"
    )
    know_rate = _score_rate_sql(f"SUM({w_score})", f"SUM({w_full})", db_type)
    knowledge_sql = (
        "SELECT sc.class AS class_name,\n"
        "       COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        "       COUNT(DISTINCT sd.question_no) AS question_count,\n"
        f"       {know_rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        f"{w_join}"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where_clause_detail
        + "\nGROUP BY sc.class, COALESCE(k.knowledge_name, '未关联知识点')\n"
        "ORDER BY knowledge_name, sc.class\nLIMIT 10000"
    )
    return item_sql, knowledge_sql


def _rows_to_dicts(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    cols = result.get("columns") or []
    raw_rows = result.get("rows") or []
    return [dict(zip(cols, row)) for row in raw_rows]


def _parse_score_result(result: dict[str, Any] | None) -> tuple[list[float], float | None, list[str], list[dict[str, Any]]]:
    score_values: list[float] = []
    full_score: float | None = None
    exam_ids: list[str] = []
    score_rows: list[dict[str, Any]] = []
    if not isinstance(result, dict):
        return score_values, full_score, exam_ids, score_rows
    score_rows = _rows_to_dicts(result)
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
    return score_values, full_score, exam_ids, score_rows


def _fetch_subject_diagnosis_rows(
    *,
    datasource_id: int,
    workspace_oid: int | None,
    user_id: int | None,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    class_name: str = "",
    student_id: str = "",
    db_type: str = "pg",
) -> dict[str, Any]:
    """查小题/知识点/成绩，带考试名放宽与 exam_id 回退。

    全校无班级（横向对比）时额外返回 ``item_class_rows`` / ``knowledge_class_rows``。
    """
    base_kw = {
        "school_name": school_name,
        "class_name": class_name,
        "subject_name": subject_name,
        "exam_name": exam_name,
        "student_id": student_id,
    }
    item_rows: list[dict[str, Any]] = []
    knowledge_rows: list[dict[str, Any]] = []
    score_values: list[float] = []
    score_rows: list[dict[str, Any]] = []
    full_score: float | None = None
    warnings: list[str] = []
    errors: list[str] = []
    sql_logs: list[dict[str, Any]] = []
    last_detail_wc = ""
    want_class_compare = bool(school_name and not class_name and not student_id)

    def run_bundle(
        where_clause_detail: str,
        where_clause_score: str,
        phase: str,
    ) -> tuple[list[dict], list[dict], list[float], float | None, list[str], list[dict[str, Any]]]:
        nonlocal last_detail_wc
        last_detail_wc = where_clause_detail
        item_sql, knowledge_sql, score_sql, exam_id_sql = _diagnosis_sql_bundle(
            where_clause_detail,
            where_clause_score,
            db_type,
            student_id=student_id,
        )
        ir: list[dict[str, Any]] = []
        kr: list[dict[str, Any]] = []
        sv: list[float] = []
        fs: float | None = None
        eids: list[str] = []
        srows: list[dict[str, Any]] = []
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
                sv, fs, eids, srows = _parse_score_result(result)
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
        return ir, kr, sv, fs, eids, srows

    detail_wc, score_wc = _diagnosis_where_clause_pair(**base_kw)
    item_rows, knowledge_rows, score_values, full_score, exam_ids, score_rows = run_bundle(
        detail_wc, score_wc, "primary"
    )

    # 学号查询 + 中文校名简称：sch.name 精确匹配失败时，去掉学校过滤重试
    # （school_id 形态已在 WHERE 中按 sc.school_id 过滤，无需此放宽）
    if (
        student_id
        and not item_rows
        and not score_values
        and _is_unsafe_school_name_filter(school_name)
        and not _looks_like_school_id(school_name)
    ):
        relaxed_kw = {**base_kw, "school_name": ""}
        detail_wc_s, score_wc_s = _diagnosis_where_clause_pair(**relaxed_kw)
        item_rows, knowledge_rows, score_values, full_score, exam_ids, score_rows = run_bundle(
            detail_wc_s, score_wc_s, "fallback_student_no_school"
        )
        if item_rows or score_values:
            warnings.append(
                f"学校名 `{school_name}` 与库中 sch.name 不完全一致，已按学号 {student_id} 重试（"
                f"命中小题 {len(item_rows)}、成绩 {len(score_values)} 条）"
            )

    if not item_rows and score_values and exam_ids:
        detail_wc2, score_wc2 = _diagnosis_where_clause_pair(
            **base_kw, exam_ids=exam_ids, skip_exam_name=True
        )
        item_rows, knowledge_rows, _sv2, _fs2, _, score_rows = run_bundle(
            detail_wc2, score_wc2, "fallback_exam_id"
        )
        if item_rows:
            warnings.append("小题明细已按成绩记录的 exam_id 回退查询（考试名与库中不完全一致）")

    if not item_rows and exam_name:
        detail_wc3, score_wc3 = _diagnosis_where_clause_pair(**base_kw, skip_exam_name=True)
        item_rows, knowledge_rows, score_values, full_score, exam_ids, score_rows = run_bundle(
            detail_wc3, score_wc3, "fallback_no_exam_name"
        )
        if not item_rows and score_values and exam_ids:
            detail_wc4, score_wc4 = _diagnosis_where_clause_pair(
                **base_kw, exam_ids=exam_ids, skip_exam_name=True
            )
            item_rows, knowledge_rows, _, _, _, score_rows = run_bundle(
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
        _, _, sv_r, fs_r, _, score_rows = run_bundle(detail_wc_r, score_wc_r, "relaxed_score_no_class")
        if sv_r:
            score_values = sv_r
            full_score = fs_r if fs_r is not None else full_score
            warnings.append(
                f"成绩记录按班级 `{class_name}` 未查到，已放宽班级过滤重试"
                f"（命中 {len(sv_r)} 条）。建议核查 tb_score.class 实际值。"
            )

    # 考试名 LIKE 可能命中多场：班级横向对比/单场诊断收敛至参与人数最多的一场
    if (
        (exam_name or want_class_compare)
        and not student_id
        and score_rows
    ):
        from src.agent.education.aggregation import pick_primary_exam_id

        exam_keys = {
            str(r.get("exam_id") or "").strip()
            for r in score_rows
            if isinstance(r, dict) and str(r.get("exam_id") or "").strip()
        }
        if len(exam_keys) > 1:
            primary = pick_primary_exam_id(score_rows)
            if primary:
                detail_wc_n, score_wc_n = _diagnosis_where_clause_pair(
                    **base_kw, exam_ids=[primary], skip_exam_name=True
                )
                item_rows, knowledge_rows, score_values, full_score, exam_ids, score_rows = (
                    run_bundle(detail_wc_n, score_wc_n, "narrow_primary_exam")
                )
                warnings.append(
                    f"考试名匹配到多场（{len(exam_keys)}），"
                    f"已收敛至参与人数最多的 exam_id={primary}"
                )

    item_class_rows: list[dict[str, Any]] = []
    knowledge_class_rows: list[dict[str, Any]] = []
    if want_class_compare and last_detail_wc and (item_rows or knowledge_rows):
        item_c_sql, know_c_sql = _diagnosis_class_compare_sql(last_detail_wc, db_type)
        for label, sql in (
            ("item_by_class", item_c_sql),
            ("knowledge_by_class", know_c_sql),
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
                    "phase": "class_compare",
                    "label": label,
                    "success": success,
                    "row_count": row_count,
                    "message": msg if not success else "",
                    "sql_preview": (sql_run or sql)[:600],
                }
            )
            if not success:
                errors.append(f"[class_compare/{label}] {msg}")
                continue
            if not isinstance(result, dict):
                continue
            if label == "item_by_class":
                item_class_rows = _rows_to_dicts(result)
            else:
                knowledge_class_rows = _rows_to_dicts(result)
        if item_class_rows or knowledge_class_rows:
            warnings.append(
                f"已加载班级横向对比明细：小题 {len(item_class_rows)} 行、"
                f"知识点 {len(knowledge_class_rows)} 行"
            )

    return {
        "item_rows": item_rows,
        "knowledge_rows": knowledge_rows,
        "item_class_rows": item_class_rows,
        "knowledge_class_rows": knowledge_class_rows,
        "score_values": score_values,
        "score_rows": score_rows,
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


def _coerce_numeric_score(v: Any) -> float | None:
    """将得分字段转为数值；拒绝学号等非分数字符串。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).strip()
    if not text or text in {"-", "—", "None", "null"}:
        return None
    # 学号形如 2024_STU... / 含字母下划线的长串，绝不当得分
    if re.search(r"[A-Za-z_]", text) and not re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
        return None
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _score_column_index(col_l: list[str]) -> int | None:
    """定位成绩列：优先 score/得分，绝不回退到第 0 列（常为学号）。"""
    preferred = ("score", "得分", "总分", "分数", "成绩", "total_score", "exam_total")
    for name in preferred:
        for i, c in enumerate(col_l):
            if c == name:
                return i
    avoid = {"exam_score", "满分", "full_score", "student_id", "id", "学号"}
    for i, c in enumerate(col_l):
        if c in avoid or "rank" in c or "率" in c:
            continue
        if "score" in c or (c.endswith("分") and "排" not in c and "满" not in c):
            return i
    return None


def _parse_rank_value(value: Any) -> int | None:
    """解析排名：17 / 17.0 / 「第17名」/ 「17名」→ int。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        iv = int(value)
        return iv if iv > 0 and abs(value - iv) < 1e-6 else None
    text = str(value).strip()
    if not text or text in {"-", "—", "None", "null"}:
        return None
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    try:
        iv = int(m.group(1))
    except ValueError:
        return None
    return iv if iv > 0 else None


def _pick_score_from_score_rows(
    score_rows: list[dict[str, Any]],
    student_id: str,
) -> tuple[float | None, Any]:
    """从 score_rows 取该生数值得分，并在班级样本足够时给出班内名次。

    仅有本人 1 行时**不算排名**（会错误得到第 1 名），返回 rank=None。
    """
    from src.agent.education.query_parse import student_matches

    sid = (student_id or "").strip()
    scored: list[tuple[float, bool]] = []
    own: float | None = None
    peer_ids: set[str] = set()
    for r in score_rows:
        if not isinstance(r, dict):
            continue
        val = _coerce_numeric_score(r.get("score"))
        if val is None:
            continue
        rid = str(r.get("student_id") or r.get("id") or "").strip()
        is_self = student_matches(rid, sid) if rid else False
        scored.append((val, is_self))
        if rid:
            peer_ids.add(rid)
        if is_self and own is None:
            own = val
    if own is None and len(scored) == 1:
        own = scored[0][0]
    if own is None:
        return None, None
    # 样本不足：无法可靠算班排（常见于按学号过滤后只剩 1 行）
    distinct_n = len(peer_ids) if peer_ids else len(scored)
    if distinct_n < 2:
        return own, None
    # 同分并列：高于本人的人数 + 1
    class_rank = 1 + sum(1 for s, _ in scored if s > own)
    return own, class_rank


def _compute_class_rank_from_rows(
    rows: list[Any],
    cols: list[str],
    student_id: str,
    *,
    score_i: int | None,
    sid_i: int | None,
) -> tuple[float | None, int | None]:
    """从一张含全班得分的表计算本人得分与班级排名。"""
    from src.agent.education.query_parse import student_matches

    if score_i is None or not rows:
        return None, None
    sid = (student_id or "").strip()
    scored: list[tuple[float, bool]] = []
    own: float | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        try:
            raw = row[score_i]
        except IndexError:
            continue
        val = _coerce_numeric_score(raw)
        if val is None:
            continue
        is_self = False
        if sid_i is not None and sid_i < len(row):
            is_self = student_matches(str(row[sid_i] or "").strip(), sid)
        scored.append((val, is_self))
        if is_self and own is None:
            own = val
    if own is None:
        return None, None
    if len(scored) < 2:
        return own, None
    return own, 1 + sum(1 for s, _ in scored if s > own)


def _report_scope_subtitle(school_name: str = "", class_name: str = "") -> str:
    """报告副标题，过滤 None/空班级。"""
    parts: list[str] = []
    for raw in (school_name, class_name):
        text = str(raw or "").strip()
        if text and text.lower() != "none":
            parts.append(text)
    return " ".join(parts)


def _html_report_tool_result(
    content: str,
    payload: dict[str, Any],
    *,
    report_type: ReportType | str | None = None,
) -> ToolResult:
    """HTML 报告工具成功返回：附带报告类型，并标记 is_final。"""
    from src.agent.education.report_types import report_type_label

    data = dict(payload)
    if report_type is not None:
        rt = report_type if isinstance(report_type, ReportType) else _coerce_report_type(str(report_type))
        if rt is not None:
            from src.agent.education.report_types import format_report_display_title

            label = report_type_label(rt)
            data["report_type"] = rt.value
            data["report_type_label"] = label
            data["title"] = format_report_display_title(
                str(data.get("title") or "").strip(),
                rt,
                type_label=label,
            )
            # 同步 chunks 标题
            chunks = data.get("chunks")
            if isinstance(chunks, list) and chunks and isinstance(chunks[0], dict):
                chunks = [dict(chunks[0])]
                chunks[0]["title"] = data["title"]
                data["chunks"] = chunks
    return ToolResult(
        content=content + "\n任务已完成，无需再调用其他工具。",
        data=data,
        is_final=True,
    )


def _resolve_subject_diagnosis_report_type(*, school_name: str = "", class_name: str = "") -> ReportType:
    """全校无班级 → 班级横向对比；否则科目诊断。"""
    if (school_name or "").strip() and not (class_name or "").strip():
        return ReportType.GRADE_COMPARISON
    return ReportType.SUBJECT_DIAGNOSIS


def _apply_grade_compare_section_tables(
    data: dict[str, Any],
    *,
    items: list[dict[str, Any]],
    knowledge: list[dict[str, Any]],
    item_class_rows: list[dict[str, Any]] | None = None,
    knowledge_class_rows: list[dict[str, Any]] | None = None,
    is_grade_compare: bool = False,
) -> None:
    """班级横向对比：小题/知识点/题型改为各班对比表与分组柱图。"""
    item_cmp = list(item_class_rows or [])
    know_cmp = list(knowledge_class_rows or [])
    use_item_cmp = is_grade_compare and len(collect_class_names(item_cmp)) >= 2
    # 知识点对比只信加权 SQL（know_c_sql）；不可由小题行反推（无法还原 weight）
    use_know_cmp = is_grade_compare and len(collect_class_names(know_cmp)) >= 2

    if use_item_cmp:
        data["ITEM_TABLE"] = build_item_table_html(item_cmp)
    else:
        data["ITEM_TABLE"] = build_item_table_html(items)

    if use_know_cmp:
        data["KNOWLEDGE_TABLE"] = build_knowledge_table_html(know_cmp)
        chart_payload = build_knowledge_compare_chart_payload(know_cmp)
        if chart_payload:
            data["KNOWLEDGE_CHART"] = _build_chart_option(
                "group_compare_bar",
                chart_payload,
                title="各班知识点得分率对比",
            )
        else:
            data["KNOWLEDGE_CHART"] = ""
    else:
        data["KNOWLEDGE_TABLE"] = build_knowledge_table_html(knowledge)
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

    qtype_source = item_cmp if use_item_cmp else _compute_item_metrics(items)
    qtype_table = _build_question_type_table_html(qtype_source)
    if qtype_table:
        data["QUESTION_TYPE_TABLE"] = qtype_table
        if use_item_cmp:
            qchart = _build_question_type_compare_chart_payload(item_cmp)
            if qchart:
                data["QUESTION_TYPE_CHART"] = _build_chart_option(
                    "group_compare_bar",
                    qchart,
                    title="各班题型得分率对比",
                )
        else:
            from collections import defaultdict

            buckets: dict[str, list[float]] = defaultdict(list)
            for ir in qtype_source:
                if ir.get("question_type") and ir.get("score_rate") is not None:
                    buckets[str(ir["question_type"])].append(float(ir["score_rate"]))
            if buckets:
                cats = sorted(buckets.keys())
                vals = [round(sum(buckets[c]) / len(buckets[c]), 2) for c in cats]
                data["QUESTION_TYPE_CHART"] = _build_chart_option(
                    "question_type_bar",
                    {"categories": cats, "values": vals},
                    title="题型得分率",
                )


def _subject_diagnosis_student_archive(
    *,
    score_rows: list[dict[str, Any]] | None,
    item_rows: list[dict[str, Any]] | None = None,
    exam_name: str = "",
    subject_name: str = "",
    school_name: str = "",
    class_name: str = "",
    full_score: float | None = None,
    weak_threshold: float = 60.0,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> str:
    """科目诊断：单科档案（无优势/待提升/偏科度），建议按逐人知识点得分生成。"""
    from src.agent.education.comprehensive import (
        aggregate_student_item_insights,
        build_student_archive_from_score_rows,
    )

    enriched: list[dict[str, Any]] = []
    for r in score_rows or []:
        if not isinstance(r, dict):
            continue
        rr = dict(r)
        if subject_name and not str(rr.get("subject") or rr.get("subject_name") or "").strip():
            rr["subject_name"] = subject_name
        if exam_name and not str(rr.get("exam") or rr.get("exam_name") or "").strip():
            rr["exam_name"] = exam_name
        enriched.append(rr)

    detail_rows = list(item_rows or [])
    has_student_detail = any(
        isinstance(r, dict)
        and str(r.get("student_id") or r.get("student") or "").strip()
        for r in detail_rows
    )
    # 班级科目诊断的 fetch item_rows 多为按题聚合（无学号）——需另查逐人小题明细
    if not has_student_detail and datasource_id:
        try:
            fetched = _fetch_class_student_item_rows(
                datasource_id=int(datasource_id),
                class_name=class_name or "",
                exam_name=exam_name or "",
                subject_name=subject_name or "",
                school_name=school_name or "",
                workspace_oid=workspace_oid,
            )
            if fetched:
                detail_rows = fetched
                has_student_detail = True
        except Exception:  # noqa: BLE001
            logger.exception("subject diagnosis: fetch per-student item rows failed")

    insights: dict[str, dict[str, Any]] | None = None
    if has_student_detail and detail_rows:
        raw = aggregate_student_item_insights(
            detail_rows,
            weak_threshold=weak_threshold,
            exam_name=exam_name,
        )
        if raw:
            insights = raw

    fs = full_score
    if fs is None:
        for r in enriched:
            try:
                v = r.get("exam_score")
                if v is not None and v != "":
                    fs = float(v)
                    break
            except (TypeError, ValueError):
                continue

    return build_student_archive_from_score_rows(
        enriched,
        exam_name=exam_name or "本次考试",
        full_score=fs,
        student_item_insights=insights,
        single_subject=True,
    )


def _stats_from_fetch_bundle(
    fetch_data: dict[str, Any] | None,
    cfg: EducationConfig,
) -> dict[str, Any] | None:
    """从 fetch 返回的 score_rows / score_result 计算 KPI。"""
    if not isinstance(fetch_data, dict):
        return None
    from src.agent.education.aggregation import prepare_score_rows_for_kpi

    score_rows = prepare_score_rows_for_kpi(fetch_data.get("score_rows") or [])
    values: list[float] = []
    fs_val: float | None = None
    if score_rows:
        for r in score_rows:
            if not isinstance(r, dict):
                continue
            if r.get("score") is not None:
                try:
                    values.append(float(r["score"]))
                except (TypeError, ValueError):
                    pass
            if fs_val is None and r.get("exam_score") is not None:
                try:
                    fs_val = float(r["exam_score"])
                except (TypeError, ValueError):
                    pass
    if not values:
        sr = fetch_data.get("score_result")
        if isinstance(sr, dict):
            cols = sr.get("columns") or []
            raw = sr.get("rows") or []
            if cols and raw:
                si = cols.index("score") if "score" in cols else 0
                fs_i = cols.index("exam_score") if "exam_score" in cols else -1
                for row in raw:
                    try:
                        v = row[si] if isinstance(row, (list, tuple)) else row.get("score")
                        if v is not None and v != "":
                            values.append(float(v))
                    except (TypeError, ValueError, IndexError):
                        continue
                    if fs_val is None and fs_i >= 0:
                        try:
                            fv = row[fs_i] if isinstance(row, (list, tuple)) else row.get("exam_score")
                            if fv is not None:
                                fs_val = float(fv)
                        except (TypeError, ValueError, IndexError):
                            pass
    if not values:
        return None
    return _compute_stats(values, cfg, fs_val)


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _segment_table_html(segments: list[dict[str, Any]], *, full_score: float | None = None) -> str:
    return build_segment_table_html(segments, full_score=full_score)


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


def _tier_alert_table_html(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p>暂无</p>"
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows
    )
    return (
        f'<div class="edu-table-wrap"><table class="edu-table">'
        f"<thead>{head}</thead><tbody>{body}</tbody></table></div>"
    )


def _score_rows_to_at_risk_students(
    score_rows: list[dict[str, Any]],
    *,
    default_subject: str = "",
) -> list[dict[str, Any]]:
    students: list[dict[str, Any]] = []
    for r in score_rows:
        if not isinstance(r, dict) or r.get("score") is None:
            continue
        name = str(
            r.get("name") or r.get("student_name") or r.get("student_id") or ""
        ).strip()
        if not name:
            continue
        try:
            score_val = float(r["score"])
        except (TypeError, ValueError):
            continue
        item: dict[str, Any] = {
            "name": name,
            "student_id": str(r.get("student_id") or name).strip(),
            "subject": str(
                r.get("subject") or r.get("subject_name") or default_subject or ""
            ),
            "score": score_val,
        }
        school_id = str(r.get("school_id") or r.get("school") or "").strip()
        if school_id:
            item["school_id"] = school_id
        exam_id = str(r.get("exam_id") or "").strip()
        if exam_id:
            item["exam_id"] = exam_id
        class_name = str(r.get("class") or r.get("class_name") or "").strip()
        if class_name:
            item["class"] = class_name
        prev = r.get("prev_score")
        if prev is not None and prev != "":
            try:
                item["prev_score"] = float(prev)
            except (TypeError, ValueError):
                pass
        students.append(item)
    return students


def _build_tier_alert_template_data(
    at_risk: dict[str, list[dict[str, Any]]],
    *,
    class_name: str = "",
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    pass_line: float | None = None,
) -> dict[str, Any]:
    critical = list(at_risk.get("critical") or [])
    regression = list(at_risk.get("regression") or [])
    imbalanced = list(at_risk.get("imbalanced") or [])

    crit_rows = [
        [
            str(s.get("name") or ""),
            str(s.get("subject") or subject_name or ""),
            f"{float(s['score']):.1f}" if s.get("score") is not None else "",
            str(s.get("reason") or ""),
        ]
        for s in critical
    ]
    reg_rows = [
        [
            str(s.get("name") or ""),
            str(s.get("subject") or subject_name or ""),
            f"{float(s['score']):.1f}" if s.get("score") is not None else "",
            (
                f"{float(s['prev_score']):.1f}"
                if s.get("prev_score") is not None
                else ""
            ),
            str(s.get("reason") or ""),
        ]
        for s in regression
    ]
    imb_rows = [
        [
            str(s.get("name") or ""),
            str(s.get("low_subject") or ""),
            f"{float(s['low_score']):.1f}" if s.get("low_score") is not None else "",
            str(s.get("high_subject") or ""),
            f"{float(s['high_score']):.1f}" if s.get("high_score") is not None else "",
            str(s.get("reason") or ""),
        ]
        for s in imbalanced
    ]

    scope = " · ".join(p for p in (school_name, class_name) if p) or class_name or school_name or "本班"
    title_bits = [p for p in (school_name, class_name, subject_name) if p]
    title = f"{' · '.join(title_bits)}分层预警报告" if title_bits else "分层预警报告"
    pass_hint = f"{pass_line:.0f} 分" if pass_line is not None else "及格线"

    summary = (
        f"<p>共识别临界生 <strong>{len(critical)}</strong> 人、"
        f"大幅退步 <strong>{len(regression)}</strong> 人、"
        f"偏科 <strong>{len(imbalanced)}</strong> 人"
        f"（临界区间参考 {pass_hint} ± 临界幅度）。</p>"
    )
    recs: list[str] = []
    if critical:
        recs.append("<li>临界生：针对近及格线知识点开展巩固练习，重点盯周测过关。</li>")
    if regression:
        recs.append("<li>大幅退步：一对一面谈溯因（知识点断层/心态/缺考），制定追赶计划。</li>")
    if imbalanced:
        recs.append("<li>偏科生：补短板科目课时，优势科保持节奏避免回落。</li>")
    if not recs:
        recs.append("<li>本班暂无明显预警对象，维持常规学情跟踪即可。</li>")
    recommendations = "<ul>" + "".join(recs) + "</ul>"

    tier_type_chart = ""
    critical_dist_chart = ""
    total_alert = len(critical) + len(regression) + len(imbalanced)
    if total_alert > 0:
        try:
            tier_type_chart = _build_chart_option(
                "pie",
                {
                    "items": [
                        {
                            "name": "临界生",
                            "value": len(critical),
                            "color": "#faad14",
                        },
                        {
                            "name": "大幅退步",
                            "value": len(regression),
                            "color": "#ff4d4f",
                        },
                        {
                            "name": "偏科生",
                            "value": len(imbalanced),
                            "color": "#1677ff",
                        },
                    ]
                },
                title="预警类型人数分布",
            )
        except Exception:
            tier_type_chart = ""

    crit_scores: list[float] = []
    for s in critical:
        try:
            if s.get("score") is not None:
                crit_scores.append(float(s["score"]))
        except (TypeError, ValueError):
            continue
    if crit_scores:
        try:
            from src.agent.education.config import load_config
            from src.agent.education.stats import compute_score_stats

            cfg = load_config()
            full_est: float | None = None
            if pass_line is not None and float(cfg.pass_ratio or 0) > 0:
                full_est = float(pass_line) / float(cfg.pass_ratio)
            stats = compute_score_stats(crit_scores, cfg, full_score=full_est)
            segs = list(stats.get("segments") or [])
            if any(int(s.get("count") or 0) > 0 for s in segs):
                critical_dist_chart = _build_chart_option(
                    "score_distribution",
                    {
                        "segments": segs,
                        "pass_rate": stats.get("pass_rate"),
                    },
                    title="临界生分数分布",
                )
        except Exception:
            critical_dist_chart = ""

    return {
        "REPORT_TITLE": title,
        "REPORT_TYPE": report_type_label(ReportType.TIER_ALERT),
        "REPORT_SUBTITLE": scope,
        "REPORT_TIME": _now_str(),
        "SCOPE": scope,
        "EXAM_NAME": exam_name or "本次考试",
        "SUBJECT_NAME": subject_name or "全科",
        "CRITICAL_COUNT": str(len(critical)),
        "REGRESSION_COUNT": str(len(regression)),
        "IMBALANCED_COUNT": str(len(imbalanced)),
        "TIER_TYPE_CHART": tier_type_chart,
        "CRITICAL_DIST_CHART": critical_dist_chart,
        "CRITICAL_TABLE": _tier_alert_table_html(
            ["姓名", "科目", "分数", "原因"], crit_rows
        ),
        "REGRESSION_TABLE": _tier_alert_table_html(
            ["姓名", "科目", "本次", "上次", "原因"], reg_rows
        ),
        "IMBALANCED_TABLE": _tier_alert_table_html(
            ["姓名", "劣势科目", "低分", "优势科目", "高分", "原因"], imb_rows
        ),
        "SUMMARY": summary,
        "RECOMMENDATIONS": recommendations,
    }


@tool()
def build_tier_alert_report_data_tool(
    students: list[dict[str, Any]] | None = None,
    score_rows: list[dict[str, Any]] | None = None,
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    class_name: str = "",
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    pass_threshold: float | None = None,
    critical_margin: float | None = None,
    regression_threshold: float | None = None,
    full_score: float | None = None,
    render: bool = True,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """组装分层预警报告（临界生 / 大幅退步 / 偏科）并直接渲染 HTML。

    **临界生预警的关键工具**：从上游成绩行识别预警名单后填入
    ``education/tier_alert.html``。LLM 调完只需 ``terminate``，
    **禁止**再调 ``build_subject_diagnosis_sections_tool`` / ``render_html_report``。

    入参优先级：``students`` → ``score_rows`` → ``rows``+``columns`` →
    运行时 ``report_data`` / ``last_exec_result`` 上游 SQL 结果。

    有 ``exam_score`` / ``full_score`` 时，及格线 = 满分 × pass_ratio（如 150×0.6=90），
    避免 150 分卷面仍按默认 60 分线误判临界生。
    """
    from src.agent.education.query_parse import (
        resolve_comprehensive_table_input,
        resolve_diagnostic_score_rows,
    )

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    rd = report_data if isinstance(report_data, dict) else ctx.get("report_data")

    resolved_rows = resolve_diagnostic_score_rows(
        score_rows=score_rows if score_rows else None,
        report_data=rd if isinstance(rd, dict) else None,
        fetch_data=None,
    )
    if not resolved_rows and (rows and columns):
        _, long_rows, long_cols, _ = resolve_comprehensive_table_input(
            records=None,
            rows=rows,
            columns=columns,
            last_exec_result=ctx.get("last_exec_result"),
            report_data=rd if isinstance(rd, dict) else None,
        )
        if long_rows and long_cols:
            resolved_rows = resolve_diagnostic_score_rows(
                score_rows=None,
                report_data={
                    "sub_tasks": [
                        {
                            "exec_result": {
                                "columns": long_cols,
                                "rows": long_rows,
                            }
                        }
                    ]
                },
            )
    if not resolved_rows:
        _, long_rows, long_cols, _ = resolve_comprehensive_table_input(
            records=None,
            rows=None,
            columns=None,
            last_exec_result=ctx.get("last_exec_result"),
            report_data=rd if isinstance(rd, dict) else None,
        )
        if long_rows and long_cols:
            resolved_rows = resolve_diagnostic_score_rows(
                score_rows=None,
                report_data={
                    "sub_tasks": [
                        {
                            "exec_result": {
                                "columns": long_cols,
                                "rows": long_rows,
                            }
                        }
                    ]
                },
            )

    student_list: list[dict[str, Any]] = []
    if students:
        student_list = [dict(s) for s in students if isinstance(s, dict)]
    if not student_list:
        student_list = _score_rows_to_at_risk_students(
            resolved_rows, default_subject=subject_name
        )
    if not student_list:
        return ToolResult(
            content="build_tier_alert_report_data_tool 失败：无学生成绩数据。",
            data={"error": "empty students"},
        )

    cfg = _get_effective_config()
    fs = full_score
    if fs is None:
        for r in resolved_rows:
            if r.get("exam_score") is not None:
                try:
                    fs = float(r["exam_score"])
                    break
                except (TypeError, ValueError):
                    pass
    if pass_threshold is not None:
        cfg.pass_threshold = float(pass_threshold)
    elif fs is not None and float(fs) > 0:
        cfg.pass_threshold = float(fs) * float(cfg.pass_ratio)
    if critical_margin is not None:
        cfg.critical_margin = float(critical_margin)
    if regression_threshold is not None:
        cfg.regression_threshold = float(regression_threshold)

    at_risk = _identify_at_risk(student_list, cfg)
    data = _build_tier_alert_template_data(
        at_risk,
        class_name=class_name,
        school_name=school_name,
        subject_name=subject_name,
        exam_name=exam_name,
        pass_line=cfg.pass_threshold,
    )

    # 补写校内异常提醒（失败不影响报告）
    try:
        from src.agent.education.alert_service import (
            SOURCE_TIER_ALERT,
            upsert_from_at_risk_payload,
        )
        from src.common.core.database import get_db_session

        school_id = ""
        exam_id = ""
        for r in resolved_rows or []:
            if isinstance(r, dict):
                school_id = school_id or str(
                    r.get("school_id") or r.get("school") or ""
                ).strip()
                exam_id = exam_id or str(r.get("exam_id") or "").strip()
        for s in student_list:
            school_id = school_id or str(s.get("school_id") or "").strip()
            exam_id = exam_id or str(s.get("exam_id") or "").strip()
        # 工具参数 school_name 实际常被填成学校编码（如 GZ_A53C79E3）
        if not school_id and school_name and _looks_like_school_id(school_name):
            school_id = school_name.strip()
        # 权限约束里的 target_school
        if not school_id:
            constraints = ctx.get("constraints") if isinstance(ctx.get("constraints"), dict) else {}
            target = str(
                (constraints or {}).get("target_school")
                or ctx.get("target_school")
                or ""
            ).strip()
            if target and _looks_like_school_id(target):
                school_id = target

        ds_id = datasource_id if datasource_id is not None else ctx.get("datasource_id")
        ws_oid = workspace_oid if workspace_oid is not None else (ctx.get("workspace_oid") or 1)
        if school_id and ds_id is not None:
            with get_db_session() as session:
                stats = upsert_from_at_risk_payload(
                    session,
                    at_risk,
                    workspace_oid=int(ws_oid),
                    datasource_id=int(ds_id),
                    school_id=school_id,
                    exam_id=exam_id or exam_name or "unknown",
                    exam_name=exam_name or "",
                    class_name=class_name or "",
                    source=SOURCE_TIER_ALERT,
                )
            import logging

            logging.getLogger(__name__).info(
                "tier_alert anomaly alerts upserted school=%s exam=%s stats=%s",
                school_id,
                exam_id or exam_name,
                stats,
            )
        else:
            import logging

            logging.getLogger(__name__).warning(
                "tier_alert anomaly alerts skipped: school_id=%r datasource_id=%r "
                "(need both; school_name=%r)",
                school_id,
                ds_id,
                school_name,
            )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("tier_alert anomaly alert upsert failed")

    if not render:
        data["critical"] = at_risk.get("critical") or []
        data["regression"] = at_risk.get("regression") or []
        data["imbalanced"] = at_risk.get("imbalanced") or []
        return ToolResult(content="分层预警报告 data 已组装。", data=data)

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    template_name = "education/tier_alert.html"
    title = data.get("REPORT_TITLE") or "分层预警报告"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            content=f"分层预警报告渲染失败：{e}",
            data={"error": str(e), **{k: data[k] for k in ("CRITICAL_COUNT", "REGRESSION_COUNT", "IMBALANCED_COUNT") if k in data}},
        )
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    return _html_report_tool_result(
        (
            f"分层预警报告已渲染（临界生 {data['CRITICAL_COUNT']} / "
            f"退步 {data['REGRESSION_COUNT']} / 偏科 {data['IMBALANCED_COUNT']}）。"
        ),
        payload,
        report_type=ReportType.TIER_ALERT,
    )



@tool()
def build_group_feature_report_data_tool(
    dimension: str = "class",
    score_rows: list[dict[str, Any]] | None = None,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    render: bool = True,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """组装群体特征报告（按班级/区县等维度聚合对比）并直接渲染 HTML。

    **群体特征报告的关键工具**：上游学生明细 → 按维度聚合 → 特征画像 →
    ``education/group_feature.html``。有 ``datasource_id`` 时自动回退拉全校明细与
    各班知识点/小题对比（与班级横向对比同构）。

    LLM 调完只需 ``terminate``，**禁止**再调科目诊断 sections / ``render_html_report``。
    """
    from src.agent.education.group_feature import build_group_feature_data
    from src.agent.education.query_parse import resolve_group_feature_score_rows

    dim = (dimension or "class").strip().lower()
    if dim not in DIMENSIONS:
        return ToolResult(
            content=f"build_group_feature_report_data_tool 失败：不支持维度 `{dimension}`。",
            data={"error": "invalid dimension"},
        )

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    rd = report_data if isinstance(report_data, dict) else ctx.get("report_data")
    rows = resolve_group_feature_score_rows(
        score_rows=score_rows if score_rows else None,
        report_data=rd if isinstance(rd, dict) else None,
        last_exec_result=ctx.get("last_exec_result"),
        dimension=dim,
    )

    item_class_rows: list[dict[str, Any]] = []
    knowledge_class_rows: list[dict[str, Any]] = []
    unique_dim = {
        str(r.get("class") or r.get("class_name") or "").strip()
        for r in rows
        if (r.get("class") or r.get("class_name"))
    }
    unique_dim.discard("")
    need_fetch = bool(datasource_id) and (
        len(rows) < 2 or (dim == "class" and len(unique_dim) < 2)
    )
    if need_fetch:
        fetched = _fetch_subject_diagnosis_rows(
            datasource_id=int(datasource_id),
            workspace_oid=workspace_oid,
            user_id=ctx.get("user_id"),
            school_name=school_name,
            subject_name=subject_name,
            exam_name=exam_name,
            class_name="",
        )
        fetched_rows = list(fetched.get("score_rows") or [])
        if len(fetched_rows) > len(rows):
            rows = fetched_rows
        item_class_rows = list(fetched.get("item_class_rows") or [])
        knowledge_class_rows = list(fetched.get("knowledge_class_rows") or [])
    elif datasource_id and school_name and dim == "class":
        try:
            fetched = _fetch_subject_diagnosis_rows(
                datasource_id=int(datasource_id),
                workspace_oid=workspace_oid,
                user_id=ctx.get("user_id"),
                school_name=school_name,
                subject_name=subject_name,
                exam_name=exam_name,
                class_name="",
            )
            item_class_rows = list(fetched.get("item_class_rows") or [])
            knowledge_class_rows = list(fetched.get("knowledge_class_rows") or [])
            if not rows and fetched.get("score_rows"):
                rows = list(fetched.get("score_rows") or [])
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return ToolResult(
            content=(
                "build_group_feature_report_data_tool 失败：无成绩行可聚合。"
                "请确认上游 SQL 返回学生明细（含 class/score），勿以校级 KPI 作为最终结果。"
            ),
            data={"error": "empty score_rows"},
        )

    cfg = _get_effective_config()
    data = build_group_feature_data(
        rows,
        dimension=dim,
        config=cfg,
        school_name=school_name,
        subject_name=subject_name,
        exam_name=exam_name,
        knowledge_class_rows=knowledge_class_rows or None,
        item_class_rows=item_class_rows or None,
    )
    data["REPORT_TIME"] = _now_str()
    data["KNOWLEDGE_SECTION_CLASS"] = (
        "" if data.get("KNOWLEDGE_COMPARE_TABLE") else "edu-section-empty"
    )
    data["QUESTION_TYPE_SECTION_CLASS"] = (
        "" if data.get("QUESTION_TYPE_COMPARE_TABLE") else "edu-section-empty"
    )
    data["ITEM_SECTION_CLASS"] = (
        "" if data.get("ITEM_COMPARE_TABLE") else "edu-section-empty"
    )

    if not render:
        return ToolResult(content="群体特征报告 data 已组装。", data=data)

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    template_name = "education/group_feature.html"
    title = data.get("REPORT_TITLE") or "群体特征报告"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"群体特征报告渲染失败：{e}", data={"error": str(e)})
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    group_n = len(data.get("_groups") or [])
    return _html_report_tool_result(
        f"群体特征报告已渲染（维度 {data.get('GROUP_DIMENSION')}，{group_n} 组，{len(rows)} 人）。",
        payload,
        report_type=ReportType.GROUP_FEATURE,
    )


@tool()
def build_trend_tracking_report_data_tool(
    class_name: str = "",
    school_name: str = "",
    subject_name: str = "",
    target_name: str = "",
    exam_order: list[str] | None = None,
    records: list[dict[str, Any]] | None = None,
    rows: list[list[Any]] | None = None,
    columns: list[str] | None = None,
    full_score: float | None = None,
    render: bool = True,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """组装成绩趋势报告并直接渲染 HTML（``education/trend_tracking.html``）。

    **成绩趋势 / 走势 / 进退步的关键工具**：从上游多场学生明细生成均分折线、
    历次 KPI 表与变化解读。LLM 调完只需 ``terminate``，**禁止**再调
    ``render_html_report`` / ``build_comprehensive_report_data_tool``（易手填空图）。

    入参优先用运行时注入的完整 SQL；有 ``datasource_id`` + ``class_name`` 时
    可回退拉取该班历次长表。
    """
    from src.agent.education.query_parse import resolve_comprehensive_table_input
    from src.agent.education.trend_tracking import build_trend_tracking_data
    from src.agent.resource.tool.business import (
        _render_template_html,
        _sanitize_report_html,
    )

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    records, rows, columns, used_upstream = resolve_comprehensive_table_input(
        records=records,
        rows=rows,
        columns=columns,
        last_exec_result=ctx.get("last_exec_result"),
        report_data=report_data or ctx.get("report_data"),
    )

    if not records and not (rows and columns) and datasource_id and class_name:
        fetched = _fetch_class_score_long_table(
            datasource_id=int(datasource_id),
            class_name=class_name,
            school_name=school_name,
            subject_name=subject_name,
            workspace_oid=workspace_oid,
        )
        if fetched:
            rows = list(fetched["rows"])
            columns = [str(c) for c in fetched["columns"]]
            used_upstream = True

    if not records and not (rows and columns):
        return ToolResult(
            content=(
                "build_trend_tracking_report_data_tool 失败：未找到上游多场学生成绩明细。"
                "请只传 class_name（禁止手填 records）；确保 DataAnalyst 已查出历次明细。"
            ),
            data={"error": "missing input"},
        )

    exam_seen: list[str] = []
    if not records:
        if not columns:
            return ToolResult(
                content="build_trend_tracking_report_data_tool 失败：columns 为空。",
                data={"error": "empty columns"},
            )
        exam_f, stu_f, sub_f, score_f, total_f = _guess_long_table_fields(
            [str(c) for c in columns],
            exam_field="exam_name",
            student_field="student_id",
            subject_field="subject_name",
            score_field="score",
            total_field="total",
        )
        records, exam_seen = _aggregate_long_table_records(
            list(rows or []),
            [str(c) for c in columns],
            exam_field=exam_f,
            student_field=stu_f,
            subject_field=sub_f,
            score_field=score_f,
            total_field=total_f,
        )

    if not records:
        return ToolResult(
            content="build_trend_tracking_report_data_tool 失败：聚合后无有效成绩行。",
            data={"error": "empty records"},
        )

    order = list(exam_order or []) or list(exam_seen)
    data = build_trend_tracking_data(
        records,
        order or None,
        class_name=class_name,
        school_name=school_name,
        subject_name=subject_name,
        target_name=target_name or class_name,
        full_score=full_score,
    )
    if not render:
        note = "（已用上游全量明细）" if used_upstream else ""
        return ToolResult(
            content=f"成绩趋势报告 data 已组装{note}。",
            data=data,
        )

    template_name = "education/trend_tracking.html"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            content=f"成绩趋势报告渲染失败：{e}",
            data={"error": str(e)},
        )
    payload = {
        "output_type": "html",
        "title": data.get("REPORT_TITLE") or "成绩趋势报告",
        "html": safe_html,
        "mode": "template",
        "chunks": [
            {
                "output_type": "html",
                "title": data.get("REPORT_TITLE") or "成绩趋势报告",
                "content": safe_html,
            }
        ],
    }
    n = int(data.get("EXAM_COUNT") or 0)
    return _html_report_tool_result(
        f"成绩趋势报告已渲染（{data.get('TARGET_NAME') or class_name or '本班'}，{n} 场）。",
        payload,
        report_type=ReportType.TREND_TRACKING,
    )


@tool()
def build_class_overview_report_data_tool(
    class_name: str = "",
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    render: bool = True,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """组装班级总览报告并直接渲染 HTML（``education/class_overview.html``）。

    **成绩总览 / 班级总览的关键工具**：从上游学生明细补齐 KPI、分数段、能力画像后渲染。
    有 ``datasource_id`` 且缺知识点时，仅自动补拉**本班知识点**（薄弱清单/柱），
    不拉全校 peer、不改写 ``score_rows``（避免多人次/跨考污染 KPI）。
    LLM 调完只需 ``terminate``，**禁止**再调 ``build_subject_diagnosis_sections_tool``。
    """
    import logging

    from src.agent.resource.tool.business import (
        _enrich_class_overview_archive,
        _polish_class_overview_html,
        _render_template_html,
        _sanitize_report_html,
    )

    log = logging.getLogger(__name__)
    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    if ctx.get("user_id") is None and ctx.get("userid") is not None:
        ctx = {**ctx, "user_id": ctx.get("userid")}
    rd = report_data if isinstance(report_data, dict) else ctx.get("report_data")
    rd_out: dict[str, Any] = dict(rd) if isinstance(rd, dict) else {}

    ds_id = datasource_id if datasource_id is not None else ctx.get("datasource_id")
    ws_oid = workspace_oid if workspace_oid is not None else ctx.get("workspace_oid")
    uid = ctx.get("user_id")

    need_kn = not (
        isinstance(rd_out.get("knowledge_rows"), list) and rd_out.get("knowledge_rows")
    )
    if ds_id and need_kn and class_name:
        school = _resolve_school_for_class_overview(
            school_name,
            tool_runtime_ctx=ctx,
            report_data=rd_out,
        )
        try:
            kn_bundle = _fetch_subject_diagnosis_rows(
                datasource_id=int(ds_id),
                workspace_oid=int(ws_oid) if ws_oid is not None else None,
                user_id=int(uid) if uid is not None else None,
                school_name=school,
                subject_name=subject_name,
                exam_name=exam_name,
                class_name=class_name,
            )
            kn_rows = list(kn_bundle.get("knowledge_rows") or [])
            if kn_rows:
                rd_out["knowledge_rows"] = kn_rows
        except Exception as e:  # noqa: BLE001
            log.warning("class_overview auto-fetch knowledge failed: %s", e)

    title_bits = [p for p in (school_name, class_name, subject_name) if p]
    title = f"{' · '.join(title_bits)}班级总览报告" if title_bits else "班级总览报告"
    data: dict[str, Any] = {
        "REPORT_TITLE": title,
        "REPORT_TYPE": report_type_label(ReportType.CLASS_OVERVIEW),
        "REPORT_SUBTITLE": class_name or school_name or "本班",
        "REPORT_TIME": _now_str(),
        "CLASS_NAME": class_name or "",
        "EXAM_NAME": exam_name or "本次考试",
        "SUBJECT_NAME": subject_name or "全科",
        "WEAK_KNOWLEDGE_LIST": "",
        "WEAK_KNOWLEDGE_CHART": "",
        "SUMMARY": "<p>班级成绩总览：关注均分、及格率与分数段分布。</p>",
        "RECOMMENDATIONS": "<p>结合 KPI 与分数段，对薄弱区间安排巩固练习。</p>",
    }
    data = _enrich_class_overview_archive(
        "education/class_overview.html",
        data,
        report_data=rd_out or None,
        tool_runtime_ctx=ctx,
    )
    if not render:
        return ToolResult(content="班级总览报告 data 已组装。", data=data)

    template_name = "education/class_overview.html"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
        safe_html = _polish_class_overview_html(
            safe_html, template=template_name, title=str(data.get("REPORT_TITLE") or "")
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"班级总览报告渲染失败：{e}", data={"error": str(e)})
    payload = {
        "output_type": "html",
        "title": data.get("REPORT_TITLE") or title,
        "html": safe_html,
        "mode": "template",
        "chunks": [
            {
                "output_type": "html",
                "title": data.get("REPORT_TITLE") or title,
                "content": safe_html,
            }
        ],
    }
    return _html_report_tool_result(
        f"班级总览报告已渲染（{data.get('CLASS_NAME') or class_name or '本班'}）。",
        payload,
        report_type=ReportType.CLASS_OVERVIEW,
    )


def _resolve_school_for_class_overview(
    school_name: str = "",
    *,
    tool_runtime_ctx: dict[str, Any] | None = None,
    report_data: dict[str, Any] | None = None,
) -> str:
    """班级总览查数用学校：优先权限 school_id，避免口语校名对不上。"""
    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    rd = report_data if isinstance(report_data, dict) else {}
    constraints = ctx.get("constraints") if isinstance(ctx.get("constraints"), dict) else {}
    edu = (
        constraints.get("edu_scope")
        if isinstance(constraints.get("edu_scope"), dict)
        else None
    )
    if not isinstance(edu, dict):
        edu = ctx.get("edu_scope") if isinstance(ctx.get("edu_scope"), dict) else {}

    sn = (school_name or "").strip()
    if sn and _looks_like_school_id(sn):
        return sn
    for cand in (
        edu.get("school_id") if isinstance(edu, dict) else None,
        constraints.get("school_id"),
        constraints.get("target_school"),
        ctx.get("school_id"),
        ctx.get("target_school"),
        rd.get("school_id"),
    ):
        s = str(cand or "").strip()
        if s and _looks_like_school_id(s):
            return s
    for cand in (
        edu.get("school_name") if isinstance(edu, dict) else None,
        sn,
        rd.get("school_name"),
        rd.get("SCHOOL_NAME"),
    ):
        s = str(cand or "").strip()
        if s:
            return s
    return sn


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
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    school_name: str = "",
    subject_name: str = "",
    workspace_oid: int | None = None,
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

    **全量兜底**：运行时会注入 ``tool_runtime_ctx.last_exec_result`` /
    ``report_data``；若 LLM 只抄了 execute_sql preview（默认 20 行），工具自动
    改用完整 SQL 结果，避免报告只含部分学生/单次考试。

    有 ``datasource_id`` 时，会按班级拉取<strong>全部考试</strong>的
    ``tb_score_detail`` 小题，融入第九节个性化建议（按考试分别列出薄弱小题/知识点）。

    Args:
        exam_order: 考试顺序（最早→最近）；为空时按出现顺序去重。
        class_name: 班级名，用于封面标题。
        full_score: 单科满分；用于水平分布阈值。
        render: True（默认）直接渲染 HTML 并上报；False 仅返回 data 字典（调试用）。

    Returns:
        render=True 时 ``data`` 为 HTML 报告载荷（``output_type=html``），可直接
        推送前端；render=False 时 ``data`` 为模板字段字典。
    """
    from src.agent.education.comprehensive import aggregate_student_item_insights
    from src.agent.education.query_parse import resolve_comprehensive_table_input

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    records, rows, columns, used_upstream = resolve_comprehensive_table_input(
        records=records,
        rows=rows,
        columns=columns,
        last_exec_result=ctx.get("last_exec_result"),
        report_data=report_data or ctx.get("report_data"),
    )

    if not records and not (rows and columns) and datasource_id and class_name:
        fetched = _fetch_class_score_long_table(
            datasource_id=int(datasource_id),
            class_name=class_name,
            school_name=school_name,
            subject_name=subject_name,
            workspace_oid=workspace_oid,
        )
        if fetched:
            rows = list(fetched["rows"])
            columns = [str(c) for c in fetched["columns"]]
            used_upstream = True

    if not records and not (rows and columns):
        return ToolResult(
            content=(
                "build_comprehensive_report_data_tool 失败：未找到上游学生×考试明细。"
                "请只传 `class_name`（**禁止**手填 records，会截断）；"
                "确保上游 DataAnalyst 已 execute_sql 查出学生明细。"
            ),
            data={"error": "missing input"},
        )

    if not records:
        # 从长表 rows + columns 聚合
        if not columns:
            return ToolResult(
                content="build_comprehensive_report_data_tool 失败：columns 为空。",
                data={"error": "empty columns"},
            )
        exam_field, student_field, subject_field, score_field, total_field = (
            _guess_long_table_fields(
                columns,
                exam_field=exam_field,
                student_field=student_field,
                subject_field=subject_field,
                score_field=score_field,
                total_field=total_field,
            )
        )
        records, exam_seen = _aggregate_long_table_records(
            rows or [],
            columns,
            exam_field=exam_field,
            student_field=student_field,
            subject_field=subject_field,
            score_field=score_field,
            total_field=total_field,
        )
        exam_order = exam_order or exam_seen

    # 推断全部考试 / 科目，用于拉取小题明细（覆盖历次考试，非仅最近一次）
    exams_in_data: list[str] = []
    seen_e: set[str] = set()
    for r in records:
        e = str(r.get("exam") or "")
        if e and e not in seen_e:
            seen_e.add(e)
            exams_in_data.append(e)
    if exam_order:
        ordered: list[str] = []
        for e in exam_order:
            if e in seen_e and e not in ordered:
                ordered.append(e)
        for e in exams_in_data:
            if e not in ordered:
                ordered.append(e)
        exams_in_data = ordered or exams_in_data
    if not subject_name:
        sub_counts: dict[str, int] = {}
        for r in records:
            for s in (r.get("subjects") or {}):
                if s and s not in ("成绩", "总分"):
                    sub_counts[s] = sub_counts.get(s, 0) + 1
        subject_name = max(sub_counts, key=sub_counts.get) if sub_counts else ""

    item_note = ""
    student_item_insights: dict[str, Any] = {}
    constraints = ctx.get("constraints") if isinstance(ctx.get("constraints"), dict) else {}
    if not school_name:
        school_name = str(constraints.get("target_school") or "")
    if datasource_id and class_name and exams_in_data:
        try:
            detail_rows = _fetch_class_student_item_rows(
                datasource_id=int(datasource_id),
                class_name=class_name,
                exam_names=exams_in_data,
                subject_name=subject_name,
                school_name=school_name,
                workspace_oid=workspace_oid,
            )
            student_item_insights = aggregate_student_item_insights(detail_rows)
            if student_item_insights:
                n_exams_items = len({
                    str(it.get("exam_name") or "")
                    for ins in student_item_insights.values()
                    for it in (ins.get("weak_items") or [])
                    if it.get("exam_name")
                }) or len(exams_in_data)
                item_note = (
                    f"，已融合 {len(student_item_insights)} 人、"
                    f"{n_exams_items} 场考试小题诊断"
                )
            else:
                item_note = "（未查到小题明细，建议基于总分趋势）"
        except Exception as e:  # noqa: BLE001
            item_note = f"（小题拉取跳过：{e}）"

    data = _build_comprehensive_data(
        records,
        exam_order or exams_in_data,
        class_name=class_name,
        full_score=full_score,
        student_item_insights=student_item_insights or None,
    )
    n_exams = len({str(r.get("exam") or "") for r in records if r.get("exam")})
    upstream_note = "（已自动改用完整 SQL 结果，避免 preview 截断）" if used_upstream else ""

    if not render:
        return ToolResult(
            content=(
                f"综合报告 data 已组装（{len(records)} 条记录、{n_exams} 次考试"
                f"{item_note}）{upstream_note}。render=False，未渲染。"
            ),
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
    return _html_report_tool_result(
        (
            f"综合分析报告已渲染完成（{len(records)} 条记录、{n_exams} 次考试、"
            f"HTML {len(safe_html)} 字符{item_note}）{upstream_note}。"
            "报告已自动推送到前端，直接调 terminate 结束即可。"
        ),
        payload,
        report_type=ReportType.COMPREHENSIVE,
    )


def _fetch_class_student_item_rows(
    *,
    datasource_id: int,
    class_name: str = "",
    exam_names: list[str] | None = None,
    exam_name: str = "",
    subject_name: str = "",
    school_name: str = "",
    student_id: str = "",
    workspace_oid: int | None = None,
) -> list[dict[str, Any]]:
    """一次查出班级（或指定学生）在指定考试中每位学生的小题得分率。"""
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.service.sql_auto_fix import run_sql_with_auto_fix

    names = [str(n).strip() for n in (exam_names or []) if str(n).strip()]
    if not names and exam_name:
        names = [exam_name.strip()]

    db_type, config, _ds_name = _load_datasource(datasource_id, workspace_oid)
    parts = _diagnosis_where_parts(
        school_name=_keep_school_filter_arg(school_name),
        class_name=class_name,
        subject_name=subject_name,
        student_id=student_id.strip() if student_id else "",
        exam_name="",  # 多场考试用下方 OR 条件
        exam_id_expr="sd.exam_id",
        skip_exam_name=True,
    )
    if names:
        exam_ors: list[str] = []
        for n in names:
            for c in exam_name_like_candidates(n):
                exam_ors.append(f"e.exam_name LIKE '%{_esc(c)}%'")
        # 去重保持顺序
        seen_or: set[str] = set()
        uniq_ors: list[str] = []
        for o in exam_ors:
            if o not in seen_or:
                seen_or.add(o)
                uniq_ors.append(o)
        if uniq_ors:
            parts.append("(" + " OR ".join(uniq_ors) + ")")
    where = (" WHERE " + " AND ".join(parts)) if parts else ""

    full_score_expr = "COALESCE(eq.question_score, sd.question_score)"
    rate = _score_rate_sql("sd.score", full_score_expr, db_type)
    kn_join = knowledge_names_subquery_join(db_type)
    sql = (
        "SELECT sd.student_id AS student_id,\n"
        "       e.exam_name AS exam_name,\n"
        "       sd.question_no AS question_no,\n"
        "       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        f"       {full_score_expr} AS full_score,\n"
        "       sd.score AS score,\n"
        f"       {rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        f"{kn_join}"
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where
        + "\nORDER BY sd.student_id, e.exam_name, sd.question_no\nLIMIT 50000"
    )
    outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
    if not outcome.success:
        return []
    return _rows_to_dicts(outcome.result if isinstance(outcome.result, dict) else None)


def _fetch_student_item_rows_direct(
    *,
    datasource_id: int,
    student_id: str,
    subject_name: str = "",
    workspace_oid: int | None = None,
) -> list[dict[str, Any]]:
    """按学号直接查小题明细：不强制班级/考试名，避免过滤过严导致空结果。

    以 ``sd.student_id`` 为主条件，``tb_score`` 改为 LEFT JOIN（无总分行仍能出小题）。
    """
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.service.sql_auto_fix import run_sql_with_auto_fix

    sid = (student_id or "").strip()
    if not sid:
        return []
    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    parts = [
        f"(sd.student_id = '{_esc(sid)}' OR sd.student_id LIKE '%{_esc(sid)}%')"
    ]
    if subject_name:
        parts.append(
            f"(sc.subject_name = '{_esc(subject_name)}' OR sc.subject_name IS NULL)"
        )
    where = " WHERE " + " AND ".join(parts)
    full_score_expr = "COALESCE(eq.question_score, sd.question_score)"
    rate = _score_rate_sql("sd.score", full_score_expr, db_type)
    kn_join = knowledge_names_subquery_join(db_type)
    sql = (
        "SELECT sd.student_id AS student_id,\n"
        "       COALESCE(e.exam_name, '未知考试') AS exam_name,\n"
        "       sd.question_no AS question_no,\n"
        "       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,\n"
        f"       {full_score_expr} AS full_score,\n"
        "       sd.score AS score,\n"
        f"       {rate} AS score_rate\n"
        "FROM tb_score_detail sd\n"
        "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
        "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
        f"{kn_join}"
        "LEFT JOIN tb_exam e ON sd.exam_id = e.id\n"
        "LEFT JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id"
        + where
        + "\nORDER BY exam_name, sd.question_no\nLIMIT 20000"
    )
    outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
    if not outcome.success:
        logger.warning(
            "direct student item fetch failed student=%s msg=%s",
            sid,
            getattr(outcome, "message", ""),
        )
        return []
    return _rows_to_dicts(outcome.result if isinstance(outcome.result, dict) else None)


def _resolve_class_name_for_student(
    *,
    datasource_id: int,
    student_name: str,
    school_name: str = "",
    workspace_oid: int | None = None,
) -> str:
    """用学号/姓名反查班级，供学生报告在缺 class_name 时自查全班。"""
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.service.sql_auto_fix import run_sql_with_auto_fix

    sid = (student_name or "").strip()
    if not sid:
        return ""
    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    parts = [f"(sc.student_id = '{_esc(sid)}' OR sc.student_id LIKE '%{_esc(sid)}%')"]
    school_pred = _school_scope_predicate(_keep_school_filter_arg(school_name))
    if school_pred:
        parts.append(school_pred)
    sql = (
        "SELECT sc.class AS class_name\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "WHERE " + " AND ".join(parts) + "\n"
        "LIMIT 1"
    )
    outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
    if not outcome.success or not isinstance(outcome.result, dict):
        return ""
    rows = outcome.result.get("rows") or []
    if not rows:
        return ""
    cell = rows[0][0] if not isinstance(rows[0], dict) else rows[0].get("class_name")
    return str(cell or "").strip()


def _fetch_class_score_long_table(
    *,
    datasource_id: int,
    class_name: str = "",
    student_name: str = "",
    school_name: str = "",
    subject_name: str = "",
    workspace_oid: int | None = None,
) -> dict[str, Any] | None:
    """上游明细缺失时，直接查 tb_score 拉全班×历次考试长表（供综合/学生报告）。"""
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.service.sql_auto_fix import run_sql_with_auto_fix

    cls = (class_name or "").strip()
    if not cls and student_name:
        cls = _resolve_class_name_for_student(
            datasource_id=datasource_id,
            student_name=student_name,
            school_name=school_name,
            workspace_oid=workspace_oid,
        )
    if not cls:
        return None

    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    parts = _diagnosis_where_parts(
        school_name=_keep_school_filter_arg(school_name),
        class_name=cls,
        subject_name=subject_name,
        exam_name="",
        skip_exam_name=True,
    )
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    sql = (
        "SELECT e.exam_name AS exam_name,\n"
        "       sc.student_id AS student_id,\n"
        "       sc.subject_name AS subject_name,\n"
        "       sc.score AS score,\n"
        "       sc.class AS class,\n"
        "       e.exam_time AS exam_time\n"
        "FROM tb_score sc\n"
        "JOIN tb_school sch ON sc.school_id = sch.id\n"
        "JOIN tb_exam e ON sc.exam_id = e.id"
        + where
        + "\nORDER BY e.exam_time, sc.student_id, sc.subject_name\nLIMIT 20000"
    )
    outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
    if not outcome.success or not isinstance(outcome.result, dict):
        return None
    cols = list(outcome.result.get("columns") or [])
    rows = list(outcome.result.get("rows") or [])
    if not cols or not rows:
        return None
    return {
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "class_name": cls,
    }


@tool()
def build_student_exam_report_data_tool(
    student_name: str = "",
    student_id: str = "",
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
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    datasource_id: int | None = None,
    school_name: str = "",
    subject_name: str = "",
    workspace_oid: int | None = None,
) -> ToolResult:
    """组装**单个学生**多次考试深度分析报告并直接渲染 HTML（对齐 Word 样例结构）。

    **这是个体学生多次考试分析的关键工具**：内部完成数据组装 + 模板渲染 +
    HTML 上报。LLM 调完只需 ``terminate``，无需再调 ``render_html_report``。

    ``records`` 应包含**全班**历次考试数据（用于排名/班级均分），工具会按
    ``student_name`` / ``student_id`` 过滤出目标学生并生成**一份**报告。
    有 ``datasource_id`` 时自动拉取该生小题/知识点明细，写入报告第四节与备考建议。

    Args:
        student_name: 目标学生姓名/学号别名（如「学生001」）。
        student_id: 学号（与 student_name 二选一，优先 student_name）。
        records / rows+columns: 与 ``build_comprehensive_report_data_tool`` 相同。
        exam_order: 考试顺序（最早→最近）。
        class_name: 班级名。
        class_size: 班级人数；缺省时从数据推断。
        render: True（默认）直接渲染 HTML；False 仅返回 data 字典。
    """
    from src.agent.education.comprehensive import aggregate_student_item_insights
    from src.agent.education.query_parse import resolve_comprehensive_table_input, student_matches

    target = (student_name or student_id or "").strip()
    if not target:
        return ToolResult(
            content="build_student_exam_report_data_tool 失败：student_name / student_id 为空。",
            data={"error": "missing student_name"},
        )

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    records, rows, columns, _used_upstream = resolve_comprehensive_table_input(
        records=records,
        rows=rows,
        columns=columns,
        last_exec_result=ctx.get("last_exec_result"),
        report_data=report_data or ctx.get("report_data"),
    )

    # 上游缺失时：按班级（或学号反查班级）直接拉 tb_score 全班历次明细
    if not records and not (rows and columns) and datasource_id:
        fetched = _fetch_class_score_long_table(
            datasource_id=int(datasource_id),
            class_name=class_name,
            student_name=target,
            school_name=school_name,
            subject_name=subject_name,
            workspace_oid=workspace_oid,
        )
        if fetched:
            rows = list(fetched["rows"])
            columns = [str(c) for c in fetched["columns"]]
            if not class_name and fetched.get("class_name"):
                class_name = str(fetched["class_name"])

    if not records and not (rows and columns):
        return ToolResult(
            content=(
                "build_student_exam_report_data_tool 失败：未找到上游学生×考试明细。"
                "请只传 `student_name`（**禁止**手填 records）；"
                "确保上游 DataAnalyst 已查出该班历次成绩，或检查数据源中是否有该生成绩。"
            ),
            data={"error": "missing input"},
        )

    if not records:
        if not columns:
            return ToolResult(
                content="build_student_exam_report_data_tool 失败：columns 为空。",
                data={"error": "empty columns"},
            )
        exam_field, student_field, subject_field, score_field, total_field = (
            _guess_long_table_fields(
                columns,
                exam_field=exam_field,
                student_field=student_field,
                subject_field=subject_field,
                score_field=score_field,
                total_field=total_field,
            )
        )
        records, exam_seen = _aggregate_long_table_records(
            rows or [],
            columns,
            exam_field=exam_field,
            student_field=student_field,
            subject_field=subject_field,
            score_field=score_field,
            total_field=total_field,
        )
        exam_order = exam_order or exam_seen

    # 拉取该生全部考试小题/知识点（用于第四节明细与第六节建议）
    item_note = ""
    student_item_insights: dict[str, dict[str, Any]] = {}
    exams_in_data: list[str] = []
    seen_e: set[str] = set()
    for r in records or []:
        e = str(r.get("exam") or "")
        if e and e not in seen_e:
            seen_e.add(e)
            exams_in_data.append(e)
    if exam_order:
        ordered = [e for e in exam_order if e in seen_e]
        for e in exams_in_data:
            if e not in ordered:
                ordered.append(e)
        exams_in_data = ordered or exams_in_data

    from src.agent.education.query_parse import extract_item_detail_rows_from_report_data

    detail_rows: list[dict[str, Any]] = []
    # 1) 优先复用上游 Agent 已查到的小题/知识点 SQL（常见 80+ 行）
    detail_rows = extract_item_detail_rows_from_report_data(
        report_data or ctx.get("report_data"),
        student_id=target,
    )
    if not detail_rows and isinstance(ctx.get("last_exec_result"), dict):
        detail_rows = extract_item_detail_rows_from_report_data(
            {"sub_tasks": [{"exec_result": ctx.get("last_exec_result")}]},
            student_id=target,
        )
    # 2) 再查库：先学号直查（不过滤考试名/班级），再班级路径兜底
    if not detail_rows and datasource_id:
        try:
            detail_rows = _fetch_student_item_rows_direct(
                datasource_id=int(datasource_id),
                student_id=target,
                subject_name=subject_name,
                workspace_oid=workspace_oid,
            )
            if not detail_rows and subject_name:
                # subject 过滤可能导致空，再放宽
                detail_rows = _fetch_student_item_rows_direct(
                    datasource_id=int(datasource_id),
                    student_id=target,
                    subject_name="",
                    workspace_oid=workspace_oid,
                )
            if not detail_rows:
                if not class_name:
                    class_name = _resolve_class_name_for_student(
                        datasource_id=int(datasource_id),
                        student_name=target,
                        school_name=school_name,
                        workspace_oid=workspace_oid,
                    ) or class_name
                # 不带考试名过滤（考试全称常与 LIKE 候选对不上）
                detail_rows = _fetch_class_student_item_rows(
                    datasource_id=int(datasource_id),
                    class_name=class_name,
                    exam_names=None,
                    subject_name=subject_name,
                    school_name=school_name,
                    student_id=target,
                    workspace_oid=workspace_oid,
                )
            if not detail_rows and class_name:
                detail_rows = _fetch_class_student_item_rows(
                    datasource_id=int(datasource_id),
                    class_name=class_name,
                    exam_names=None,
                    subject_name="",
                    school_name="",
                    workspace_oid=workspace_oid,
                )
                detail_rows = [
                    r
                    for r in detail_rows
                    if student_matches(str(r.get("student_id") or ""), target)
                ]
        except Exception:  # noqa: BLE001
            logger.exception("student exam item/knowledge fetch failed")
            item_note = "；小题明细拉取失败，建议仍基于总分趋势"

    if detail_rows:
        student_item_insights = aggregate_student_item_insights(detail_rows)
        n_kn = sum(
            len(ins.get("knowledge_rows") or ins.get("weak_knowledge") or [])
            for ins in student_item_insights.values()
        )
        item_note = f"；已融合小题/知识点 {len(detail_rows)} 条、知识点点位约 {n_kn}"
    elif not item_note:
        item_note = "；未查到小题明细（建议检查 tb_score_detail 与知识点关联）"

    data = _build_student_exam_data(
        records,
        student_name=target,
        exam_order=exam_order or [],
        class_name=class_name,
        class_size=class_size,
        student_item_insights=student_item_insights or None,
    )

    if not render:
        return ToolResult(
            content=(
                f"学生考试分析报告 data 已组装（学生={target}，"
                f"{len(exam_order or exams_in_data or [])} 次考试{item_note}）。"
            ),
            data=data,
        )

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    template_name = "education/student_exam_analysis.html"
    title = data.get("REPORT_TITLE") or f"{target} 考试分析报告"
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
    return _html_report_tool_result(
        (
            f"{target} 考试分析报告已渲染完成（HTML {len(safe_html)} 字符{item_note}）。"
            "报告已自动推送到前端，直接调 terminate 结束即可。"
        ),
        payload,
        report_type=ReportType.STUDENT_PROFILE,
    )


@tool()
def aggregate_dimension_tool(
    dimension: str,
    rows: list[dict[str, Any]] | None = None,
    exec_result: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    score_key: str = "score",
    report_data: dict[str, Any] | None = None,
) -> ToolResult:
    """按单一维度聚合成绩 KPI（citywide/district/school/grade/class/subject 等）。

    ``rows`` 支持三种形态：dict 行列表、``exec_result``（columns+rows）、
    或省略后从 ``report_data`` 上游 SQL 自动读取。禁止直接传二维数组而不带 columns。
    """
    dim = (dimension or "").strip().lower()
    if dim not in DIMENSIONS:
        return ToolResult(
            content=f"不支持的维度 {dimension}，可选：{', '.join(DIMENSIONS)}",
            data={"error": "invalid dimension"},
        )
    data_rows, norm_warnings = _normalize_aggregate_rows(
        rows,
        exec_result=exec_result,
        columns=columns,
        report_data=report_data,
    )
    if not data_rows:
        hint = "；".join(norm_warnings) if norm_warnings else "请传入 exec_result 或 dict 行列表"
        return ToolResult(
            content=f"aggregate_dimension_tool 失败：无有效成绩行。{hint}",
            data={"error": "no rows", "warnings": norm_warnings},
        )
    cfg = _get_effective_config()
    result = _aggregate_by(dim, data_rows, cfg, score_key=score_key)
    note = f"（{'；'.join(norm_warnings)}）" if norm_warnings else ""
    return ToolResult(
        content=f"维度 {dim} 聚合完成，共 {len(result)} 组。{note}",
        data={"dimension": dim, "groups": result, "warnings": norm_warnings},
    )


@tool()
def cross_analyze_tool(
    dim_a: str,
    dim_b: str,
    rows: list[dict[str, Any]] | None = None,
    exec_result: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    metric: str = "avg",
    report_data: dict[str, Any] | None = None,
) -> ToolResult:
    """二维交叉分析，返回 pivot 矩阵与 heatmap option。"""
    data_rows, norm_warnings = _normalize_aggregate_rows(
        rows,
        exec_result=exec_result,
        columns=columns,
        report_data=report_data,
    )
    if not data_rows:
        hint = "；".join(norm_warnings) if norm_warnings else "请传入 exec_result 或 dict 行列表"
        return ToolResult(
            content=f"cross_analyze_tool 失败：无有效成绩行。{hint}",
            data={"error": "no rows", "warnings": norm_warnings},
        )
    pivot = _cross_analyze(dim_a, dim_b, data_rows, metric=metric)
    chart = _build_chart_option("heatmap", pivot, title=f"{dim_a}×{dim_b}")
    note = f"（{'；'.join(norm_warnings)}）" if norm_warnings else ""
    return ToolResult(
        content=(
            f"交叉分析 {dim_a}×{dim_b} 完成（{len(pivot.get('rows') or [])}"
            f"×{len(pivot.get('cols') or [])}）。{note}"
        ),
        data={"pivot": pivot, "heatmap_option": chart, "warnings": norm_warnings},
    )


@tool()
def build_citywide_exam_analysis_report_tool(
    datasource_id: int,
    subject_name: str = "",
    exam_name: str = "",
    workspace_oid: int | None = None,
    user_id: int | None = None,
    render: bool = True,
) -> ToolResult:
    """一键生成「全市 + 考试 + 科目」结构化诊断报告（含区县对比、分数段、小题/知识点）。

    **快捷路径**：内部一次性查数并渲染，适合单 Agent 直调。
    Team 模式请走 Planner 拆分的 3 步路径（DataAnalyst 查数 → fetch →
    ``build_diagnostic_report_data_tool``），以便工具链可观测。
    """
    from src.agent.resource.tool.business import _load_datasource, _render_template_html, _sanitize_report_html

    try:
        db_type, _config, ds_name = _load_datasource(datasource_id, workspace_oid)
    except Exception as e:
        return ToolResult(content=f"build_citywide_exam_analysis_report_tool 失败：{e}", data={"error": str(e)})

    bundle = _fetch_subject_diagnosis_rows(
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
        user_id=user_id,
        subject_name=subject_name,
        exam_name=exam_name,
        db_type=db_type,
    )
    score_rows = bundle.get("score_rows") or []
    score_values = bundle.get("score_values") or []
    item_rows = bundle.get("item_rows") or []
    knowledge_rows = enrich_knowledge_rows(bundle.get("knowledge_rows") or [])
    full_score = bundle.get("full_score")
    sql_logs = bundle.get("sql_logs") or []
    warnings = bundle.get("warnings") or []
    errors = bundle.get("errors") or []

    if not score_rows and not score_values:
        return ToolResult(
            content=(
                f"全市考试分析未查到成绩（ds={ds_name}，subject={subject_name}，exam={exam_name}）。\n"
                f"SQL 记录：\n{_format_diagnosis_sql_logs(sql_logs)}"
            ),
            data={"error": "no data", "sql_logs": sql_logs},
        )

    if not score_rows and score_values:
        score_rows = [
            {"score": v, "exam_score": full_score, "subject": subject_name}
            for v in score_values
        ]

    cfg = _get_effective_config()
    scope_label = "全市"
    report_data = _build_diagnostic_data(
        score_rows,
        config=cfg,
        scope_label=scope_label,
        exam_name=exam_name,
        subject_name=subject_name,
    )
    stats = _compute_stats(score_values, cfg, full_score)
    segments = _normalize_segments(stats.get("segments") or [], full_score=stats.get("full_score"))
    report_data["REPORT_TIME"] = _now_str()
    report_data["SCOPE"] = scope_label
    report_data["SEGMENT_TABLE"] = _segment_table_html(segments, full_score=stats.get("full_score"))
    report_data["SCORE_DIST_CHART"] = _build_chart_option(
        "score_distribution",
        {
            "segments": [{"label": s.get("label", ""), "count": s.get("count", 0)} for s in segments],
            "pass_rate": stats.get("pass_rate") or 0,
        },
        title="全市分数段分布",
    )
    if item_rows:
        report_data["ITEM_TABLE"] = build_item_table_html(_compute_item_metrics(item_rows))
    if knowledge_rows:
        report_data["KNOWLEDGE_TABLE"] = build_knowledge_table_html(knowledge_rows)
    if warnings:
        report_data["GENERAL_INSIGHT"] = (report_data.get("GENERAL_INSIGHT") or "") + (
            "<p class='edu-sub'>" + "；".join(warnings) + "</p>"
        )

    if not render:
        return ToolResult(
            content=f"全市考试分析 data 已组装（{len(score_rows)} 条成绩，区县表已生成）。",
            data=report_data,
        )

    template_name = "education/diagnostic_report.html"
    title = report_data.get("REPORT_TITLE") or "全市结构化诊断报告"
    try:
        raw_html = _render_template_html(template_name, report_data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"全市诊断报告渲染失败：{e}", data={"error": str(e), **report_data})

    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    district_note = "含区县对比" if report_data.get("DISTRICT_SUMMARY") else "区县字段缺失，已降级为全市 KPI"
    return _html_report_tool_result(
        (
            f"全市考试分析报告已渲染（{len(score_rows)} 条成绩，{district_note}，"
            f"小题 {len(item_rows)}、知识点 {len(knowledge_rows)}、HTML {len(safe_html)} 字符）。"
        ),
        payload,
        report_type=ReportType.DIAGNOSTIC_REPORT,
    )


@tool()
def build_diagnostic_report_data_tool(
    score_rows: list[dict[str, Any]] | None = None,
    scope_label: str = "",
    exam_name: str = "",
    subject_name: str = "",
    trend_records: list[dict[str, Any]] | None = None,
    fetch_data: dict[str, Any] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    score_result: dict[str, Any] | None = None,
    render: bool = True,
    report_data: dict[str, Any] | None = None,
    sub_task: str = "",
    tool_runtime_ctx: dict[str, Any] | None = None,
) -> ToolResult:
    """组装结构化诊断报告（一般性/特殊性/动态性）并可选渲染 HTML。

    Team 多步路径：DataAnalyst 查数 → fetch → 本工具组装。
    ``report_data`` / ``sub_task`` / ``tool_runtime_ctx`` 由运行时注入；
    ``score_rows`` / ``fetch_data`` 缺省时自动从上游还原。
    **禁止**手传大字典（易 JSON 截断成空表）。
    """
    from src.agent.education.query_parse import (
        extract_upstream_participant_count,
        resolve_diagnostic_score_rows,
        resolve_subject_diagnosis_fetch_data,
        sub_task_primary_tool,
    )

    task_text = (sub_task or "").strip()
    if render and sub_task_primary_tool(task_text) == "fetch_subject_diagnosis_data_tool":
        return ToolResult(
            content=(
                "本子任务禁止渲染诊断报告。若任务是 fetch_subject_diagnosis_data_tool，"
                "请仅调 fetch 后 terminate；build_diagnostic_report_data_tool(render=true) "
                "留给下一步组装子任务。"
            ),
            data={"error": "render_not_allowed_in_fetch_subtask"},
        )

    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    fetch_data = resolve_subject_diagnosis_fetch_data(
        fetch_data if isinstance(fetch_data, dict) else None,
        report_data=report_data or ctx.get("report_data"),
        tool_runtime_ctx=ctx,
    )

    # 空 list / 空 dict 视为未传，避免 LLM 截断空参盖掉真实上游
    if isinstance(fetch_data, dict):
        if not item_rows:
            item_rows = fetch_data.get("item_rows")
        if not knowledge_rows:
            knowledge_rows = fetch_data.get("knowledge_rows")
        if not score_result:
            score_result = fetch_data.get("score_result")

    rows = resolve_diagnostic_score_rows(
        score_rows=score_rows if score_rows else None,
        report_data=report_data or ctx.get("report_data"),
        fetch_data=fetch_data if isinstance(fetch_data, dict) else None,
    )
    expected_count = extract_upstream_participant_count(
        report_data or ctx.get("report_data")
    )
    if expected_count and len(rows) < expected_count:
        upstream_only = resolve_diagnostic_score_rows(
            score_rows=None,
            report_data=report_data or ctx.get("report_data"),
            fetch_data=None,
        )
        if len(upstream_only) >= expected_count:
            rows = upstream_only

    cfg = _get_effective_config()
    data = _build_diagnostic_data(
        rows,
        trend_records=trend_records if trend_records else None,
        config=cfg,
        scope_label=scope_label,
        exam_name=exam_name,
        subject_name=subject_name,
    )
    data["REPORT_TIME"] = _now_str()
    if scope_label:
        data["SCOPE"] = scope_label

    score_values = [
        float(r["score"]) for r in rows if r.get("score") is not None
    ]
    full_score = None
    for r in rows:
        if r.get("exam_score") is not None:
            full_score = float(r["exam_score"])
            break
    if not score_values and isinstance(score_result, dict):
        cols = score_result.get("columns") or []
        raw = score_result.get("rows") or []
        if cols and raw:
            si = cols.index("score") if "score" in cols else 0
            for row in raw:
                try:
                    score_values.append(float(row[si]))
                except (TypeError, ValueError, IndexError):
                    continue
            fs_idx = cols.index("exam_score") if "exam_score" in cols else -1
            if fs_idx >= 0 and raw:
                try:
                    full_score = float(raw[0][fs_idx])
                except (TypeError, ValueError, IndexError):
                    pass

    if score_values:
        stats = _compute_stats(score_values, cfg, full_score)
        segments = _normalize_segments(stats.get("segments") or [], full_score=stats.get("full_score"))
        data["SEGMENT_TABLE"] = _segment_table_html(segments, full_score=stats.get("full_score"))
        data["SCORE_DIST_CHART"] = _build_chart_option(
            "score_distribution",
            {
                "segments": [{"label": s.get("label", ""), "count": s.get("count", 0)} for s in segments],
                "pass_rate": stats.get("pass_rate") or 0,
            },
            title="分数段分布",
        )

    items = list(item_rows or [])
    knowledge = enrich_knowledge_rows(list(knowledge_rows or []))
    if items:
        data["ITEM_TABLE"] = build_item_table_html(_compute_item_metrics(items))
    if knowledge:
        data["KNOWLEDGE_TABLE"] = build_knowledge_table_html(knowledge)

    if not render:
        return ToolResult(content="诊断报告 data 已组装。", data=data)
    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html
    template_name = "education/diagnostic_report.html"
    title = data.get("REPORT_TITLE") or "结构化诊断报告"
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"诊断报告渲染失败：{e}", data={"error": str(e)})
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    return _html_report_tool_result(
        f"结构化诊断报告已渲染（HTML {len(safe_html)} 字符）。",
        payload,
        report_type=ReportType.DIAGNOSTIC_REPORT,
    )


def _pick_student_overview_from_report(
    report_data: dict[str, Any] | None,
    student_id: str,
    *,
    subject_name: str = "",
) -> dict[str, Any]:
    """从上游 DataAnalyst 子任务 SQL 结果提取该生成绩概览。

    会遍历全部子任务合并字段：优先采用带 ``班级排名`` 列的结果；
    若无排名列但有全班得分表，则按得分计算班排（避免单人表误算第 1 名）。
    """
    from src.agent.education.query_parse import student_matches

    out: dict[str, Any] = {}
    if not report_data:
        return out
    sid = (student_id or "").strip()
    subj_key = (subject_name or "").strip().lower()
    best_computed: tuple[int, float | None, int | None] = (-1, None, None)
    # (row_count, score, class_rank)

    for st in report_data.get("sub_tasks") or []:
        if st.get("sub_task_agent") not in (None, "", "DataAnalyst"):
            continue
        er = st.get("exec_result") or st.get("last_exec_result") or {}
        cols = [str(c) for c in (er.get("columns") or [])]
        rows = er.get("rows") or []
        if not cols or not rows:
            continue
        col_l = [c.lower() for c in cols]
        sid_i = next(
            (i for i, c in enumerate(col_l) if c in ("student_id", "id", "学号")),
            None,
        )
        score_i = _score_column_index(col_l)
        if subj_key:
            for i, c in enumerate(col_l):
                if subj_key in c and any(k in c for k in ("分", "score", "成绩")) and "率" not in c:
                    score_i = i
                    break
        class_rank_i = next(
            (i for i, c in enumerate(col_l) if "class_rank" in c or c in ("班级排名", "班排", "班级名次")),
            None,
        )
        grade_rank_i = next(
            (i for i, c in enumerate(col_l) if "grade_rank" in c or c in ("年级排名", "年排", "年级名次")),
            None,
        )
        class_i = next(
            (i for i, c in enumerate(col_l) if c in ("class", "class_name", "班级")),
            None,
        )
        school_i = next(
            (i for i, c in enumerate(col_l) if "school" in c or c in ("学校", "school_name")),
            None,
        )

        def _cell(row: Any, idx: int | None) -> Any:
            if idx is None or not isinstance(row, (list, tuple)) or idx >= len(row):
                return None
            return row[idx]

        # 1) 本人行上的显式排名列
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            if sid_i is not None:
                rv = str(_cell(row, sid_i) or "").strip()
                if rv and not student_matches(rv, sid):
                    continue
            raw_score = _cell(row, score_i) if score_i is not None else None
            numeric = _coerce_numeric_score(raw_score)
            if numeric is None and score_i is not None and raw_score is not None:
                fallback_i = _score_column_index(col_l)
                if fallback_i is not None and fallback_i != score_i:
                    numeric = _coerce_numeric_score(_cell(row, fallback_i))
            if numeric is not None and out.get("total_score") is None:
                out["total_score"] = numeric
            parsed_rank = _parse_rank_value(_cell(row, class_rank_i))
            if parsed_rank is not None and out.get("class_rank") is None:
                out["class_rank"] = parsed_rank
            parsed_grade = _parse_rank_value(_cell(row, grade_rank_i))
            if parsed_grade is not None and out.get("grade_rank") is None:
                out["grade_rank"] = parsed_grade
            if out.get("class_name") in (None, "", "-"):
                cn = _cell(row, class_i)
                if cn:
                    out["class_name"] = cn
            if out.get("school_name") in (None, "", "-"):
                sn = _cell(row, school_i)
                if sn:
                    out["school_name"] = sn

        # 2) 无显式排名时，用全班得分表推算（优先行数更多的结果）
        if out.get("class_rank") is None and score_i is not None and len(rows) >= 2:
            c_score, c_rank = _compute_class_rank_from_rows(
                rows, cols, sid, score_i=score_i, sid_i=sid_i
            )
            if c_rank is not None and len(rows) > best_computed[0]:
                best_computed = (len(rows), c_score, c_rank)

    if out.get("class_rank") is None and best_computed[2] is not None:
        out["class_rank"] = best_computed[2]
        if out.get("total_score") is None and best_computed[1] is not None:
            out["total_score"] = best_computed[1]
    return out


def _pick_student_multi_exam_series_from_report(
    report_data: dict[str, Any] | None,
    student_id: str,
    *,
    subject_name: str = "",
) -> list[dict[str, Any]]:
    """从上游 SQL 提取该生各场得分，并用同结果全班数据推算均分/最高分/差距。"""
    from collections import defaultdict

    from src.agent.education.query_parse import is_vague_exam_name, student_matches

    if not report_data:
        return []
    sid = (student_id or "").strip()
    subj_key = (subject_name or "").strip().lower()

    by_exam: dict[str, dict[str, Any]] = {}
    exam_order: list[str] = []
    class_scores: dict[str, list[float]] = defaultdict(list)

    for st in report_data.get("sub_tasks") or []:
        if st.get("sub_task_agent") not in (None, "", "DataAnalyst"):
            continue
        er = st.get("exec_result") or st.get("last_exec_result") or {}
        cols = [str(c) for c in (er.get("columns") or [])]
        rows = er.get("rows") or []
        if not cols or not rows:
            continue
        col_l = [c.lower() for c in cols]
        sid_i = next(
            (i for i, c in enumerate(col_l) if c in ("student_id", "id", "学号")),
            None,
        )
        exam_i = next(
            (
                i
                for i, c in enumerate(col_l)
                if c in ("exam_name", "exam", "考试", "考试名称") or ("exam" in c and "score" not in c)
            ),
            None,
        )
        if exam_i is None:
            continue
        score_i = _score_column_index(col_l)
        if subj_key:
            for i, c in enumerate(col_l):
                if subj_key in c and any(k in c for k in ("分", "score", "成绩")) and "率" not in c:
                    score_i = i
                    break
        class_rank_i = next(
            (i for i, c in enumerate(col_l) if "class_rank" in c or c in ("班级排名", "班排")),
            None,
        )

        def _cell(row: Any, idx: int | None) -> Any:
            if idx is None or not isinstance(row, (list, tuple)) or idx >= len(row):
                return None
            return row[idx]

        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            exam = str(_cell(row, exam_i) or "").strip()
            if not exam or is_vague_exam_name(exam):
                continue
            score = _coerce_numeric_score(_cell(row, score_i))
            if score is None:
                continue
            class_scores[exam].append(score)
            is_self = True
            if sid_i is not None:
                rv = str(_cell(row, sid_i) or "").strip()
                is_self = bool(rv) and student_matches(rv, sid)
            if not is_self:
                continue
            if exam not in by_exam:
                exam_order.append(exam)
                by_exam[exam] = {}
            by_exam[exam]["score"] = score
            rank = _cell(row, class_rank_i)
            if rank is not None:
                by_exam[exam]["class_rank"] = rank

    out: list[dict[str, Any]] = []
    for exam in exam_order:
        row = dict(by_exam.get(exam) or {})
        score = _coerce_numeric_score(row.get("score"))
        if score is None:
            continue
        scores = class_scores.get(exam) or []
        if scores:
            row["class_avg"] = round(sum(scores) / len(scores), 2)
            row["class_max"] = max(scores)
            row["gap_to_first"] = round(max(scores) - score, 2)
        out.append({"exam_name": exam, **row})
    return out


def _build_student_multi_exam_overview_html(
    series: list[dict[str, Any]],
    *,
    student_id: str = "",
) -> tuple[str, str, str, str]:
    """返回 (overview_title, exam_badge, kpi_html, detail_html含表+趋势图)。"""
    if len(series) < 2:
        return "", "", "", ""
    exams = [str(s.get("exam_name") or "") for s in series]
    scores = [_coerce_numeric_score(s.get("score")) for s in series]
    valid_scores = [v for v in scores if v is not None]
    avg = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None
    last = series[-1]
    last_score = _coerce_numeric_score(last.get("score"))
    last_gap = _coerce_numeric_score(last.get("gap_to_first"))
    if last_gap is None:
        cm = _coerce_numeric_score(last.get("class_max"))
        if cm is not None and last_score is not None:
            last_gap = round(cm - last_score, 2)

    badge = f"共{len(series)}次考试（{'、'.join(exams)}）"
    title = f"多次考试概览（共{len(series)}次）"
    kpi = (
        f'<div class="edu-kpi"><div class="label">考试次数</div><div class="value">{len(series)}</div></div>'
        f'<div class="edu-kpi"><div class="label">多次均分</div><div class="value">{_fmt_val(avg)}</div></div>'
        f'<div class="edu-kpi"><div class="label">最近得分</div><div class="value">{_fmt_val(last_score)}</div></div>'
        f'<div class="edu-kpi"><div class="label">最近距第1名</div><div class="value">'
        f'{_fmt_val(last_gap) if last_gap is not None else "-"}</div></div>'
    )
    head = (
        "<tr><th>考试</th><th class='num'>得分</th><th class='num'>班级均分</th>"
        "<th class='num'>班级最高</th><th class='num'>与第1名差距</th><th class='num'>班级排名</th></tr>"
    )
    body_parts: list[str] = []
    for s in series:
        sc = _coerce_numeric_score(s.get("score"))
        ca = _coerce_numeric_score(s.get("class_avg"))
        cm = _coerce_numeric_score(s.get("class_max"))
        gap = _coerce_numeric_score(s.get("gap_to_first"))
        if gap is None and sc is not None and cm is not None:
            gap = round(cm - sc, 2)
        body_parts.append(
            f"<tr><td>{s.get('exam_name') or '-'}</td>"
            f"<td class='num'>{_fmt_val(sc)}</td>"
            f"<td class='num'>{_fmt_val(ca)}</td>"
            f"<td class='num'>{_fmt_val(cm)}</td>"
            f"<td class='num'>{_fmt_val(gap) if gap is not None else '-'}</td>"
            f"<td class='num'>{_fmt_val(s.get('class_rank'))}</td></tr>"
        )
    table = (
        '<div class="edu-table-wrap"><table class="edu-table"><thead>'
        f"{head}</thead><tbody>{''.join(body_parts)}</tbody></table></div>"
    )
    chart = ""
    if valid_scores:
        chart = _build_chart_option(
            "trend_line",
            {
                "x_labels": exams,
                "series": [
                    {
                        "name": "得分",
                        "values": [float(v) if v is not None else 0 for v in scores],
                    }
                ],
            },
            title=f"{student_id or '该生'} 历次得分趋势",
        )
    chart_block = ""
    if chart:
        chart_block = (
            '<div id="multiExamTrend" class="edu-chart"></div>'
            f'<script type="application/json" id="multiExamTrendData">{chart}</script>'
            "<script>(function(){var el=document.getElementById('multiExamTrendData');"
            "if(!el)return;var raw=(el.textContent||'').trim();if(!raw){"
            "document.getElementById('multiExamTrend').style.display='none';return;}"
            "try{echarts.init(document.getElementById('multiExamTrend')).setOption(JSON.parse(raw));}"
            "catch(e){}})();</script>"
        )
    detail = (
        '<p class="edu-sub">以下为各场得分明细对比；与第1名差距为正表示落后班级最高分。</p>'
        f"{table}{chart_block}"
    )
    return title, badge, kpi, detail


def _build_student_subject_summary(
    *,
    student_id: str,
    subject_name: str,
    exam_name: str,
    knowledge_rows: list[dict[str, Any]],
    item_rows: list[dict[str, Any]],
    overview: dict[str, Any],
    weak_threshold: float,
) -> str:
    weak = [
        str(r.get("knowledge_name") or "")
        for r in knowledge_rows
        if r.get("level") == "需加强"
    ]
    lines = [
        f"<p>学号 <strong>{student_id}</strong> 在 <strong>{exam_name or '本次考试'}</strong> "
        f"<strong>{subject_name or '该科目'}</strong> 的个人学情如下：</p>",
    ]
    if overview.get("total_score") is not None:
        lines.append(
            f"<p>得分 <strong>{_fmt_val(overview.get('total_score'))}</strong>，"
            f"班级排名 <strong>{_fmt_val(overview.get('class_rank'))}</strong>，"
            f"年级排名 <strong>{_fmt_val(overview.get('grade_rank'))}</strong>。</p>"
        )
    if knowledge_rows:
        lines.append(
            f"<p>共涉及 <strong>{len(knowledge_rows)}</strong> 个知识点，"
            f"其中 <strong>{len(weak)}</strong> 个需加强（得分率 &lt; {weak_threshold:g}%）。</p>"
        )
        if weak:
            lines.append(f"<p><strong>薄弱知识点：</strong>{'、'.join(weak[:8])}。</p>")
    if item_rows:
        weak_items = [
            r for r in item_rows
            if r.get("score_rate") is not None and float(r["score_rate"]) < weak_threshold
        ]
        if weak_items:
            qnos = "、".join(f"第{r.get('question_no')}题" for r in weak_items[:6])
            lines.append(f"<p><strong>薄弱小题：</strong>{qnos}。</p>")
    return "".join(lines)


@tool()
def build_student_subject_diagnosis_tool(
    datasource_id: int,
    student_id: str,
    subject_name: str = "",
    exam_name: str = "",
    school_name: str = "",
    class_name: str = "",
    workspace_oid: int | None = None,
    user_id: int | None = None,
    report_data: dict[str, Any] | None = None,
    weak_threshold: float = 60.0,
    render: bool = True,
) -> ToolResult:
    """组装**单个学生**科目分析报告（个人视角）。

    单场：小题/知识点诊断；检测到多场成绩时自动切换多次概览（次数、均分、与第1名差距、趋势）。
    内部按 ``student_id`` 查询小题/知识点明细，渲染 ``education/student_subject_diagnosis.html``。
    LLM 调完只需 ``terminate``。
    """
    from src.agent.education.query_parse import is_vague_exam_name

    sid = (student_id or "").strip()
    if not sid:
        return ToolResult(
            content="build_student_subject_diagnosis_tool 失败：student_id 为空。",
            data={"error": "missing student_id"},
        )
    # 模糊表述不能写进 SQL / 报告标题
    if is_vague_exam_name(exam_name):
        exam_name = ""
    try:
        from src.agent.resource.tool.business import _load_datasource

        db_type, _config, ds_name = _load_datasource(datasource_id, workspace_oid)
    except Exception as e:
        return ToolResult(
            content=f"build_student_subject_diagnosis_tool 失败：{e}",
            data={"error": str(e)},
        )

    overview = _pick_student_overview_from_report(report_data, sid, subject_name=subject_name)
    # 中文简称不宜做 sch.name 精确匹配；school_id（如 YZZX）须保留按 sc.school_id 过滤
    fetch_school = school_name
    if sid and _is_unsafe_school_name_filter(school_name) and not _looks_like_school_id(school_name):
        fetch_school = ""
    fetch_class = class_name or str(overview.get("class_name") or "").strip()

    bundle = _fetch_subject_diagnosis_rows(
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
        user_id=user_id,
        school_name=fetch_school,
        subject_name=subject_name,
        exam_name=exam_name,
        class_name=fetch_class,
        student_id=sid,
        db_type=db_type,
    )
    item_rows = bundle["item_rows"]
    knowledge_rows = enrich_knowledge_rows(bundle["knowledge_rows"])
    score_rows = bundle.get("score_rows") or []
    warnings = list(bundle.get("warnings") or [])
    if not overview.get("class_name") and score_rows:
        overview["class_name"] = score_rows[0].get("class") or score_rows[0].get("class_name")
    if not overview.get("school_name") and score_rows:
        overview["school_name"] = score_rows[0].get("school_name")

    # 得分必须以数值为准；上游误把学号填入 score 列时，用 score_rows 覆盖
    overview_score = _coerce_numeric_score(overview.get("total_score"))
    row_score, row_class_rank = _pick_score_from_score_rows(score_rows, sid)
    if row_score is not None:
        overview["total_score"] = row_score
    elif overview_score is not None:
        overview["total_score"] = overview_score
    else:
        overview["total_score"] = None
    # 班排：优先上游可靠排名；仅当 score_rows 含全班样本时才用本地推算覆盖空值
    # （按学号 fetch 常只剩 1 行，推算会错误得到第 1 名）
    overview_rank = _parse_rank_value(overview.get("class_rank"))
    if overview_rank is not None:
        overview["class_rank"] = overview_rank
    elif row_class_rank is not None:
        overview["class_rank"] = row_class_rank
    else:
        overview["class_rank"] = None
    grade_rank = _parse_rank_value(overview.get("grade_rank"))
    overview["grade_rank"] = grade_rank

    class_label = str(overview.get("class_name") or fetch_class or class_name or "").strip()
    school_label = str(
        overview.get("school_name")
        or (score_rows[0].get("school_name") if score_rows else "")
        or school_name
        or ""
    ).strip()
    subtitle = " ".join(p for p in (school_label, class_label) if p)

    full_score_val = bundle.get("full_score")
    if full_score_val is None and score_rows:
        fs = score_rows[0].get("exam_score")
        try:
            full_score_val = float(fs) if fs is not None else None
        except (TypeError, ValueError):
            full_score_val = None

    multi_series = _pick_student_multi_exam_series_from_report(
        report_data, sid, subject_name=subject_name
    )
    # score_rows 也可能含多场（无 exam 列时无法拆）
    if len(multi_series) < 2 and score_rows:
        from src.agent.education.query_parse import student_matches

        exam_scores: dict[str, float] = {}
        for r in score_rows:
            if not isinstance(r, dict):
                continue
            if not student_matches(str(r.get("student_id") or r.get("id") or ""), sid):
                # 全班行：无学号匹配时若仅有一行本人则接受
                if r.get("student_id") or r.get("id"):
                    continue
            en = str(r.get("exam_name") or r.get("exam") or "").strip()
            sc = _coerce_numeric_score(r.get("score"))
            if en and sc is not None and not is_vague_exam_name(en):
                exam_scores[en] = sc
        if len(exam_scores) >= 2 and len(multi_series) < 2:
            multi_series = [{"exam_name": e, "score": s} for e, s in exam_scores.items()]

    ov_title, exam_badge, multi_kpi, multi_detail = _build_student_multi_exam_overview_html(
        multi_series, student_id=sid
    )
    is_multi = bool(ov_title)
    display_exam = exam_badge if is_multi else (exam_name or "本次考试")
    if is_vague_exam_name(display_exam):
        display_exam = exam_badge or "本次考试"

    single_kpi = (
        f'<div class="edu-kpi"><div class="label">得分</div><div class="value">{_fmt_val(overview.get("total_score"))}</div></div>'
        f'<div class="edu-kpi"><div class="label">满分</div><div class="value">{_fmt_val(full_score_val)}</div></div>'
        f'<div class="edu-kpi"><div class="label">班级排名</div><div class="value">'
        f'{_fmt_val(overview.get("class_rank")) if overview.get("class_rank") is not None else "-"}</div></div>'
        f'<div class="edu-kpi"><div class="label">年级排名</div><div class="value">'
        f'{_fmt_val(overview.get("grade_rank")) if overview.get("grade_rank") is not None else "-"}</div></div>'
    )

    data: dict[str, Any] = {
        "REPORT_TITLE": f"{sid} {subject_name or '科目'}学情分析报告",
        "REPORT_TYPE": report_type_label(ReportType.STUDENT_PROFILE),
        "REPORT_SUBTITLE": subtitle or f"学号 {sid}",
        "REPORT_TIME": _now_str(),
        "STUDENT_NAME": sid,
        "SUBJECT_NAME": subject_name or "全科",
        "CLASS_NAME": class_label or "-",
        "EXAM_NAME": display_exam,
        "OVERVIEW_TITLE": ov_title if is_multi else "本次考试概览",
        "OVERVIEW_KPIS": multi_kpi if is_multi else single_kpi,
        "MULTI_EXAM_SECTION": multi_detail if is_multi else "",
        "TOTAL_SCORE": _fmt_val(overview.get("total_score")),
        "FULL_SCORE": _fmt_val(full_score_val),
        "CLASS_RANK": _fmt_val(overview.get("class_rank")) if overview.get("class_rank") is not None else "-",
        "GRADE_RANK": _fmt_val(overview.get("grade_rank")) if overview.get("grade_rank") is not None else "-",
        "ITEM_TABLE": build_item_table_html(_compute_item_metrics(item_rows)) if item_rows else "",
        "KNOWLEDGE_TABLE": build_knowledge_table_html(knowledge_rows) if knowledge_rows else "",
        "WEAK_KNOWLEDGE_LIST": "、".join(
            str(r.get("knowledge_name") or "")
            for r in knowledge_rows
            if r.get("level") == "需加强"
        )[:500],
        "ABILITY_INSIGHT": "",
        "ABILITY_TIER_CHART": "",
        "ABILITY_TIER_TABLE": "",
        "QUESTION_TYPE_CHART": "",
        "QUESTION_TYPE_TABLE": "",
        "KNOWLEDGE_CHART": "",
        "SUMMARY": _build_student_subject_summary(
            student_id=sid,
            subject_name=subject_name,
            exam_name=display_exam,
            knowledge_rows=knowledge_rows,
            item_rows=item_rows,
            overview=overview,
            weak_threshold=weak_threshold,
        ),
        "RECOMMENDATIONS": build_diagnosis_recommendations(
            knowledge_rows=knowledge_rows,
            item_rows=item_rows,
            weak_threshold=weak_threshold,
            audience="student",
        ),
    }
    if knowledge_rows:
        data["KNOWLEDGE_CHART"] = _build_chart_option(
            "knowledge_bar",
            {
                "categories": [str(r.get("knowledge_name") or "") for r in knowledge_rows[:12]],
                "values": [float(r.get("score_rate") or 0) for r in knowledge_rows[:12]],
            },
            title="知识点得分率",
        )
    tier = build_ability_tier_summary(knowledge_rows, weak_threshold=weak_threshold)
    item_metrics = _compute_item_metrics(item_rows)
    data["ABILITY_INSIGHT"] = build_ability_tier_insight(
        tier,
        knowledge_rows=knowledge_rows,
        item_rows=item_metrics,
        weak_threshold=weak_threshold,
    )
    tier_table = _build_ability_tier_table_html(knowledge_rows)
    if tier_table:
        data["ABILITY_TIER_TABLE"] = tier_table
        levels = [s.get("ability_level") for s in tier.get("by_ability_level") or []]
        values = [float(s.get("avg_score_rate") or 0) for s in tier.get("by_ability_level") or []]
        if levels and values:
            data["ABILITY_TIER_CHART"] = _build_chart_option(
                "ability_radar",
                {
                    "levels": [ABILITY_LABELS.get(str(level), str(level)) for level in levels],
                    "values": values,
                },
                title="能力画像",
            )
    qtype_table = _build_question_type_table_html(item_metrics)
    if qtype_table:
        data["QUESTION_TYPE_TABLE"] = qtype_table
        from collections import defaultdict

        buckets: dict[str, list[float]] = defaultdict(list)
        for ir in item_metrics:
            if ir.get("question_type") and ir.get("score_rate") is not None:
                buckets[str(ir["question_type"])].append(float(ir["score_rate"]))
        if buckets:
            cats = sorted(buckets.keys())
            vals = [round(sum(buckets[c]) / len(buckets[c]), 2) for c in cats]
            data["QUESTION_TYPE_CHART"] = _build_chart_option(
                "question_type_bar",
                {"categories": cats, "values": vals},
                title="题型得分率",
            )

    if not render:
        return ToolResult(
            content=f"学生科目诊断 data 已组装（学号={sid}，小题 {len(item_rows)}，知识点 {len(knowledge_rows)}）。",
            data=data,
        )

    from src.agent.resource.tool.business import _render_template_html, _sanitize_report_html

    template_name = "education/student_subject_diagnosis.html"
    title = str(data.get("REPORT_TITLE") or f"{sid} 学情报告")
    try:
        raw_html = _render_template_html(template_name, data)
        safe_html = _sanitize_report_html(raw_html.strip())
    except Exception as e:  # noqa: BLE001
        return ToolResult(content=f"学生学情报告渲染失败：{e}", data={"error": str(e), **data})
    if not safe_html.strip():
        return ToolResult(content="学生学情报告渲染失败：HTML 为空。", data={"error": "empty html"})
    payload = {
        "output_type": "html",
        "title": title,
        "html": safe_html,
        "mode": "template",
        "chunks": [{"output_type": "html", "title": title, "content": safe_html}],
    }
    weak_cnt = sum(1 for r in knowledge_rows if r.get("level") == "需加强")
    warn_note = f"\n注意：{'；'.join(warnings)}" if warnings else ""
    return _html_report_tool_result(
        (
            f"学生学情报告已渲染（学号={sid}，ds={ds_name}，小题 {len(item_rows)} 条，"
            f"知识点 {len(knowledge_rows)} 个、薄弱 {weak_cnt} 个、HTML {len(safe_html)} 字符）。"
            f"{warn_note}"
        ),
        payload,
        report_type=ReportType.STUDENT_PROFILE,
    )


@tool()
def build_knowledge_tier_sections_tool(
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """生成科目诊断的能力层级与题型区块 HTML。"""
    items = _compute_item_metrics(list(item_rows or []))
    knowledge = enrich_knowledge_rows(list(knowledge_rows or []))
    data: dict[str, Any] = {}
    tier_table = _build_ability_tier_table_html(knowledge)
    if tier_table:
        data["ABILITY_TIER_TABLE"] = tier_table
    qtype_table = _build_question_type_table_html(items)
    if qtype_table:
        data["QUESTION_TYPE_TABLE"] = qtype_table
    return ToolResult(content="能力层级/题型区块已生成。", data=data)


# 暴露给 ``build_default_toolpack`` 的列表（与 business.py 工具并列）。
EDUCATION_TOOLS = [
    resolve_score_schema,
    compute_score_stats_tool,
    compute_rankings_tool,
    identify_at_risk_students_tool,
    build_chart_option_tool,
    select_report_template_tool,
    build_comprehensive_report_data_tool,
    build_tier_alert_report_data_tool,
    build_group_feature_report_data_tool,
    build_class_overview_report_data_tool,
    build_trend_tracking_report_data_tool,
    build_student_exam_report_data_tool,
    build_student_subject_diagnosis_tool,
    fetch_subject_diagnosis_data_tool,
    build_subject_diagnosis_sections_tool,
    build_subject_diagnosis_report_tool,
    aggregate_dimension_tool,
    cross_analyze_tool,
    build_citywide_exam_analysis_report_tool,
    build_diagnostic_report_data_tool,
    build_knowledge_tier_sections_tool,
]


__all__ = [
    "EDUCATION_TOOLS",
    "aggregate_dimension_tool",
    "build_chart_option_tool",
    "build_comprehensive_report_data_tool",
    "build_citywide_exam_analysis_report_tool",
    "build_class_overview_report_data_tool",
    "build_trend_tracking_report_data_tool",
    "build_diagnostic_report_data_tool",
    "build_group_feature_report_data_tool",
    "build_knowledge_tier_sections_tool",
    "build_student_exam_report_data_tool",
    "build_student_subject_diagnosis_tool",
    "build_subject_diagnosis_report_tool",
    "build_subject_diagnosis_sections_tool",
    "build_tier_alert_report_data_tool",
    "compute_rankings_tool",
    "compute_score_stats_tool",
    "cross_analyze_tool",
    "fetch_subject_diagnosis_data_tool",
    "identify_at_risk_students_tool",
    "resolve_score_schema",
    "select_report_template_tool",
]
