"""ToolAction：让 LLM 以 JSON 形式选择并调用一个工具。

约定的 LLM 输出 schema（可包在 ```json 代码块里）::

    {
      "thoughts": "为什么选这个工具 / 下一步计划",
      "tool": "tool_name",
      "args": { "arg1": "...", "arg2": 123 }
    }

解析失败、工具不存在、参数不匹配、工具抛异常——一律返回
``ActionOutput(is_exe_success=False, ...)``，由 ConversableAgent 主循环
决定是否回灌失败原因并重试。

本 Action 一次只执行一个工具；多步 ReAct 循环由 ReActAgent 上层驱动（Phase B-3）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from src.agent.audit.tool_call_log import log_tool_call_fire_and_forget
from src.agent.core.action.base import Action, ActionOutput
from src.agent.resource.tool.base import ToolResult
from src.agent.resource.tool.builtin import TERMINATE_TOOL_NAME
from src.agent.resource.tool.pack import ToolNotFoundError, ToolPack
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

#: 同一会话内相同 (tool, args) 成功调用后，重复请求直接返回缓存，不再打库/重算。
_DEDUP_EXEMPT_TOOLS = frozenset({TERMINATE_TOOL_NAME})

_NEXT_TOOL_HINTS: dict[str, str] = {
    "fetch_subject_diagnosis_data_tool": (
        "fetch 子任务：`terminate`。**禁止** `build_diagnostic_report_data_tool(render=true)`；"
        "组装子任务：改调 `build_diagnostic_report_data_tool(render=true)`，**禁止**再 fetch"
    ),
    "build_diagnostic_report_data_tool": "`terminate`（报告已渲染）",
    "build_comprehensive_report_data_tool": "`terminate`（综合报告已渲染，含进步/退步 TOP 与学生档案）",
    "build_student_exam_report_data_tool": "`terminate`（学生考试报告已渲染）",
    "build_subject_diagnosis_sections_tool": "`terminate`（sections 默认已渲染 HTML）",
    "build_student_subject_diagnosis_tool": "`terminate`（报告已渲染）",
    "build_chart_option_tool": "`render_html_report` → `terminate`（科目诊断 sections 已含图表则跳过本工具）",
    "select_report_template_tool": (
        "若 comprehensive → `build_comprehensive_report_data_tool(class_name=...)` → `terminate`；"
        "科目诊断 → `build_subject_diagnosis_sections_tool(fetch_data=..., render=true)` → `terminate`"
    ),
    "compute_score_stats_tool": "`build_subject_diagnosis_sections_tool(fetch_data=..., render=true)` → `terminate`",
    "list_tables": "`describe_table` / `execute_sql`",
    "describe_table": "`execute_sql` / `sample_rows`",
}

#: 这些工具禁止 LLM 手填超长表格入参（易截断 JSON）；改由 bindings / 上游 SQL 注入。
_STRIP_TABLE_ARGS_TOOLS = frozenset(
    {
        "build_comprehensive_report_data_tool",
        "build_student_exam_report_data_tool",
    }
)
# records 等由上游注入；report_data / tool_runtime_ctx 必须保留 bindings，禁止 LLM 覆盖。
_STRIP_TABLE_ARG_KEYS = frozenset(
    {
        "records",
        "rows",
        "columns",
        "exec_result",
        "score_rows",
        "report_data",
        "tool_runtime_ctx",
    }
)


def _sanitize_report_tool_args(
    tool_name: str,
    args: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
    sub_task: str = "",
) -> dict[str, Any]:
    """综合/学生报告工具：丢掉 LLM 手抄的 records/rows，补全 class_name / student_name。"""
    out = dict(args)
    if tool_name in _STRIP_TABLE_ARGS_TOOLS:
        for k in _STRIP_TABLE_ARG_KEYS:
            out.pop(k, None)
    ctx = constraints if isinstance(constraints, dict) else {}
    if tool_name == "build_comprehensive_report_data_tool" and not str(out.get("class_name") or "").strip():
        classes = ctx.get("target_classes") or []
        if isinstance(classes, list) and classes:
            out["class_name"] = str(classes[0])
        else:
            m = re.search(r"((?:高|初)[一二三123]?\s*[（(]?\d+[）)]?\s*班)", sub_task or "")
            if m:
                out["class_name"] = m.group(1).replace(" ", "")
    if tool_name == "build_student_exam_report_data_tool" and not str(out.get("student_name") or "").strip():
        if str(out.get("student_id") or "").strip():
            out["student_name"] = str(out.get("student_id")).strip()
        else:
            stu = ctx.get("target_student")
            if stu:
                out["student_name"] = str(stu)
            else:
                m = re.search(
                    r"(学生\s*\d+|STU[\w-]+|2024_STU[\w-]+|student_name\s*[=：:]\s*([^\s,，)）]+)|student_id\s*[=：:]\s*([^\s,，)）]+))",
                    sub_task or "",
                    re.IGNORECASE,
                )
                if m:
                    out["student_name"] = (
                        (m.group(3) or m.group(2) or m.group(1) or "").replace(" ", "").strip()
                    )
    if tool_name == "build_student_exam_report_data_tool" and not str(out.get("class_name") or "").strip():
        classes = ctx.get("target_classes") or []
        if isinstance(classes, list) and classes:
            out["class_name"] = str(classes[0])
        else:
            m = re.search(r"((?:高|初)[一二三123]?\s*[（(]?\d+[）)]?\s*班)", sub_task or "")
            if m:
                out["class_name"] = m.group(1).replace(" ", "")
    return out


def _should_rescue_report_tool(sub_task: str, ai_message: str) -> str | None:
    """若应自动救援报告工具，返回工具名；否则 None。"""
    blob = f"{sub_task}\n{ai_message}".lower()
    student_keys = (
        "build_student_exam_report_data_tool",
        "student_exam_analysis",
        "学生考试分析",
        "该学生考试",
    )
    if any(k.lower() in blob for k in student_keys):
        return "build_student_exam_report_data_tool"
    comp_keys = (
        "build_comprehensive_report_data_tool",
        "comprehensive.html",
        "综合分析",
        "comprehensive",
    )
    if any(k.lower() in blob for k in comp_keys):
        return "build_comprehensive_report_data_tool"
    return None


def _should_rescue_comprehensive(sub_task: str, ai_message: str) -> bool:
    return _should_rescue_report_tool(sub_task, ai_message) == "build_comprehensive_report_data_tool"

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_TOOL_NAME_RE = re.compile(r"""tool\s*:\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?""", re.IGNORECASE)
_CLI_ARG_RE = re.compile(r"""--([A-Za-z_][A-Za-z0-9_]*)\s+("([^"]*)"|'([^']*)'|([^\s,}\]]+))""")


