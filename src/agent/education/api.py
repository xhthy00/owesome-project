"""教育学情配置 API（Phase 3）。

提供阈值配置的读取 / 更新 / 重置端点，供管理台 UI 调用。
持久化目前为进程内覆盖（见 ``config_store``）；Phase 4 再接入工作区 JSON / DB。

端点：
- ``GET /api/v1/education/report-config`` — 读取当前生效阈值；
- ``PUT /api/v1/education/report-config`` — 部分更新阈值；
- ``POST /api/v1/education/report-config/reset`` — 清除覆盖，回到环境默认。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from common.schemas.response import success_response
from src.agent.education import config_store
from src.agent.education.orchestrator import ReportOrchestrator
from src.agent.education.schema_mapping import (
    ScoreSchemaMapping,
    infer_normalized_mapping,
    infer_wide_mapping,
    load_schema_from_config,
)

router = APIRouter(prefix="/education", tags=["education"])


class ReportConfigPayload(BaseModel):
    """阈值配置 payload；所有字段可选，仅传需要更新的。"""

    pass_threshold: Optional[float] = Field(None, ge=0, description="及格线，默认 60")
    excellent_threshold: Optional[float] = Field(None, ge=0, description="优秀线，默认 85")
    default_full_score: Optional[float] = Field(None, ge=1, description="满分兜底，默认 100")
    critical_margin: Optional[float] = Field(None, ge=0, description="临界生判定半径，默认 5")
    regression_threshold: Optional[float] = Field(
        None,
        description="退步判定阈值（负数，如 -10 表示降幅 ≥10 分算退步）",
    )


@router.get("/report-config")
async def get_report_config() -> dict:
    cfg = config_store.get_config()
    return success_response(
        {
            "pass_threshold": cfg.pass_threshold,
            "excellent_threshold": cfg.excellent_threshold,
            "default_full_score": cfg.default_full_score,
            "critical_margin": cfg.critical_margin,
            "regression_threshold": cfg.regression_threshold,
        }
    )


@router.put("/report-config")
async def update_report_config(payload: ReportConfigPayload) -> dict:
    cfg = config_store.update_config(payload.model_dump(exclude_none=True))
    return success_response(
        {
            "pass_threshold": cfg.pass_threshold,
            "excellent_threshold": cfg.excellent_threshold,
            "default_full_score": cfg.default_full_score,
            "critical_margin": cfg.critical_margin,
            "regression_threshold": cfg.regression_threshold,
        },
        message="配置已更新",
    )


@router.post("/report-config/reset")
async def reset_report_config() -> dict:
    config_store.reset_config()
    return success_response(None, message="已恢复默认配置")


# ---- 批量报告（Phase 4） --------------------------------------------------


class BatchReportRequest(BaseModel):
    """按班级列表批量生成报告。"""

    datasource_id: int = Field(..., description="数据源 ID")
    question: str = Field(
        ..., min_length=1, description="报告问题模板，如「生成{class}期中成绩分析报告」"
    )
    class_names: list[str] = Field(..., min_length=1, description="班级名列表")
    audience: Optional[str] = Field(None, description="报告受众")
    workspace_oid: Optional[int] = Field(None, description="工作区 OID，鉴权用")


def _build_orchestrator(datasource_id: int, workspace_oid: int | None) -> ReportOrchestrator:
    """用真实数据源回调构造 ReportOrchestrator。"""
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.db.db import execute_sql as db_execute_sql, get_schema_info

    db_type, config, _ds_name = _load_datasource(datasource_id, workspace_oid)

    async def execute_sql(sql: str) -> dict:
        success, _msg, result = db_execute_sql(db_type=db_type, config=config, sql=sql)
        if not success or not isinstance(result, dict):
            return {"columns": [], "rows": [], "row_count": 0}
        return {
            "columns": result.get("columns") or [],
            "rows": result.get("rows") or [],
            "row_count": result.get("row_count") or 0,
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

    return ReportOrchestrator(execute_sql=execute_sql, resolve_schema=resolve_schema)


@router.post("/batch-report")
async def batch_report(req: BatchReportRequest) -> dict:
    """按班级列表批量生成报告，返回每班的渲染摘要（不含全量 HTML，避免响应过大）。"""
    orch = _build_orchestrator(req.datasource_id, req.workspace_oid)
    results = []
    for cls in req.class_names:
        question = req.question.replace("{class}", cls)
        res = await orch.run(question, audience_hint=req.audience)
        results.append(
            {
                "class_name": cls,
                "template_name": res.template_name,
                "html_length": len(res.html),
                "report_type": res.spec.report_type.value,
                "error": res.error,
            }
        )
    return success_response({"items": results}, message=f"已批量生成 {len(results)} 份报告")
