"""教育学情配置 API。

阈值与异常规则持久化到系统库表 ``edu_anomaly_config``（见 ``config_store`` /
``anomaly_persistence``）。

端点：
- ``GET /api/v1/education/report-config`` — 读取当前生效阈值；
- ``PUT /api/v1/education/report-config`` — 部分更新阈值（落库）；
- ``POST /api/v1/education/report-config/reset`` — 恢复默认并写回 DB。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from audit.service.decorators import audit_access
from common.exceptions.base import BadRequestException
from common.schemas.response import error_response, success_response
from src.agent.education import config_store
from src.agent.education.config import config_to_public_dict
from src.agent.education.orchestrator import ReportOrchestrator
from src.agent.education.schema_mapping import (
    ScoreSchemaMapping,
    infer_normalized_mapping,
    infer_wide_mapping,
    load_schema_from_config,
)
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import assert_datasource_accessible, get_workspace_oid

router = APIRouter(prefix="/education", tags=["education"])


class ReportConfigPayload(BaseModel):
    """阈值配置 payload；所有字段可选，仅传需要更新的。"""

    pass_threshold: Optional[float] = Field(None, ge=0, description="及格绝对分兜底（无满分时）；优先改 pass_ratio")
    excellent_threshold: Optional[float] = Field(None, ge=0, description="优秀绝对分兜底（无满分时）；优先改 excellent_ratio")
    pass_ratio: Optional[float] = Field(
        None, ge=0, le=1, description="及格比例（占卷面满分，如 0.6=60%）；有满分时优先"
    )
    excellent_ratio: Optional[float] = Field(
        None, ge=0, le=1, description="优秀比例（占卷面满分，如 0.85=85%）；有满分时优先"
    )
    default_full_score: Optional[float] = Field(None, ge=1, description="满分兜底，默认 100")
    critical_margin: Optional[float] = Field(None, ge=0, description="临界生判定半径，默认 5")
    regression_threshold: Optional[float] = Field(
        None,
        description="退步判定阈值（负数，如 -10 表示降幅 ≥10 分算退步）",
    )
    imbalance_score_gap: Optional[float] = Field(
        None,
        ge=0,
        description="偏科科间分差下限，默认 20",
    )
    anomaly_rules: Optional[list[dict[str, Any]]] = Field(
        None,
        description="可选：显式异常规则列表（含五类参数）；不传则由经典字段推导",
    )


@router.get("/report-config")
async def get_report_config() -> dict:
    cfg = config_store.get_config()
    return success_response(config_to_public_dict(cfg))


@router.put("/report-config")
async def update_report_config(payload: ReportConfigPayload) -> dict:
    cfg = config_store.update_config(payload.model_dump(exclude_none=True))
    return success_response(config_to_public_dict(cfg), message="配置已更新")


@router.post("/report-config/reset")
async def reset_report_config() -> dict:
    config_store.reset_config()
    return success_response(config_to_public_dict(config_store.get_config()), message="已恢复默认配置")


# ---- 批量报告（Phase 4） --------------------------------------------------


class BatchReportRequest(BaseModel):
    """按班级列表批量生成报告。

    - 传 ``report_type``：走分析工具确定性 ``run_spec``（推荐）
    - 仅传 ``question``：兼容旧 NLP 意图解析路径（``{class}`` 占位）
    """

    datasource_id: int = Field(..., description="数据源 ID")
    question: Optional[str] = Field(
        None, description="报告问题模板，如「生成{class}期中成绩分析报告」（兼容旧路径）"
    )
    report_type: Optional[str] = Field(None, description="报告类型，如 class_overview")
    filters: dict[str, str] = Field(
        default_factory=dict, description="公共筛选（班级由 class_names 逐个覆盖）"
    )
    class_names: list[str] = Field(..., min_length=1, description="班级名列表")
    audience: Optional[str] = Field(None, description="报告受众")
    include_charts: bool = Field(True, description="是否嵌入图表")
    workspace_oid: Optional[int] = Field(None, description="工作区 OID，鉴权用")


def _build_orchestrator(
    datasource_id: int,
    workspace_oid: int | None,
    user_id: int | None = None,
) -> ReportOrchestrator:
    """用真实数据源回调构造 ReportOrchestrator。"""
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.db.db import get_schema_info
    from src.datasource.service.execute_with_permission import (
        execute_sql_with_permission_by_user_id,
    )

    db_type, config, _ds_name = _load_datasource(datasource_id, workspace_oid)

    async def execute_sql(sql: str) -> dict:
        success, msg, result, _sql_run = execute_sql_with_permission_by_user_id(
            user_id, datasource_id, workspace_oid, sql
        )
        # 失败必须抛出，否则编排层会当成「0 行成绩」生成空壳报告。
        if not success:
            raise RuntimeError(msg or "SQL 执行失败")
        if not isinstance(result, dict):
            raise RuntimeError("SQL 执行结果格式异常")
        if result.get("error"):
            raise RuntimeError(str(result.get("error")))
        return {
            "columns": result.get("columns") or [],
            "rows": result.get("rows") or [],
            "row_count": result.get("row_count") or len(result.get("rows") or []),
        }

    async def resolve_schema() -> ScoreSchemaMapping:
        bundle = load_schema_from_config()
        if bundle is not None:
            return bundle.mapping
        schema = get_schema_info(db_type, config)
        normalized = infer_normalized_mapping(schema)
        if normalized is not None:
            return normalized
        # 宽表 fallback：找第一张含"成绩/score"的表
        for t in schema:
            fields = t.get("fields") or []
            wide = infer_wide_mapping(t.get("name", ""), fields)
            if wide.subject_columns:
                return wide
        # 最终兜底：空宽表映射
        return ScoreSchemaMapping(mode="wide", table="", subject_columns={}, fields={})

    return ReportOrchestrator(
        execute_sql=execute_sql,
        resolve_schema=resolve_schema,
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
        user_id=user_id,
    )


@router.post("/batch-report")
@audit_access(datasource_id_arg="req.datasource_id", query_arg="req.question")
async def batch_report(
    req: BatchReportRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """按班级列表批量生成报告，返回每班摘要（不含全量 HTML，避免响应过大）。"""
    from src.agent.education.report_types import Audience, ReportSpec, ReportType
    from src.common.core.database import get_db_session

    rt_raw = (req.report_type or "").strip()
    question = (req.question or "").strip()
    if not rt_raw and not question:
        raise BadRequestException("请提供 report_type 或 question")
    if rt_raw and rt_raw not in _GENERATE_REPORT_ALLOWED_TYPES:
        raise BadRequestException(
            f"不支持的报告类型: {rt_raw}；当前支持: {', '.join(sorted(_GENERATE_REPORT_ALLOWED_TYPES))}"
        )

    with get_db_session() as session:
        assert_datasource_accessible(session, current_user, req.datasource_id, workspace_oid)
    ws_oid = req.workspace_oid if req.workspace_oid is not None else workspace_oid
    orch = _build_orchestrator(req.datasource_id, ws_oid, user_id=int(current_user.id))

    audience = Audience.DEFAULT
    if req.audience:
        try:
            audience = Audience(req.audience.strip())
        except ValueError as exc:
            raise BadRequestException(f"无效的受众: {req.audience}") from exc

    base_filters = {
        str(k): str(v)
        for k, v in (req.filters or {}).items()
        if v is not None and str(v).strip()
    }
    results = []
    for cls in req.class_names:
        cls_name = str(cls or "").strip()
        if not cls_name:
            continue
        if rt_raw:
            try:
                report_type = ReportType(rt_raw)
            except ValueError as exc:
                raise BadRequestException(f"无效的报告类型: {rt_raw}") from exc
            filters = {**base_filters, "class_name": cls_name}
            spec = ReportSpec(
                report_type=report_type,
                audience=audience,
                filters=filters,
                include_charts=bool(req.include_charts),
            )
            res = await orch.run_spec(spec)
            results.append(
                {
                    "class_name": cls_name,
                    "template_name": res.template_name,
                    "html_length": len(res.html or ""),
                    "report_type": res.spec.report_type.value,
                    "title": orch._title(spec),
                    "error": res.error,
                }
            )
        else:
            q = question.replace("{class}", cls_name)
            res = await orch.run(q, audience_hint=req.audience)
            results.append(
                {
                    "class_name": cls_name,
                    "template_name": res.template_name,
                    "html_length": len(res.html or ""),
                    "report_type": res.spec.report_type.value,
                    "title": orch._title(res.spec),
                    "error": res.error,
                }
            )
    return success_response({"items": results}, message=f"已批量生成 {len(results)} 份报告")


class DiagnosticReportRequest(BaseModel):
    datasource_id: int
    question: str = Field(..., min_length=1)
    audience: Optional[str] = None
    workspace_oid: Optional[int] = None


@router.post("/diagnostic-report")
@audit_access(datasource_id_arg="req.datasource_id", query_arg="req.question")
async def diagnostic_report(
    req: DiagnosticReportRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """确定性生成结构化诊断报告。"""
    from src.common.core.database import get_db_session

    with get_db_session() as session:
        assert_datasource_accessible(session, current_user, req.datasource_id, workspace_oid)
    ws_oid = req.workspace_oid if req.workspace_oid is not None else workspace_oid
    orch = _build_orchestrator(req.datasource_id, ws_oid, user_id=int(current_user.id))
    res = await orch.run(req.question, audience_hint=req.audience)
    return success_response(
        {
            "template_name": res.template_name,
            "report_type": res.spec.report_type.value,
            "html_length": len(res.html),
            "error": res.error,
        }
    )


# ---- 分析工具：按 ReportSpec 生成报告 --------------------------------------

#: 分析工具允许的全部 9 类报告
_GENERATE_REPORT_ALLOWED_TYPES = frozenset(
    {
        "class_overview",
        "grade_comparison",
        "subject_diagnosis",
        "student_profile",
        "trend_tracking",
        "tier_alert",
        "group_feature",
        "comprehensive",
        "diagnostic_report",
    }
)


class GenerateReportRequest(BaseModel):
    """分析工具：结构化参数生成单份报告。"""

    datasource_id: int = Field(..., description="数据源 ID")
    report_type: str = Field(..., description="报告类型，如 class_overview")
    audience: Optional[str] = Field(None, description="报告受众")
    filters: dict[str, str] = Field(default_factory=dict, description="筛选条件")
    include_charts: bool = Field(True, description="是否嵌入图表")
    workspace_oid: Optional[int] = Field(None, description="工作区 OID，鉴权用")


@router.post("/generate-report")
@audit_access(datasource_id_arg="req.datasource_id", query_arg="req.report_type")
async def generate_report(
    req: GenerateReportRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """按报告类型与 filters 确定性生成 HTML 报告（不经聊天 / LLM）。"""
    from src.agent.education.report_types import (
        Audience,
        ReportSpec,
        ReportType,
        report_type_label,
    )
    from src.common.core.database import get_db_session

    rt_raw = (req.report_type or "").strip()
    if rt_raw not in _GENERATE_REPORT_ALLOWED_TYPES:
        raise BadRequestException(
            f"不支持的报告类型: {rt_raw}；当前仅支持: {', '.join(sorted(_GENERATE_REPORT_ALLOWED_TYPES))}"
        )
    try:
        report_type = ReportType(rt_raw)
    except ValueError as exc:
        raise BadRequestException(f"无效的报告类型: {rt_raw}") from exc

    audience = Audience.DEFAULT
    if req.audience:
        try:
            audience = Audience(req.audience.strip())
        except ValueError as exc:
            raise BadRequestException(f"无效的受众: {req.audience}") from exc

    filters = {str(k): str(v) for k, v in (req.filters or {}).items() if v is not None and str(v).strip()}
    spec = ReportSpec(
        report_type=report_type,
        audience=audience,
        filters=filters,
        include_charts=bool(req.include_charts),
    )

    with get_db_session() as session:
        assert_datasource_accessible(session, current_user, req.datasource_id, workspace_oid)
    ws_oid = req.workspace_oid if req.workspace_oid is not None else workspace_oid
    orch = _build_orchestrator(req.datasource_id, ws_oid, user_id=int(current_user.id))
    res = await orch.run_spec(spec)
    title = orch._title(spec)
    err = res.error
    # 查无成绩时给出可操作提示，避免前端只看到全 0 / 「-」的空壳报告
    if not err and int((res.stats or {}).get("count") or 0) == 0:
        bits = [f"{k}={v}" for k, v in sorted(filters.items())]
        hint = "、".join(bits) if bits else "（未填写筛选条件）"
        err = (
            f"未查到成绩数据（筛选：{hint}）。"
            "请核对班级/学校/考试/科目名称是否与库内一致；"
            "也可先在聊天里用自然语言查出准确名称后再填到分析工具。"
        )
    return success_response(
        {
            "title": title,
            "html": res.html,
            "report_type": res.spec.report_type.value,
            "report_type_label": report_type_label(res.spec.report_type),
            "error": err,
        }
    )


class SaveReportHistoryRequest(BaseModel):
    """将分析工具生成的报告写入会话任务历史（轻量 Conversation + reports）。"""

    datasource_id: int = Field(..., description="数据源 ID")
    title: str = Field(..., min_length=1, description="报告标题")
    html: str = Field(..., min_length=1, description="报告 HTML")
    report_type: Optional[str] = Field(None, description="报告类型")
    report_type_label: Optional[str] = Field(None, description="报告类型中文名")
    question: Optional[str] = Field(None, description="写入 record 的问题摘要")
    workspace_oid: Optional[int] = Field(None, description="工作区 OID")


@router.post("/save-report-history")
async def save_report_history(
    req: SaveReportHistoryRequest,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """分析工具「保存到任务历史」：新建会话并写入一条带 reports 的记录。

    不改动聊天路径写入 reports 的既有逻辑，仅复用 chat CRUD。
    """
    from src.agent.education.report_types import report_type_label
    from src.agent.resource.tool.business import _load_datasource
    from src.chat.crud import chat as chat_crud
    from src.common.core.database import get_db_session

    title = (req.title or "").strip()
    html = (req.html or "").strip()
    if not title or not html:
        raise BadRequestException("title 与 html 不能为空")

    ws_oid = req.workspace_oid if req.workspace_oid is not None else workspace_oid
    with get_db_session() as session:
        ds = assert_datasource_accessible(session, current_user, req.datasource_id, ws_oid)
        try:
            _db_type, _cfg, ds_name = _load_datasource(req.datasource_id, ws_oid)
        except Exception:
            ds_name = getattr(ds, "name", None) or f"数据源 #{req.datasource_id}"
            _db_type = getattr(ds, "type", "") or ""

        rt = (req.report_type or "").strip()
        rt_label = (req.report_type_label or "").strip() or (
            report_type_label(rt) if rt else "学情报告"
        )
        conv_title = f"[分析工具] {title}"[:64]
        question = (req.question or "").strip() or f"分析工具生成：{title}"

        conversation = chat_crud.create_conversation(
            session=session,
            user_id=int(current_user.id),
            title=conv_title,
            datasource_id=req.datasource_id,
            datasource_name=str(ds_name or ""),
            db_type=str(_db_type or ""),
            oid=int(ws_oid),
        )
        report_item: dict[str, Any] = {
            "title": title,
            "html": html,
            "mode": "inline",
            "agent": "analysis_tool",
            "review_status": "pending",
        }
        if rt:
            report_item["report_type"] = rt
        if rt_label:
            report_item["report_type_label"] = rt_label

        record = chat_crud.create_conversation_record(
            session=session,
            conversation_id=int(conversation.id),
            user_id=int(current_user.id),
            question=question,
            is_success=True,
            agent_mode="analysis_tool",
            summary=f"已保存报告：{title}",
            reports=[report_item],
            workspace_oid=int(ws_oid),
        )
        return success_response(
            {
                "conversation_id": int(conversation.id),
                "record_id": int(record.id),
                "title": conv_title,
            },
            message="已保存到任务历史",
        )


def _parse_json_safe(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _report_history_item(
    record: Any,
    conversation: Any,
    *,
    include_html: bool = False,
) -> dict[str, Any]:
    reports = _parse_json_safe(getattr(record, "reports", None), []) or []
    first = reports[0] if isinstance(reports, list) and reports and isinstance(reports[0], dict) else {}
    title = str(first.get("title") or conversation.title or "").strip()
    html = str(first.get("html") or "") if include_html else ""
    item: dict[str, Any] = {
        "conversation_id": int(conversation.id),
        "record_id": int(record.id),
        "title": title,
        "conversation_title": str(conversation.title or ""),
        "report_type": str(first.get("report_type") or ""),
        "report_type_label": str(first.get("report_type_label") or ""),
        "datasource_id": conversation.datasource_id,
        "datasource_name": str(conversation.datasource_name or ""),
        "question": str(record.question or ""),
        "summary": str(getattr(record, "summary", None) or ""),
        "create_time": record.create_time.isoformat(sep=" ", timespec="seconds")
        if getattr(record, "create_time", None)
        else None,
        "html_length": len(str(first.get("html") or "")),
    }
    if include_html:
        item["html"] = html
    return item


@router.get("/report-history")
async def list_report_history(
    limit: int = 50,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """分析工具报告历史列表（复用 ConversationRecord，不建新表）。"""
    from src.chat.crud import chat as chat_crud
    from src.common.core.database import get_db_session

    lim = max(1, min(int(limit or 50), 200))
    with get_db_session() as session:
        pairs = chat_crud.list_analysis_tool_records(
            session=session,
            user_id=int(current_user.id),
            oid=int(workspace_oid),
            limit=lim,
        )
        items = [_report_history_item(rec, conv, include_html=False) for rec, conv in pairs]
    return success_response({"total": len(items), "items": items})


@router.get("/report-history/{record_id}")
async def get_report_history_detail(
    record_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """报告历史详情（含 HTML）。"""
    from common.exceptions.base import NotFoundException
    from src.chat.crud import chat as chat_crud
    from src.common.core.database import get_db_session

    with get_db_session() as session:
        record = chat_crud.get_record_by_id(session, int(record_id), int(current_user.id))
        if not record or record.agent_mode != "analysis_tool":
            raise NotFoundException("报告记录不存在")
        conversation = chat_crud.get_conversation_by_id(
            session,
            int(record.conversation_id),
            int(current_user.id),
            int(workspace_oid),
        )
        if not conversation:
            raise NotFoundException("报告会话不存在")
        return success_response(_report_history_item(record, conversation, include_html=True))


@router.delete("/report-history/{conversation_id}")
async def delete_report_history(
    conversation_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """软删除报告历史对应会话。"""
    from common.exceptions.base import NotFoundException
    from src.chat.crud import chat as chat_crud
    from src.common.core.database import get_db_session

    with get_db_session() as session:
        ok = chat_crud.delete_conversation(
            session=session,
            conversation_id=int(conversation_id),
            user_id=int(current_user.id),
            oid=int(workspace_oid),
        )
        if not ok:
            raise NotFoundException("报告会话不存在")
    return success_response({"conversation_id": int(conversation_id)}, message="已删除")


@router.get("/meta/options")
async def list_meta_options(
    datasource_id: int,
    school_name: Optional[str] = None,
    exam_name: Optional[str] = None,
    class_name: Optional[str] = None,
    subject: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """分析工具下拉：从数据源拉取学校/考试/班级/科目可选值（带教育权限）。"""
    from datasource.service.edu_permission import parse_edu_scope
    from src.common.core.database import get_db_session
    from src.system.crud.crud_user import get_user_by_id

    with get_db_session() as session:
        assert_datasource_accessible(session, current_user, datasource_id, workspace_oid)
        user = get_user_by_id(session, int(current_user.id))
        edu_scope = parse_edu_scope(user)

    orch = _build_orchestrator(datasource_id, workspace_oid, user_id=int(current_user.id))
    options = await _load_meta_options(
        orch,
        school_name=(school_name or "").strip() or None,
        exam_name=(exam_name or "").strip() or None,
        class_name=(class_name or "").strip() or None,
        subject=(subject or "").strip() or None,
        edu_scope=edu_scope,
    )
    return success_response(options)


def _sql_quote(val: str) -> str:
    return (val or "").replace("'", "''")


async def _distinct_col(execute_sql, sql: str) -> list[str]:
    try:
        result = await execute_sql(sql)
    except Exception:
        return []
    rows = result.get("rows") or []
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        v = str(row[0] if row[0] is not None else "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


async def _load_meta_options(
    orch: ReportOrchestrator,
    *,
    school_name: str | None,
    exam_name: str | None,
    class_name: str | None,
    subject: str | None,
    edu_scope: Any = None,
) -> dict[str, list[str]]:
    """按 edu schema 查 DISTINCT；SQL 经权限执行，并按 edu_scope 再收敛选项。"""
    from datasource.service.edu_permission import EduScope

    execute = orch._execute_sql
    mapping = await orch._resolve_schema()
    empty = {"schools": [], "exams": [], "classes": [], "subjects": []}
    if getattr(mapping, "source", None) != "config_edu":
        return empty

    scope = edu_scope if isinstance(edu_scope, EduScope) else EduScope()

    def _filters(*, exclude: set[str]) -> list[str]:
        parts: list[str] = []
        if school_name and "school" not in exclude:
            parts.append(f"sch.name LIKE '%{_sql_quote(school_name)}%'")
        if exam_name and "exam" not in exclude:
            parts.append(f"e.exam_name LIKE '%{_sql_quote(exam_name)}%'")
        if class_name and "class" not in exclude:
            parts.append(f"sc.class LIKE '%{_sql_quote(class_name)}%'")
        if subject and "subject" not in exclude:
            parts.append(f"sc.subject_name LIKE '%{_sql_quote(subject)}%'")
        return parts

    def _where(parts: list[str], extra: str) -> str:
        all_parts = [*parts, extra]
        return " WHERE " + " AND ".join(all_parts)

    # 必须经 tb_score，才能挂上 school_id / class 教育权限谓词
    score_from = (
        "FROM tb_score sc "
        "JOIN tb_school sch ON sc.school_id = sch.id "
        "JOIN tb_exam e ON sc.exam_id = e.id"
    )

    schools = await _distinct_col(
        execute,
        "SELECT DISTINCT sch.name AS v "
        f"{score_from}"
        + _where(
            _filters(exclude={"school"}),
            "sch.name IS NOT NULL AND CAST(sch.name AS TEXT) <> ''",
        )
        + " ORDER BY v LIMIT 500",
    )
    exams = await _distinct_col(
        execute,
        "SELECT DISTINCT e.exam_name AS v "
        f"{score_from}"
        + _where(
            _filters(exclude={"exam"}),
            "e.exam_name IS NOT NULL AND CAST(e.exam_name AS TEXT) <> ''",
        )
        + " ORDER BY v LIMIT 500",
    )
    classes = await _distinct_col(
        execute,
        "SELECT DISTINCT sc.class AS v "
        f"{score_from}"
        + _where(
            _filters(exclude={"class"}),
            "sc.class IS NOT NULL AND CAST(sc.class AS TEXT) <> ''",
        )
        + " ORDER BY v LIMIT 500",
    )
    subjects = await _distinct_col(
        execute,
        "SELECT DISTINCT sc.subject_name AS v "
        f"{score_from}"
        + _where(
            _filters(exclude={"subject"}),
            "sc.subject_name IS NOT NULL AND CAST(sc.subject_name AS TEXT) <> ''",
        )
        + " ORDER BY v LIMIT 500",
    )

    # 按账号 edu_scope 再收敛（双保险：名称配置 / 班级名单）
    if scope.school_name:
        sn = scope.school_name.strip()
        schools = [s for s in schools if s == sn or sn in s or s in sn]
        if not schools and sn:
            schools = [sn]
    if scope.class_names:
        allowed = {c.strip() for c in scope.class_names if c and str(c).strip()}
        if allowed:
            classes = [c for c in classes if c in allowed]
            if not classes:
                classes = sorted(allowed)

    return {
        "schools": schools,
        "exams": exams,
        "classes": classes,
        "subjects": subjects,
    }


@router.get("/dimensions")
async def list_dimensions() -> dict:
    """返回可用分析维度列表。"""
    from src.agent.education.aggregation import DIMENSIONS

    return success_response({"dimensions": list(DIMENSIONS)})


# ---- 成绩导入 --------------------------------------------------------------


def _read_upload_file(file: UploadFile) -> bytes:
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise BadRequestException("请上传 .xlsx 格式的 Excel 文件")
    data = file.file.read()
    if not data:
        raise BadRequestException("上传文件为空")
    return data


def _parse_import_type(import_type: str) -> str:
    t = (import_type or "").strip().lower()
    if t not in ("total", "detail"):
        raise BadRequestException("import_type 须为 total 或 detail")
    return t


@router.get("/score-import/templates/{import_type}")
async def download_score_import_template(
    import_type: str,
    current_user: UserResponse = Depends(get_current_user),
) -> FileResponse:
    _ = current_user
    from src.agent.education.score_import import template_path

    t = _parse_import_type(import_type)
    path = template_path(t)  # type: ignore[arg-type]
    if not path.is_file():
        raise BadRequestException("模板文件不存在")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.post("/score-import/preview")
async def preview_score_import(
    datasource_id: int = Form(...),
    import_type: str = Form(...),
    file: UploadFile = File(...),
    school_id: Optional[str] = Form(None),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.score_import import import_result_to_dict, preview_import
    from src.agent.resource.tool.business import _load_datasource
    from src.common.core.database import get_db_session
    from system.crud.crud_user import get_user_by_id

    t = _parse_import_type(import_type)
    file_bytes = _read_upload_file(file)

    with get_db_session() as session:
        assert_datasource_accessible(session, current_user, datasource_id, workspace_oid)
        user = get_user_by_id(session, int(current_user.id))
        scope = parse_edu_scope(user)

    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    result = preview_import(
        file_bytes,
        t,  # type: ignore[arg-type]
        scope,
        db_type,
        config,
        override_school_id=(school_id or "").strip(),
    )
    return success_response(import_result_to_dict(result))


@router.post("/score-import/execute")
async def execute_score_import(
    datasource_id: int = Form(...),
    import_type: str = Form(...),
    file: UploadFile = File(...),
    school_id: Optional[str] = Form(None),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.score_import import import_result_to_dict, import_scores
    from src.agent.resource.tool.business import _load_datasource
    from src.common.core.database import get_db_session
    from system.crud.crud_user import get_user_by_id

    t = _parse_import_type(import_type)
    file_bytes = _read_upload_file(file)

    with get_db_session() as session:
        assert_datasource_accessible(session, current_user, datasource_id, workspace_oid)
        user = get_user_by_id(session, int(current_user.id))
        scope = parse_edu_scope(user)

    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    result = import_scores(
        file_bytes,
        t,  # type: ignore[arg-type]
        scope,
        db_type,
        config,
        override_school_id=(school_id or "").strip(),
    )
    if result.error_rows:
        return error_response(
            code=400,
            message="导入校验未通过",
            data=import_result_to_dict(result),
        )

    alert_stats: dict = {}
    # 产品约定：总分 + 小题分都导完后才生成异常提醒（仅在小题分导入成功时扫描）
    if t == "detail":
        try:
            from src.agent.education.alert_service import scan_alerts_after_import

            with get_db_session() as session:
                alert_stats = scan_alerts_after_import(
                    session,
                    db_type=db_type,
                    config=config,
                    workspace_oid=int(workspace_oid),
                    datasource_id=int(datasource_id),
                    resolved_rows=list(result.resolved_rows or []),
                )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("post-import anomaly alert scan failed")

    payload = import_result_to_dict(result)
    if t == "total":
        payload["anomaly_alerts_pending"] = True
    if alert_stats:
        payload["anomaly_alerts"] = alert_stats
    return success_response(payload, message="成绩导入成功")


# ---- 异常提醒（校内待办）----------------------------------------------------


class AnomalyAlertConfirmPayload(BaseModel):
    note: Optional[str] = Field(None, max_length=512, description="可选处理说明")


@router.get("/anomaly-alerts")
async def list_anomaly_alerts(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """校长/老师查看本校（本班）异常提醒；教育局不可见。"""
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.alert_service import (
        alert_to_dict,
        can_access_anomaly_alerts,
        list_alerts,
    )
    from src.common.core.database import get_db_session
    from system.crud.crud_user import get_user_by_id

    with get_db_session() as session:
        user = get_user_by_id(session, int(current_user.id))
        scope = parse_edu_scope(user)
        if not can_access_anomaly_alerts(scope):
            return success_response(
                {
                    "accessible": False,
                    "total": 0,
                    "items": [],
                    "message": "教育局/学生账号不提供校内异常提醒",
                }
            )
        rows, total = list_alerts(
            session,
            scope,
            workspace_oid=int(workspace_oid),
            status=(status or "").strip() or None,
            limit=limit,
            offset=offset,
        )
        return success_response(
            {
                "accessible": True,
                "total": total,
                "items": [alert_to_dict(r) for r in rows],
            }
        )


@router.get("/anomaly-alerts/{alert_id}")
async def get_anomaly_alert(
    alert_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.alert_service import (
        alert_to_dict,
        can_access_anomaly_alerts,
        get_alert_for_scope,
    )
    from src.common.core.database import get_db_session
    from system.crud.crud_user import get_user_by_id

    with get_db_session() as session:
        user = get_user_by_id(session, int(current_user.id))
        scope = parse_edu_scope(user)
        if not can_access_anomaly_alerts(scope):
            return error_response(code=403, message="无权查看异常提醒")
        row = get_alert_for_scope(
            session, int(alert_id), scope, workspace_oid=int(workspace_oid)
        )
        if row is None:
            return error_response(code=404, message="异常提醒不存在或无权查看")
        return success_response(alert_to_dict(row))


@router.post("/anomaly-alerts/{alert_id}/confirm")
async def confirm_anomaly_alert(
    alert_id: int,
    payload: AnomalyAlertConfirmPayload | None = None,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.alert_service import (
        alert_to_dict,
        can_access_anomaly_alerts,
        confirm_alert,
    )
    from src.common.core.database import get_db_session
    from system.crud.crud_user import get_user_by_id

    note = (payload.note if payload else None) or ""
    with get_db_session() as session:
        user = get_user_by_id(session, int(current_user.id))
        scope = parse_edu_scope(user)
        if not can_access_anomaly_alerts(scope):
            return error_response(code=403, message="无权确认异常提醒")
        row = confirm_alert(
            session,
            int(alert_id),
            scope,
            workspace_oid=int(workspace_oid),
            user_id=int(current_user.id),
            note=note,
        )
        if row is None:
            return error_response(code=404, message="异常提醒不存在或无权确认")
        return success_response(alert_to_dict(row), message="已确认处理")