def tool_call_fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    """稳定指纹：忽略 None / 空串，便于判定“同参重复调用”。"""
    normalized = {k: v for k, v in args.items() if v is not None and v != ""}
    payload = json.dumps(
        {"tool": tool_name, "args": normalized},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _dedup_skip_message(tool_name: str, cached: ActionOutput) -> str:
    hint = _NEXT_TOOL_HINTS.get(tool_name, "换用其他工具或 `terminate`")
    body = (cached.observations or cached.content or "").strip()
    return (
        f"【重复调用已跳过】`{tool_name}` 与本轮会话中已成功执行的调用参数完全相同，"
        f"未重新执行，直接使用缓存结果。\n"
        f"请立即进入下一步：{hint}。**禁止**再次调用 `{tool_name}`。\n\n"
        f"{body}"
    )


def build_repeat_tool_warning(tool_name: str, streak: int) -> str:
    hint = _NEXT_TOOL_HINTS.get(
        tool_name,
        "换一个工具（例如 list_tables → describe_table → execute_sql），"
        "或在已有信息足够时直接调用 `terminate` 给出结论",
    )
    return (
        f"⚠️ 你已连续 {streak} 次调用 `{tool_name}` 仍未收敛。"
        f"请**立即换用下一步**：{hint}。"
    )


class ToolAction(Action):
    name = "tool_call"

    def __init__(self, tool_pack: ToolPack) -> None:
        if tool_pack is None:
            raise ValueError("ToolAction requires a ToolPack")
        self.tool_pack = tool_pack

    async def run(self, ai_message: str, **kwargs: Any) -> ActionOutput:
        started = time.perf_counter()
        agent_name = str(kwargs.get("agent_name") or "")
        round_idx = kwargs.get("round_idx")
        sub_task_index = kwargs.get("sub_task_index")

        def _audit(
            *,
            tool_name: str,
            success: bool,
            args: dict[str, Any] | None,
            result_preview: str,
        ) -> None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_tool_call_fire_and_forget(
                agent_name=agent_name,
                round_idx=round_idx if isinstance(round_idx, int) else None,
                sub_task_index=sub_task_index if isinstance(sub_task_index, int) else None,
                tool_name=tool_name or self.name,
                success=success,
                elapsed_ms=elapsed_ms,
                args=args,
                result_preview=result_preview,
            )

        def _extract_non_json_final_answer(raw_text: str) -> str | None:
            cleaned = _THINK_BLOCK_RE.sub("", str(raw_text or "")).strip()
            if not cleaned:
                return None
            # 有 JSON 结构痕迹时不要做 terminate 猜测，交给下一轮严格重试。
            if any(ch in cleaned for ch in "{}[]"):
                return None
            # 太短通常是噪声，不作为最终答案。
            if len(cleaned) < 24:
                return None
            return cleaned

        def _parse_tool_call_fallback(raw_text: str) -> dict[str, Any] | None:
            text = str(raw_text or "")
            if "[TOOL_CALL]" not in text and "tool:" not in text:
                return None
            cleaned = _THINK_BLOCK_RE.sub("", text)
            m_tool = _TOOL_NAME_RE.search(cleaned)
            if not m_tool:
                return None
            tool_name = m_tool.group(1)

            args: dict[str, Any] = {}
            for m in _CLI_ARG_RE.finditer(cleaned):
                key = m.group(1)
                value = m.group(3) or m.group(4) or m.group(5) or ""
                args[key] = value
            if not args:
                m_q = re.search(r"""question\s*:\s*("([^"]*)"|'([^']*)')""", cleaned, re.IGNORECASE)
                if m_q:
                    args["question"] = m_q.group(2) or m_q.group(3) or ""

            return {"tool": tool_name, "args": args}

        constraints = kwargs.get("constraints") if isinstance(kwargs.get("constraints"), dict) else {}
        sub_task = str(kwargs.get("sub_task") or "")

        async def _invoke_report_rescue(
            tool_name: str, *, thoughts: str | None = None
        ) -> ActionOutput | None:
            """JSON 截断 / 手填 records 失败时：空参调报告工具（数据来自上游/自查库）。"""
            if tool_name not in self.tool_pack:
                return None
            safe_args = _sanitize_report_tool_args(
                tool_name, {}, constraints=constraints, sub_task=sub_task
            )
            if tool_name == "build_student_exam_report_data_tool" and not str(
                safe_args.get("student_name") or ""
            ).strip():
                return None
            try:
                result = await self.tool_pack.invoke(tool_name, safe_args)
            except Exception:
                logger.exception("%s rescue invoke failed", tool_name)
                return None
            if isinstance(result.data, dict) and result.data.get("error") in {
                "missing input",
                "missing student_name",
            }:
                return None
            _audit(
                tool_name=tool_name,
                success=True,
                args=safe_args,
                result_preview=result.content,
            )
            return ActionOutput(
                is_exe_success=True,
                content=result.content,
                action=tool_name,
                thoughts=thoughts,
                observations=result.content,
                terminate=result.is_final,
                extra={
                    "tool_args": safe_args,
                    "tool_data": result.data,
                    "tool_extra": result.extra,
                    "rescued_report": True,
                },
            )

        try:
            parsed = parse_json_tolerant(ai_message)
        except ValueError as e:
            fallback_tool_call = _parse_tool_call_fallback(ai_message)
            if fallback_tool_call is not None:
                parsed = fallback_tool_call
            else:
                rescue_tool = _should_rescue_report_tool(sub_task, ai_message)
                if rescue_tool:
                    rescued = await _invoke_report_rescue(
                        rescue_tool,
                        thoughts=f"JSON 截断，自动改调 {rescue_tool}（不手填 records）",
                    )
                    if rescued is not None:
                        return rescued
                fallback_answer = _extract_non_json_final_answer(ai_message)
                if fallback_answer and TERMINATE_TOOL_NAME in self.tool_pack:
                    try:
                        result = await self.tool_pack.invoke(
                            TERMINATE_TOOL_NAME,
                            {"final_answer": fallback_answer},
                        )
                        _audit(
                            tool_name=TERMINATE_TOOL_NAME,
                            success=True,
                            args={"final_answer": fallback_answer},
                            result_preview=result.content,
                        )
                        return ActionOutput(
                            is_exe_success=True,
                            content=result.content,
                            action=TERMINATE_TOOL_NAME,
                            thoughts=None,
                            observations=result.content,
                            terminate=result.is_final,
                            extra={
                                "tool_args": {"final_answer": fallback_answer},
                                "tool_data": result.data,
                                "tool_extra": result.extra,
                            },
                        )
                    except Exception:
                        logger.exception("fallback terminate failed")
                msg = (
                    f"无法从 LLM 输出解析 JSON：{e}. "
                    "综合/学生报告请只调对应 build_*_report_data_tool（轻量 args），"
                    "**禁止**手填 records（会截断）。"
                )
                _audit(tool_name=self.name, success=False, args=None, result_preview=msg)
                return ActionOutput(
                    is_exe_success=False,
                    content=msg,
                    action=self.name,
                    thoughts=None,
                )

        if not isinstance(parsed, dict):
            # 兜底：模型直接返回一段报告正文/自然语言（字符串标量）时，
            # 作为最终答案 terminate，避免连续失败循环。
            if isinstance(parsed, str) and parsed.strip() and TERMINATE_TOOL_NAME in self.tool_pack:
                try:
                    result = await self.tool_pack.invoke(
                        TERMINATE_TOOL_NAME,
                        {"final_answer": parsed.strip()},
                    )
                    _audit(
                        tool_name=TERMINATE_TOOL_NAME,
                        success=True,
                        args={"final_answer": parsed.strip()},
                        result_preview=result.content,
                    )
                    return ActionOutput(
                        is_exe_success=True,
                        content=result.content,
                        action=TERMINATE_TOOL_NAME,
                        thoughts=None,
                        observations=result.content,
                        terminate=result.is_final,
                        extra={
                            "tool_args": {"final_answer": parsed.strip()},
                            "tool_data": result.data,
                            "tool_extra": result.extra,
                        },
                    )
                except Exception:
                    logger.exception("scalar fallback terminate failed")
            msg = "LLM 输出必须是 JSON 对象，不能是数组或标量。"
            _audit(tool_name=self.name, success=False, args=None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
            )

        thoughts = parsed.get("thoughts") or parsed.get("reasoning")
        tool_name = parsed.get("tool") or parsed.get("action")
        args = parsed.get("args") or parsed.get("arguments") or {}

        if not tool_name:
            # 兜底 1：模型把 render_html_report 的 args 直接当根对象输出
            # （形如 {"template_name": "...", "data": {...}, "title": "..."} 但漏了
            # tool 外壳）——这是教育报告组装最常见的格式错误。检测到
            # template_name/template_path/file_path/html 任一字段时，直接当作
            # render_html_report 的 args 调用，避免连续失败。
            _RENDER_TOOL = "render_html_report"
            if _RENDER_TOOL in self.tool_pack and any(
                isinstance(parsed.get(_k), str) and parsed.get(_k).strip()
                for _k in ("template_name", "template_path", "file_path", "html")
            ):
                try:
                    result = await self.tool_pack.invoke(_RENDER_TOOL, parsed)
                    _audit(
                        tool_name=_RENDER_TOOL,
                        success=True,
                        args=parsed,
                        result_preview=result.content,
                    )
                    return ActionOutput(
                        is_exe_success=True,
                        content=result.content,
                        action=_RENDER_TOOL,
                        thoughts=thoughts,
                        observations=result.content,
                        terminate=result.is_final,
                        extra={
                            "tool_args": parsed,
                            "tool_data": result.data,
                            "tool_extra": result.extra,
                        },
                    )
                except Exception:
                    logger.exception("missing-tool render_html_report rescue failed")

            # 兜底 2：部分模型会返回 {"final_answer": "..."} 这类对象但漏掉 tool，
            # 将其视作 terminate，避免整轮 ReAct 因格式细节失败。
            final_answer = (
                parsed.get("final_answer")
                or parsed.get("answer")
                or parsed.get("content")
            )
            # 进一步兜底：模型把报告正文/结果直接塞进 report/result/summary
            # 等字段（未走 tool 协议）时，取其字符串内容作为最终答案，避免连续失败。
            if not (isinstance(final_answer, str) and final_answer.strip()):
                for _k in ("report", "result", "summary", "output", "message"):
                    v = parsed.get(_k)
                    if isinstance(v, str) and v.strip():
                        final_answer = v
                        break
            if isinstance(final_answer, str) and final_answer.strip() and TERMINATE_TOOL_NAME in self.tool_pack:
                final_text = final_answer.strip()
                try:
                    result = await self.tool_pack.invoke(
                        TERMINATE_TOOL_NAME,
                        {"final_answer": final_text},
                    )
                    _audit(
                        tool_name=TERMINATE_TOOL_NAME,
                        success=True,
                        args={"final_answer": final_text},
                        result_preview=result.content,
                    )
                    return ActionOutput(
                        is_exe_success=True,
                        content=result.content,
                        action=TERMINATE_TOOL_NAME,
                        thoughts=thoughts,
                        observations=result.content,
                        terminate=result.is_final,
                        extra={
                            "tool_args": {"final_answer": final_text},
                            "tool_data": result.data,
                            "tool_extra": result.extra,
                        },
                    )
                except Exception:
                    logger.exception("missing-tool fallback terminate failed")
            msg = "LLM 输出缺少 `tool` 字段。"
            _audit(tool_name=self.name, success=False, args=args if isinstance(args, dict) else None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )

        if not isinstance(args, dict):
            msg = "`args` 必须是 JSON 对象。"
            _audit(tool_name=str(tool_name), success=False, args=None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )

        tool_name_str = str(tool_name)
        # 综合/学生报告：丢掉手填 records（易截断），只保留 class_name 等轻量参数
        if tool_name_str in _STRIP_TABLE_ARGS_TOOLS:
            args = _sanitize_report_tool_args(
                tool_name_str, args, constraints=constraints, sub_task=sub_task
            )

        cache: dict[str, ActionOutput] | None = kwargs.get("tool_call_cache")
        if (
            isinstance(cache, dict)
            and tool_name_str not in _DEDUP_EXEMPT_TOOLS
        ):
            fp = tool_call_fingerprint(tool_name_str, args)
            cached = cache.get(fp)
            if cached is not None:
                skip_msg = _dedup_skip_message(tool_name_str, cached)
                _audit(
                    tool_name=tool_name_str,
                    success=True,
                    args=args,
                    result_preview=skip_msg,
                )
                return ActionOutput(
                    is_exe_success=True,
                    content=skip_msg,
                    action=tool_name_str,
                    thoughts=thoughts,
                    observations=skip_msg,
                    terminate=False,
                    extra={
                        **dict(cached.extra or {}),
                        "tool_args": dict(args),
                        "deduplicated": True,
                    },
                )

        try:
            result: ToolResult = await self.tool_pack.invoke(tool_name_str, args)
        except ToolNotFoundError:
            available = ", ".join(self.tool_pack.names()) or "（空）"
            msg = f"未知工具：{tool_name}。可用工具：{available}"
            _audit(tool_name=str(tool_name), success=False, args=args, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )
        except TypeError as e:
            msg = f"调用工具 {tool_name} 参数不匹配：{e}"
            _audit(tool_name=str(tool_name), success=False, args=args, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )
        except Exception as e:
            logger.exception("Tool %s raised", tool_name)
            msg = f"工具 {tool_name} 执行异常：{e}"
            _audit(tool_name=str(tool_name), success=False, args=args, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )

        _audit(
            tool_name=tool_name_str,
            success=True,
            args=args,
            result_preview=result.content,
        )
        action_out = ActionOutput(
            is_exe_success=True,
            content=result.content,
            action=tool_name_str,
            thoughts=thoughts,
            observations=result.content,
            terminate=result.is_final,
            extra={
                "tool_args": dict(args),
                "tool_data": result.data,
                "tool_extra": result.extra,
            },
        )
        if (
            isinstance(cache, dict)
            and tool_name_str not in _DEDUP_EXEMPT_TOOLS
            and not result.is_final
        ):
            cache[tool_call_fingerprint(tool_name_str, args)] = action_out
        return action_out
