"""业务工具集：DataAnalyst 在 ReAct 循环里可用的六把刀。

设计约束（回顾 PR 决策）：
- ``datasource_id`` / ``user_id`` 由 ToolPack.bindings 注入，LLM 不可决定；
- 每个工具内部开自己的短事务（``with get_db_session() as s``），不跨线程；
- SQL / 库访问一律使用现有同步函数，通过 ``FunctionTool`` 自动 ``asyncio.to_thread``；
- **工具内的"业务失败"（SQL 执行报错、表不存在、……）不抛异常**：返回正常
  ``ToolResult``，让 LLM 在 ReAct observation 里看到错误并自修正；只有"框架级
  错误"（找不到数据源、参数非法到无法执行）才抛，交给 ToolAction 归类为
  ``is_exe_success=False`` 触发主循环重试。
"""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any

from src.agent.resource.tool.base import ToolResult
from src.agent.resource.tool.builtin import TerminateTool
from src.agent.resource.tool.calc import calculate
from src.agent.resource.tool.function_tool import FunctionTool, tool
from src.agent.resource.tool.pack import ToolPack

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")

SAMPLE_ROWS_HARD_CAP = 100
SAMPLE_ROWS_DEFAULT = 3
SAMPLE_ROWS_LLM_MAX = 10  # prompt 里对 LLM 建议的上限；服务端再 clamp 到 HARD_CAP
EXECUTE_SQL_PREVIEW_ROWS = 20


def _validate_read_only_select(sql: str, db_type: str) -> None:
    """确保 ``sql`` 是**单条** SELECT，AST 内不含任何写操作/DDL/UNION 绕过。

    相比 :func:`check_sql_read` 更严格：
    - 必须恰好 1 条语句（防 ``;`` 注入）；
    - 顶层必须是 ``exp.Select``（防 ``WITH`` / CTE 隐藏 INSERT 等）；
    - 全部子 AST 节点中不得出现 INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/MERGE/COPY；
    - parse 失败时**拒绝**而不是 fallback allow——工具侧是可控入口，宁可错杀。

    仅供 :func:`sample_rows` 在拼 WHERE 时用；不影响 :func:`execute_sql` 既有流程。
    """
    from sqlglot import expressions as exp
    from sqlglot import parse

    dialect = "mysql" if db_type == "mysql" else None
    try:
        statements = parse(sql, dialect=dialect)
    except Exception as e:
        raise ValueError(f"SQL 解析失败：{e}") from e
    if not statements or len(statements) != 1:
        raise ValueError("必须是恰好一条 SQL 语句")
    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise ValueError(f"必须是 SELECT 语句，实际是 {type(stmt).__name__}")

    write_types = (
        exp.Insert, exp.Update, exp.Delete,
        exp.Create, exp.Drop, exp.Alter,
        exp.Merge, exp.Copy,
    )
    for wt in write_types:
        if stmt.find(wt) is not None:
            raise ValueError(f"禁止使用写操作 {wt.__name__}")


def _load_datasource(
    datasource_id: int,
    workspace_oid: int | None = None,
) -> tuple[str, dict[str, Any], str]:
    """返回 (db_type, decrypted_config, datasource_name)。

    仅在"数据源不存在"或归属工作空间不匹配时抛 ValueError——这属于调用方传入的
    binding 错误，不是业务错误。其余数据库访问错误由调用方处理。
    """
    from src.common.core.database import get_db_session
    from src.common.utils.aes import decrypt_conf
    from src.datasource.crud import crud_datasource

    with get_db_session() as session:
        ds = crud_datasource.get_datasource_by_id(session, datasource_id)
        if ds is None:
            raise ValueError(f"datasource not found: id={datasource_id}")
        if workspace_oid is not None and int(ds.oid) != int(workspace_oid):
            raise ValueError(
                f"datasource {datasource_id} does not belong to workspace {workspace_oid}"
            )
        config = decrypt_conf(ds.configuration) if ds.configuration else {}
        return ds.type, config, ds.name


def _safe_identifier(name: str, db_type: str) -> str:
    """校验标识符只含 ``[A-Za-z0-9_.]``，按库类型加引号。"""
    if not name or not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"invalid table name: {name!r}")
    quote = '"' if db_type == "pg" else "`"
    return ".".join(f"{quote}{part}{quote}" for part in name.split("."))


def _format_rows_as_markdown(columns: list[str], rows: list[list[Any]], max_rows: int) -> str:
    if not columns:
        return "（无列）"
    if not rows:
        return "| " + " | ".join(columns) + " |\n| " + " | ".join(["---"] * len(columns)) + " |\n（0 行）"
    shown = rows[:max_rows]
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = "\n".join("| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in shown)
    suffix = "" if len(rows) <= max_rows else f"\n\n（仅展示前 {max_rows} 行，共 {len(rows)} 行）"
    return f"{header}\n{sep}\n{body}{suffix}"


@tool()
def list_tables(datasource_id: int, workspace_oid: int | None = None) -> ToolResult:
    """列出当前数据源的所有表。

    Returns:
        每项含 name / comment；供 LLM 决定进一步要 describe 哪张表。
    """
    from src.datasource.db.db import get_schema_info

    db_type, config, ds_name = _load_datasource(datasource_id, workspace_oid)
    schema = get_schema_info(db_type, config)
    items = [{"name": t["name"], "comment": t.get("comment", "")} for t in schema]

    if not items:
        content = f"数据源 `{ds_name}` 暂无可见表。"
    else:
        lines = [f"数据源 `{ds_name}` 共 {len(items)} 张表："]
        for it in items:
            suffix = f" — {it['comment']}" if it["comment"] else ""
            lines.append(f"- {it['name']}{suffix}")
        content = "\n".join(lines)

    return ToolResult(content=content, data=items)


#: ``find_related_tables`` token 抽取——混合中英：ASCII ``\w`` + 中日韩 Unicode 范围。
#: 单字符的 token 太容易误命中，过滤掉长度 < 2 的。
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_FIND_RELATED_DEFAULT_LIMIT = 10
_FIND_RELATED_HARD_CAP = 20
_REPORT_MAX_HTML_LEN = 500_000
#: 允许 ``/`` 以支持 ``education/class_overview.html`` 这类子目录模板；
#: 路径穿越由 ``_resolve`` 里的 ``relative_to`` 校验兜底。
_REPORT_TEMPLATE_SAFE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_REPORT_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def _tokenize_question(question: str) -> list[str]:
    """把自然语言问题切成**中英兼顾**的匹配 token 集合。

    - ASCII 连续段（``user`` / ``order_id``）按 word；
    - 中文连续段（``学生成绩``）拆成 2-gram（``学生`` / ``生成`` / ``成绩``），
      因为 LLM 问"数学成绩"时表里可能只叫"成绩"，整段匹配太严；
    - 长度 < 2 的 token 丢弃（防"的"/"是"/"a" 之类噪声命中所有表）。
    """
    raw: list[str] = []
    for m in _TOKEN_RE.finditer(question or ""):
        piece = m.group(0)
        if not piece:
            continue
        if piece.isascii():
            if len(piece) >= 2:
                raw.append(piece.lower())
        else:
            # 中文段：整体 + 所有长度 2 的滑窗
            if len(piece) >= 2:
                raw.append(piece)
            for i in range(len(piece) - 1):
                raw.append(piece[i : i + 2])
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _score_table_against_tokens(table: dict[str, Any], tokens: list[str]) -> tuple[int, list[str]]:
    """给一张表打分 = 命中的 token 数。返回 (score, matched_tokens)。"""
    hay_parts: list[str] = [
        str(table.get("name") or "").lower(),
        str(table.get("comment") or "").lower(),
    ]
    for f in table.get("fields") or []:
        hay_parts.append(str(f.get("name") or "").lower())
        hay_parts.append(str(f.get("comment") or "").lower())
    hay = " ".join(hay_parts)

    matched: list[str] = []
    for tok in tokens:
        needle = tok.lower() if tok.isascii() else tok
        if needle and needle in hay:
            matched.append(tok)
    return len(matched), matched


def _report_base_dir() -> Path:
    return Path(__file__).resolve().parents[4]


def _report_template_dir() -> Path:
    return _report_base_dir() / "src" / "agent" / "resource" / "templates"


def _sanitize_report_html(raw_html: str) -> str:
    html = raw_html
    # 保留 <script> 以支持 HTML 报告中的图表渲染逻辑（ECharts/G2 等）。
    # 这里只做最小风险清洗：去掉内联事件与 javascript: URL。
    html = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", html)
    html = re.sub(r"(?i)\s+on[a-z]+\s*=\s*[^\s>]+", "", html)
    html = re.sub(r"""(?i)(href|src)\s*=\s*(['"])\s*javascript:[^'"]*\2""", r'\1="#"', html)
    return html


#: 班级总览：内联 HTML（非模板）也注入统一表格/文字卡片样式
_CLASS_OVERVIEW_POLISH_CSS = """
:root{
  --edu-primary:#1677ff;--edu-primary-soft:#e8f3ff;--edu-primary-bg:#e6f4ff;
  --edu-text-lv1:rgba(0,0,0,.88);--edu-text-lv2:rgba(0,0,0,.65);--edu-text-lv3:rgba(0,0,0,.45);
  --edu-border:#e8edf3;--edu-surface:#f7f9fc;
}
.edu-table-wrap{overflow-x:auto;margin:8px 0 12px;border:1px solid var(--edu-border);border-radius:12px;background:#fff}
table,.edu-table{width:100%;border-collapse:collapse;font-size:13px;min-width:420px}
table th,table td,.edu-table th,.edu-table td{
  border:none;border-bottom:1px solid var(--edu-border);padding:11px 14px;text-align:left;vertical-align:middle;color:var(--edu-text-lv1)
}
table thead th,.edu-table thead th,table tr:first-child th{
  background:linear-gradient(180deg,#f3f8ff 0%,var(--edu-primary-bg) 100%);color:#3b6fb8;font-weight:650;white-space:nowrap;font-size:12.5px
}
table tbody tr:nth-child(even) td,.edu-table tbody tr:nth-child(even) td{background:#fafcfe}
table tbody tr:hover td,.edu-table tbody tr:hover td{background:#f0f7ff}
table tbody tr:last-child td,.edu-table tbody tr:last-child td{border-bottom:none}
.prose-card,.edu-prose-card{
  margin:0 0 12px;padding:14px 16px;line-height:1.8;color:var(--edu-text-lv2);
  background:linear-gradient(135deg,#fafcff 0%,#f7f9fc 100%);border:1px solid var(--edu-border);
  border-radius:12px;border-left:3px solid var(--edu-primary);box-shadow:0 1px 2px rgba(16,24,40,.03)
}
.prose-card strong,.edu-prose-card strong{color:var(--edu-text-lv1);font-weight:650}
.edu-rec-list,ol.edu-rec-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px;counter-reset:rec}
.edu-rec-list>li,ol.edu-rec-list>li{
  position:relative;margin:0;padding:14px 16px 14px 52px;line-height:1.75;color:var(--edu-text-lv2);
  background:#fff;border:1px solid var(--edu-border);border-radius:12px;box-shadow:0 1px 2px rgba(16,24,40,.03);
  counter-increment:rec
}
.edu-rec-list>li::before,ol.edu-rec-list>li::before{
  content:counter(rec);position:absolute;left:14px;top:14px;width:26px;height:26px;border-radius:8px;
  background:var(--edu-primary-soft);color:var(--edu-primary);font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center
}
""".strip()

_CLASS_OVERVIEW_POLISH_JS = """
(function(){
  if(window.__eduClassOverviewPolished) return;
  window.__eduClassOverviewPolished=true;
  document.querySelectorAll('table').forEach(function(t){
    t.classList.add('edu-table');
    if(t.closest('.edu-table-wrap')) return;
    var w=document.createElement('div');
    w.className='edu-table-wrap';
    t.parentNode.insertBefore(w,t);
    w.appendChild(t);
  });
  document.querySelectorAll('ol').forEach(function(ol){
    if(ol.classList.contains('edu-rec-list')) return;
    if(ol.querySelector('li') && ol.closest('body')) ol.classList.add('edu-rec-list');
  });
  document.querySelectorAll('p').forEach(function(p){
    if(p.closest('.edu-table-wrap,.edu-kpi,.edu-badge')) return;
    if(p.classList.contains('prose-card')||p.classList.contains('edu-prose-card')) return;
    var t=(p.textContent||'').trim();
    if(t.length<40) return;
    p.classList.add('edu-prose-card');
  });
})();
""".strip()


def _looks_like_class_overview_html(html: str, *, template: str = "", title: str = "") -> bool:
    tpl = (template or "").replace("\\", "/")
    if "class_overview" in tpl:
        return True
    blob = f"{title}\n{html[:3000]}"
    return ("班级总览" in blob) or ("class_overview" in blob.lower())


def _polish_class_overview_html(html: str, *, template: str = "", title: str = "") -> str:
    """为班级总览（含 LLM 内联 HTML）注入表格/文字卡片样式。"""
    if not html or not _looks_like_class_overview_html(html, template=template, title=title):
        return html
    if "edu-class-overview-polish" in html:
        return html
    style = f'<style id="edu-class-overview-polish">{_CLASS_OVERVIEW_POLISH_CSS}</style>'
    script = f'<script id="edu-class-overview-polish-js">{_CLASS_OVERVIEW_POLISH_JS}</script>'
    out = html
    if re.search(r"(?i)</head>", out):
        out = re.sub(r"(?i)</head>", style + "</head>", out, count=1)
    else:
        out = style + out
    if re.search(r"(?i)</body>", out):
        out = re.sub(r"(?i)</body>", script + "</body>", out, count=1)
    else:
        out = out + script
    return out


def _render_template_html(template_name: str, data: dict[str, Any]) -> str:
    from src.agent.education.subject_diagnosis import coerce_report_table_fields

    data = coerce_report_table_fields(data or {})
    template_dir = _report_template_dir()
    template_dir.mkdir(parents=True, exist_ok=True)
    if not _REPORT_TEMPLATE_SAFE_RE.match(template_name):
        raise ValueError("template_name 仅允许字母/数字/._-")

    template_dir_resolved = template_dir.resolve()

    def _resolve(candidate: str) -> Path:
        p = (template_dir / candidate).resolve()
        p.relative_to(template_dir_resolved)
        return p

    candidates: list[str] = [template_name]
    # 兼容无后缀模板名：传 `score_analysis_report` 时自动尝试 `.html`。
    if "." not in Path(template_name).name:
        candidates.append(f"{template_name}.html")

    path: Path | None = None
    for candidate in candidates:
        p = _resolve(candidate)
        if p.is_file():
            path = p
            break
    if path is None:
        raise ValueError(f"模板不存在: {template_name}")
    raw = path.read_text(encoding="utf-8")

    # Phase 3：优先用 Jinja2 渲染，支持 {% for %}/{% if %}/过滤器与 |safe；
    # 模板若仅含 {{KEY}} 占位符（无 Jinja 控制结构），Jinja2 同样能正确渲染，
    # 因此统一走 Jinja2。解析失败（极少见，如模板含非法 Jinja 语法）时回退
    # 到原 regex 替换，保证旧模板不破。
    try:
        from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

        env = Environment(
            loader=None,
            autoescape=False,  # 报告 HTML 由 _sanitize_report_html 统一消毒
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        return env.from_string(raw).render(**{str(k): v for k, v in data.items()})
    except TemplateSyntaxError:
        # 退回 Phase 1 的纯占位符替换
        def _replace(m: re.Match[str]) -> str:
            key = m.group(1)
            return str(data.get(key, ""))

        return _REPORT_PLACEHOLDER_RE.sub(_replace, raw)
    except Exception:
        # StrictUndefined 对缺失变量抛错——LLM 漏填字段时不致命，回退 regex
        # 用空串兜底，避免整份报告渲染失败。
        def _replace(m: re.Match[str]) -> str:
            key = m.group(1)
            return str(data.get(key, ""))

        return _REPORT_PLACEHOLDER_RE.sub(_replace, raw)


def _parse_report_data(data: Any) -> dict[str, Any]:
    from src.agent.education.subject_diagnosis import coerce_report_table_fields

    if data is None:
        return {}
    if isinstance(data, dict):
        return coerce_report_table_fields(data)
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except Exception:
            return {}
        return coerce_report_table_fields(parsed) if isinstance(parsed, dict) else {}
    return {}


def _read_report_file(file_path: str) -> str:
    base = _report_base_dir().resolve()
    raw = (file_path or "").strip()
    if not raw:
        raise ValueError("文件不存在: ")

    p = Path(raw).expanduser()
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p.resolve())
    else:
        # 先按工作区相对路径找。
        candidates.append((base / p).resolve())
        # 兼容调用方把模板文件名误传到 file_path：回退到模板目录查找。
        if len(p.parts) == 1:
            candidates.append((_report_template_dir() / p.name).resolve())

    for target in candidates:
        try:
            target.relative_to(base)
        except Exception:
            continue
        if target.is_file():
            return target.read_text(encoding="utf-8")

    raise ValueError(
        f"文件不存在: {file_path}。"
        "file_path 仅用于读取工作区里**已有**的 HTML 文件，不能当作输出路径捏造。"
        "生成报告请改用 template_name（如 education/class_overview.html）+ data。"
    )


#: 有专用 build_* 工具的学情模板——禁止再用 render_html_report 手填 data（易截断空表）。
_EDU_TEMPLATE_DEDICATED_BUILDERS: dict[str, tuple[str, str]] = {
    "comprehensive": (
        "build_comprehensive_report_data_tool(class_name=...)",
        "use_build_comprehensive_report_data_tool",
    ),
    "subject_diagnosis": (
        "build_subject_diagnosis_sections_tool(school_name=..., subject_name=..., render=true)",
        "use_build_subject_diagnosis_sections_tool",
    ),
    "diagnostic_report": (
        "build_diagnostic_report_data_tool(scope_label=..., exam_name=..., subject_name=..., render=true)",
        "use_build_diagnostic_report_data_tool",
    ),
    "student_exam_analysis": (
        "build_student_exam_report_data_tool(student_name=..., class_name=...)",
        "use_build_student_exam_report_data_tool",
    ),
    "student_subject_diagnosis": (
        "build_student_subject_diagnosis_tool(student_id=..., subject_name=..., render=true)",
        "use_build_student_subject_diagnosis_tool",
    ),
    "tier_alert": (
        "build_tier_alert_report_data_tool(class_name=..., subject_name=..., render=true)",
        "use_build_tier_alert_report_data_tool",
    ),
    "group_feature": (
        "build_group_feature_report_data_tool(school_name=..., dimension=class, render=true)",
        "use_build_group_feature_report_data_tool",
    ),
    "class_overview": (
        "build_class_overview_report_data_tool(class_name=..., subject_name=..., render=true)",
        "use_build_class_overview_report_data_tool",
    ),
    "trend_tracking": (
        "build_trend_tracking_report_data_tool(class_name=..., subject_name=..., render=true)",
        "use_build_trend_tracking_report_data_tool",
    ),
}


def _edu_template_redirect(template: str, html: str = "") -> ToolResult | None:
    """若命中有专用 builder 的学情模板，返回改道提示；否则 None。"""
    tpl_norm = (template or "").replace("\\", "/").lower().strip()
    # 用文件名精确匹配，避免 student_subject_diagnosis 误中 subject_diagnosis
    stem = Path(tpl_norm).stem
    for key, (hint_tool, err_code) in _EDU_TEMPLATE_DEDICATED_BUILDERS.items():
        if stem == key or tpl_norm.endswith(f"/{key}.html") or tpl_norm == f"{key}.html":
            return ToolResult(
                content=(
                    f"该学情报告请改调 `{hint_tool}`："
                    "工具会自动读取上游 fetch / SQL 明细并渲染。"
                    f"**禁止**用 render_html_report 手填 `{key}` 模板或自写同结构 HTML"
                    "（易 JSON 截断导致空 KPI / 空表）。"
                ),
                data={"error": err_code},
            )
    # comprehensive 也可能被做成纯 inline HTML（无 template_name）
    if html and "每位学生详细档案" in html and "进步最快" in html:
        hint_tool, err_code = _EDU_TEMPLATE_DEDICATED_BUILDERS["comprehensive"]
        return ToolResult(
            content=(
                f"综合分析报告请改调 `{hint_tool}`："
                "工具会自动读取完整 SQL 学生明细并生成进步/退步 TOP5 与每位学生档案。"
                "**禁止**用 render_html_report 手填 comprehensive 模板或自写 HTML"
                "（易导致第五节变成班级汇总、第九节学生档案为空）。"
            ),
            data={"error": err_code},
        )
    return None


@tool()
def render_html_report(
    html: str = "",
    title: str = "Report",
    template_name: str = "",
    template_path: str = "",
    data: dict[str, Any] | None = None,
    file_path: str = "",
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
) -> ToolResult:
    """生成 HTML 报告载荷（DB-GPT html_interpreter 风格）。

    三种模式（优先级从高到低）：
    1. template_name/template_path + data：读取模板并替换 ``{{KEY}}``
       （仅限无专用 build_* 工具的模板，如 class_overview）；
    2. file_path：读取工作区内**已有** HTML 文件（禁止捏造输出路径如 data_analyst/xxx.html）；
    3. html：直接使用传入 HTML 字符串。

    综合 / 科目诊断 / 全市诊断 / 学生考试分析等有专用工具的学情模板，
    **禁止**本工具手填——请改用对应 ``build_*_report*_tool``。

    ``class_overview`` 渲染时若未手填 ``STUDENT_ARCHIVE_TABLE``，会自动从上游
    SQL 成绩明细组装「每位学生详细档案与个性化建议」。
    """
    template = (template_name or "").strip() or (template_path or "").strip()
    redirected = _edu_template_redirect(template, html)
    if redirected is not None:
        return redirected

    mode = "inline"
    try:
        report_payload = _parse_report_data(data)
        report_payload = _ensure_edu_report_type(template, report_payload)
        report_payload = _strip_edu_report_title_markers(report_payload)
        report_payload = _enrich_class_overview_archive(
            template,
            report_payload,
            report_data=report_data,
            tool_runtime_ctx=tool_runtime_ctx,
        )

        if template:
            mode = "template"
            try:
                html = _render_template_html(template, report_payload)
            except Exception as e:
                # 对齐 DB-GPT 的体验：模板失败不直接中断；若调用方还给了 inline html，就降级回退。
                if html and html.strip():
                    mode = "inline"
                else:
                    return ToolResult(content=f"报告生成失败：{e}", data=None)
        elif file_path.strip():
            mode = "file"
            html = _read_report_file(file_path.strip())
        elif not html.strip():
            return ToolResult(content="报告生成失败：未提供 html/template_name/file_path。", data=None)

        html = html.strip()
        if len(html) > _REPORT_MAX_HTML_LEN:
            return ToolResult(
                content=f"报告生成失败：HTML 长度超过上限 {_REPORT_MAX_HTML_LEN} 字符。",
                data=None,
            )
        safe_html = _sanitize_report_html(html)
        safe_html = _polish_class_overview_html(
            safe_html,
            template=template,
            title=(title or "") + " " + str((report_payload or {}).get("REPORT_TITLE") or ""),
        )
        out_title = (title or "Report").strip() or "Report"
        type_label = str(report_payload.get("REPORT_TYPE") or "").strip()
        rt_value = ""
        rt = None
        try:
            from src.agent.education.report_types import format_report_display_title
            from src.agent.education.templates import resolve_report_type_from_template

            rt = resolve_report_type_from_template(template)
            if rt is not None:
                rt_value = rt.value
            out_title = format_report_display_title(
                out_title,
                rt,
                type_label=type_label or None,
            )
        except Exception:
            if type_label and type_label not in out_title:
                out_title = f"{out_title}【{type_label}】"
        payload = {
            "output_type": "html",
            "title": out_title,
            "html": safe_html,
            "mode": mode,
            "chunks": [
                {
                    "output_type": "html",
                    "title": out_title,
                    "content": safe_html,
                }
            ],
        }
        if type_label:
            if rt_value:
                payload["report_type"] = rt_value
            payload["report_type_label"] = type_label
        return ToolResult(content=f"HTML 报告已生成（mode={mode}）。", data=payload)
    except Exception as e:
        return ToolResult(content=f"报告生成失败：{e}", data=None)


def _ensure_edu_report_type(template: str, data: dict[str, Any]) -> dict[str, Any]:
    """九大类标准模板：补齐 REPORT_TYPE 中文名。"""
    try:
        from src.agent.education.templates import ensure_report_type_in_data

        return ensure_report_type_in_data(template, data)
    except Exception:
        return data


def _strip_edu_report_title_markers(data: dict[str, Any]) -> dict[str, Any]:
    """页内 REPORT_TITLE 去掉类型枚举/中文角标前缀，避免主标题再带【class_overview】。"""
    out = dict(data)
    raw = str(out.get("REPORT_TITLE") or "").strip()
    if not raw:
        return out
    try:
        from src.agent.education.report_types import strip_report_type_markers

        cleaned = strip_report_type_markers(raw)
        if cleaned:
            out["REPORT_TITLE"] = cleaned
    except Exception:
        pass
    return out


def _enrich_class_overview_archive(
    template: str,
    data: dict[str, Any],
    *,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """class_overview：补齐 REPORT_TYPE、KPI、分数段图/表；结构化字段转 HTML。"""
    tpl = (template or "").replace("\\", "/")
    if "class_overview" not in tpl:
        return data
    out = dict(data)
    try:
        from src.agent.education.report_types import (
            ReportType,
            format_report_display_title,
            report_type_label,
            strip_report_type_markers,
        )

        out["REPORT_TYPE"] = report_type_label(ReportType.CLASS_OVERVIEW)
        raw_title = str(out.get("REPORT_TITLE") or "").strip()
        if raw_title:
            cleaned = strip_report_type_markers(raw_title)
            if "班级级" in cleaned:
                cleaned = cleaned.replace("班级级", "班级")
            out["REPORT_TITLE"] = cleaned or raw_title
        if out.get("_display_title"):
            out["_display_title"] = format_report_display_title(
                str(out.get("_display_title")),
                ReportType.CLASS_OVERVIEW,
            )
    except Exception:
        if not str(out.get("REPORT_TYPE") or "").strip():
            out["REPORT_TYPE"] = "班级总览报告"
        title = str(out.get("REPORT_TITLE") or "")
        if "班级级" in title:
            out["REPORT_TITLE"] = title.replace("班级级", "班级")

    rows = _collect_class_overview_rows(report_data, tool_runtime_ctx)
    if rows:
        _fill_class_overview_kpis_from_rows(out, rows)

    for key in ("PASS_RATE", "EXCELLENT_RATE", "GOOD_RATE", "LOW_SCORE_RATE"):
        out[key] = _normalize_pct_display(out.get(key))
    for key in ("MAX_SCORE", "MIN_SCORE", "AVG_SCORE", "STDEV", "VARIANCE", "TOTAL_COUNT"):
        if _is_blank_metric(out.get(key)):
            out[key] = "-"

    _fill_dispersion_explain(out)
    _fill_class_overview_ability_portrait(
        out,
        rows,
        report_data=report_data,
        tool_runtime_ctx=tool_runtime_ctx,
    )
    # 薄弱知识点：仅班级总览；无 knowledge 时保持空串，不影响 KPI/雷达
    out.setdefault("WEAK_KNOWLEDGE_CHART", "")
    out.setdefault("WEAK_KNOWLEDGE_LIST", out.get("WEAK_KNOWLEDGE_LIST") or "")
    try:
        thr = 60.0
        cached = out.get("_stats") if isinstance(out.get("_stats"), dict) else {}
        if cached.get("weak_threshold") is not None:
            thr = float(cached.get("weak_threshold") or 60.0)
        _fill_class_overview_weak_knowledge(
            out,
            report_data=report_data,
            tool_runtime_ctx=tool_runtime_ctx,
            weak_threshold=thr,
        )
    except Exception:
        pass
    # coerce 前先抽出排名摘要，供总体分析引用
    if not out.get("_RANK_SUMMARY"):
        rs = _extract_rank_summary_text(out.get("RANK_INFO"))
        if rs:
            out["_RANK_SUMMARY"] = rs
    _coerce_class_overview_structured_fields(out)
    _fill_class_overview_narrative(out)

    # 班级总览不再展示「每位学生详细档案与个性化建议」
    out["STUDENT_ARCHIVE_TABLE"] = ""
    return out


_PLACEHOLDER_SUMMARY_HINTS = (
    "班级成绩总览：关注均分",
    "由 ReportOrchestrator 自动生成",
)
_PLACEHOLDER_REC_HINTS = (
    "结合 KPI 与分数段",
)


def _is_placeholder_narrative(val: Any, *, kind: str) -> bool:
    s = str(val or "").strip()
    if not s:
        return True
    hints = _PLACEHOLDER_SUMMARY_HINTS if kind == "summary" else _PLACEHOLDER_REC_HINTS
    return any(h in s for h in hints)


def _stats_dict_from_class_overview(out: dict[str, Any]) -> dict[str, Any]:
    cached = out.get("_stats")
    if isinstance(cached, dict) and (cached.get("count") or cached.get("avg") is not None):
        return cached
    full = out.get("_FULL_SCORE") or out.get("FULL_SCORE")
    try:
        full_f = float(full) if full is not None and not _is_blank_metric(full) else None
    except (TypeError, ValueError):
        full_f = None
    count_raw = out.get("TOTAL_COUNT")
    try:
        count = int(float(str(count_raw).strip())) if not _is_blank_metric(count_raw) else 0
    except (TypeError, ValueError):
        count = 0
    return {
        "count": count,
        "avg": _metric_float(out.get("AVG_SCORE")),
        "pass_rate": _metric_float(out.get("PASS_RATE")),
        "excellent_rate": _metric_float(out.get("EXCELLENT_RATE")),
        "good_rate": _metric_float(out.get("GOOD_RATE")),
        "low_score_rate": _metric_float(out.get("LOW_SCORE_RATE")),
        "max": _metric_float(out.get("MAX_SCORE")),
        "min": _metric_float(out.get("MIN_SCORE")),
        "stdev": _metric_float(out.get("STDEV")),
        "full_score": full_f or 100.0,
        "segments": [],
    }


def _extract_rank_summary_text(rank_info: Any) -> str:
    if isinstance(rank_info, dict):
        s = str(rank_info.get("summary") or "").strip()
        return s if s and "{" not in s else ""
    if not isinstance(rank_info, str):
        return ""
    raw = rank_info.strip()
    if not raw or raw.startswith("<"):
        return ""
    parsed = _parse_structured_blob(raw)
    if isinstance(parsed, dict):
        s = str(parsed.get("summary") or "").strip()
        return s if s and "{" not in s else ""
    if "{" in raw or "'" in raw[:2]:
        return ""
    return raw[:200]


def _fill_class_overview_narrative(out: dict[str, Any]) -> None:
    """用 KPI / 分数段 / 离散度替换占位的总体分析与改进建议。"""
    need_summary = _is_placeholder_narrative(out.get("SUMMARY"), kind="summary")
    need_rec = _is_placeholder_narrative(out.get("RECOMMENDATIONS"), kind="recommendations")
    if not need_summary and not need_rec:
        return
    try:
        from src.agent.education.subject_diagnosis import (
            build_class_overview_recommendations,
            build_class_overview_summary,
        )
    except Exception:
        return

    stats = _stats_dict_from_class_overview(out)
    if not stats.get("count") and stats.get("avg") is None and stats.get("pass_rate") is None:
        return

    # RANK_INFO 可能已被转成 HTML；优先用 coerce 前抽出的摘要
    rank_summary = str(out.get("_RANK_SUMMARY") or "").strip()
    if not rank_summary:
        rank_summary = _extract_rank_summary_text(out.get("RANK_INFO"))
    tip = str(out.get("DISPERSION_TIP") or "").strip()
    level = str(out.get("STDEV_LEVEL") or "").strip()

    if need_summary:
        out["SUMMARY"] = build_class_overview_summary(
            class_name=str(out.get("CLASS_NAME") or ""),
            subject_name=str(out.get("SUBJECT_NAME") or ""),
            exam_name=str(out.get("EXAM_NAME") or ""),
            stats=stats,
            stdev_level=level,
            rank_summary=rank_summary,
        )
    if need_rec:
        out["RECOMMENDATIONS"] = build_class_overview_recommendations(
            stats=stats,
            dispersion_tip=tip,
        )


def _collect_class_overview_rows(
    report_data: dict[str, Any] | None,
    tool_runtime_ctx: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    try:
        from src.agent.education.query_parse import extract_score_rows_from_report_data
    except Exception:
        extract_score_rows_from_report_data = None  # type: ignore[assignment]

    rows: list[dict[str, Any]] = []
    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    upstream = report_data if isinstance(report_data, dict) else ctx.get("report_data")
    if isinstance(upstream, dict) and extract_score_rows_from_report_data is not None:
        rows = extract_score_rows_from_report_data(upstream) or []
    if not rows:
        er = ctx.get("last_exec_result")
        if isinstance(er, dict):
            cols = er.get("columns") or []
            raw = er.get("rows") or []
            if cols and raw:
                rows = [dict(zip(cols, row)) for row in raw]
            elif extract_score_rows_from_report_data is not None:
                rows = extract_score_rows_from_report_data({"exec_result": er}) or []
    return rows


def _is_blank_metric(val: Any) -> bool:
    s = str(val if val is not None else "").strip()
    return (not s) or s in {"-", "—", "N/A", "n/a", "null", "None", "%"}


def _fmt_metric(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _normalize_pct_display(val: Any) -> str:
    """统一比率展示为「数值%」；已含说明文案时保留原文并去掉多余尾部 %。"""
    if _is_blank_metric(val):
        return "-"
    s = str(val).strip()
    while s.endswith("%") and s.count("%") > 1:
        s = s[:-1].rstrip()
    if s.endswith(" %"):
        s = s[:-2].rstrip()
    s = s.strip()
    if not s:
        return "-"
    if "%" in s:
        return s
    return f"{s}%"


def _resolve_full_score_from_score_rows(rows: list[dict[str, Any]]) -> float | None:
    seen: set[float] = set()
    for row in rows:
        for key in ("exam_score", "full_score", "满分"):
            raw = row.get(key)
            if raw is None or raw == "":
                continue
            try:
                seen.add(float(raw))
            except (TypeError, ValueError):
                continue
    if not seen:
        return None
    return max(seen)


def _infer_full_score_for_segments(
    rows: list[dict[str, Any]],
    scores: list[float],
    out: dict[str, Any] | None = None,
) -> float | None:
    """推断满分：优先行内 exam_score；否则按常见满分上取；再否则用分数最大值上取整。"""
    explicit = _resolve_full_score_from_score_rows(rows)
    if explicit is not None and explicit > 0:
        return explicit
    if out:
        for key in ("FULL_SCORE", "full_score", "EXAM_FULL_SCORE"):
            raw = out.get(key)
            if raw is None or raw == "":
                continue
            try:
                v = float(str(raw).replace("%", "").strip())
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        # 副标题常见「满分150」
        blob = " ".join(str(out.get(k) or "") for k in ("REPORT_SUBTITLE", "REPORT_TITLE", "EXAM_NAME"))
        m = re.search(r"满分\s*(\d+(?:\.\d+)?)", blob)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    if not scores:
        return None
    mx = max(scores)
    for cand in (100.0, 120.0, 150.0, 200.0):
        if mx <= cand:
            return cand
    import math

    return float(math.ceil(mx / 10.0) * 10.0)


def _fill_class_overview_kpis_from_rows(out: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """用成绩行补齐缺失 KPI / 强制重算分数段图与表。"""
    try:
        from src.agent.education.charts import build_chart_option
        from src.agent.education.config import EducationConfig
        from src.agent.education.stats import compute_score_stats
        from src.agent.education.subject_diagnosis import build_segment_table_html
    except Exception:
        return

    scores: list[float] = []
    for r in rows:
        raw = r.get("score")
        if raw is None or raw == "":
            continue
        try:
            scores.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not scores:
        return

    full_score = _infer_full_score_for_segments(rows, scores, out)
    try:
        from src.agent.education.config_store import get_config

        cfg = get_config()
    except Exception:
        cfg = EducationConfig()
    stats = compute_score_stats(scores, cfg, full_score)

    kpi_map = {
        "TOTAL_COUNT": str(stats.get("count") or 0),
        "AVG_SCORE": _fmt_metric(stats.get("avg")),
        "PASS_RATE": _fmt_metric(stats.get("pass_rate")),
        "EXCELLENT_RATE": _fmt_metric(stats.get("excellent_rate")),
        "GOOD_RATE": _fmt_metric(stats.get("good_rate")),
        "LOW_SCORE_RATE": _fmt_metric(stats.get("low_score_rate")),
        "MAX_SCORE": _fmt_metric(stats.get("max")),
        "MIN_SCORE": _fmt_metric(stats.get("min")),
        "STDEV": _fmt_metric(stats.get("stdev")),
        "VARIANCE": _fmt_metric(stats.get("variance")),
    }
    for key, val in kpi_map.items():
        if _is_blank_metric(out.get(key)):
            out[key] = val

    segments = stats.get("segments") or []
    out["_stats"] = stats
    # 有成绩行时始终按正确满分重算图/表（覆盖 LLM 满分 100 的全 0 表）
    out["SCORE_DIST_CHART"] = build_chart_option(
        "score_distribution",
        {"segments": segments, "pass_rate": stats.get("pass_rate")},
        title="分数段分布",
    )
    out["SEGMENT_TABLE"] = build_segment_table_html(
        segments,
        full_score=stats.get("full_score"),
    )
    # 供离散度说明使用
    if stats.get("full_score") is not None:
        out.setdefault("_FULL_SCORE", stats.get("full_score"))


def _fill_dispersion_explain(out: dict[str, Any]) -> None:
    """根据 STDEV / 满分补齐标准差与方差的可读说明。"""
    try:
        from src.agent.education.stats import describe_score_dispersion
    except Exception:
        return

    stdev_raw = out.get("STDEV")
    try:
        stdev_f = float(str(stdev_raw).replace("%", "").strip()) if not _is_blank_metric(stdev_raw) else None
    except (TypeError, ValueError):
        stdev_f = None

    full_score = out.get("_FULL_SCORE") or out.get("FULL_SCORE")
    if full_score is None:
        full_score = _infer_full_score_for_segments([], [], out)

    var_raw = out.get("VARIANCE")
    try:
        var_f = float(str(var_raw).strip()) if not _is_blank_metric(var_raw) else None
    except (TypeError, ValueError):
        var_f = None

    info = describe_score_dispersion(stdev_f, full_score=full_score, variance=var_f)
    out["STDEV_LEVEL"] = info["level"]
    out["STDEV_LEVEL_CLASS"] = info["level_class"]
    out["STDEV_HINT"] = info["stdev_hint"]
    if _is_blank_metric(out.get("VARIANCE")) and info["variance"] != "-":
        out["VARIANCE"] = _fmt_metric(info["variance"])
    out["VARIANCE_HINT"] = info["variance_hint"]
    out["DISPERSION_TIP"] = info["tip"]


def _chart_option_blank(raw: Any) -> bool:
    s = str(raw or "").strip()
    return (not s) or s in {"{}", "null", "None", "[]", "undefined"}


def _metric_float(val: Any) -> float | None:
    if _is_blank_metric(val):
        return None
    s = str(val).strip().replace("%", "").replace(",", "")
    # "75.00 (39/52)" → 取首位数字
    m = re.match(r"^-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _collect_knowledge_rows_for_overview(
    report_data: dict[str, Any] | None,
    tool_runtime_ctx: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    ctx = tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}
    sources: list[Any] = []
    for blob in (report_data, ctx.get("report_data"), ctx):
        if not isinstance(blob, dict):
            continue
        for key in ("knowledge_rows", "knowledge"):
            raw = blob.get(key)
            if isinstance(raw, list) and raw:
                sources.append(raw)
        # tool_calls / fetch 结果里也可能挂着
        fetch = blob.get("fetch_data") if isinstance(blob.get("fetch_data"), dict) else None
        if fetch and isinstance(fetch.get("knowledge_rows"), list):
            sources.append(fetch["knowledge_rows"])
    for rows in sources:
        out = [dict(r) for r in rows if isinstance(r, dict)]
        if out:
            return out
    return []


def _fill_class_overview_weak_knowledge(
    out: dict[str, Any],
    *,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
    weak_threshold: float = 60.0,
) -> None:
    """有 knowledge_rows 时补薄弱芯片 + 柱；无数据保持空（前端隐藏）。"""
    list_blank = not str(out.get("WEAK_KNOWLEDGE_LIST") or "").strip()
    chart_blank = _chart_option_blank(out.get("WEAK_KNOWLEDGE_CHART"))
    if not list_blank and not chart_blank:
        return
    knowledge = _collect_knowledge_rows_for_overview(report_data, tool_runtime_ctx)
    if not knowledge:
        return
    try:
        from src.agent.education.subject_diagnosis import (
            build_weak_knowledge_chart,
            build_weak_knowledge_list_html,
            enrich_knowledge_rows,
            pick_weak_knowledge_topn,
        )
    except Exception:
        return
    kn = enrich_knowledge_rows(knowledge)
    thr = float(weak_threshold or 60.0)
    weak = pick_weak_knowledge_topn(kn, weak_threshold=thr, max_items=8)
    if not weak:
        return
    if list_blank:
        out["WEAK_KNOWLEDGE_LIST"] = build_weak_knowledge_list_html(
            weak, weak_threshold=thr
        )
    if chart_blank:
        out["WEAK_KNOWLEDGE_CHART"] = build_weak_knowledge_chart(weak)


def _guess_subject_name(rows: list[dict[str, Any]], out: dict[str, Any]) -> str:
    for key in ("SUBJECT_NAME", "subject_name", "subject"):
        v = str(out.get(key) or "").strip()
        if v and v not in ("全科", "全部", "-"):
            return v
    names: list[str] = []
    for r in rows:
        sub = str(r.get("subject") or r.get("subject_name") or "").strip()
        if sub:
            names.append(sub)
    uniq = sorted({n for n in names if n})
    if len(uniq) == 1:
        return uniq[0]
    return ""


def _fill_class_overview_ability_portrait(
    out: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    report_data: dict[str, Any] | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
) -> None:
    """补齐学科能力画像雷达：知识点能力层级 > 多科均分 > 单科 KPI 维度。"""
    if not _chart_option_blank(out.get("SUBJECT_RADAR_CHART")):
        return
    try:
        from src.agent.education.charts import build_chart_option
    except Exception:
        return

    class_name = str(out.get("CLASS_NAME") or "").strip()
    subject = _guess_subject_name(rows, out)
    portrait_title = (
        f"{class_name}{(' ' + subject) if subject else ''}能力画像".strip()
        or "学科能力画像"
    )

    # 1) 知识点能力层级雷达（有 ability_level / score_rate 时）
    knowledge = _collect_knowledge_rows_for_overview(report_data, tool_runtime_ctx)
    if knowledge:
        try:
            from src.agent.education.knowledge_tier import (
                ABILITY_LABELS,
                build_ability_tier_summary,
            )

            tier = build_ability_tier_summary(knowledge)
            levels: list[str] = []
            values: list[float] = []
            for s in tier.get("by_ability_level") or []:
                lv = str(s.get("ability_level") or "")
                if lv in ("", "unknown"):
                    continue
                rate = s.get("avg_score_rate")
                if rate is None:
                    continue
                levels.append(ABILITY_LABELS.get(lv, lv))
                values.append(round(float(rate), 1))
            if len(levels) >= 2:
                out["SUBJECT_RADAR_CHART"] = build_chart_option(
                    "ability_radar",
                    {"levels": levels, "values": values},
                    title=portrait_title,
                )
                return
        except Exception:
            pass

    # 2) 多科目：各科均分雷达
    by_subj: dict[str, list[float]] = {}
    for r in rows:
        sub = str(r.get("subject") or r.get("subject_name") or "").strip()
        if not sub:
            continue
        raw = r.get("score")
        if raw is None or raw == "":
            continue
        try:
            by_subj.setdefault(sub, []).append(float(raw))
        except (TypeError, ValueError):
            continue
    if len(by_subj) >= 2:
        subjects = sorted(by_subj.keys())
        avgs = [round(sum(by_subj[s]) / len(by_subj[s]), 2) for s in subjects]
        full = out.get("_FULL_SCORE") or _infer_full_score_for_segments(rows, [x for xs in by_subj.values() for x in xs], out) or 100
        out["SUBJECT_RADAR_CHART"] = build_chart_option(
            "subject_radar",
            {
                "subjects": subjects,
                "values": avgs,
                "full_score": full,
                "series_name": "均分",
            },
            title=portrait_title or "各科目能力画像",
        )
        return

    # 3) 单科/班级 KPI 维度雷达（与此前「数学能力维度」一致，统一到 0–100）
    avg = _metric_float(out.get("AVG_SCORE"))
    pass_rate = _metric_float(out.get("PASS_RATE"))
    exc_rate = _metric_float(out.get("EXCELLENT_RATE"))
    max_score = _metric_float(out.get("MAX_SCORE"))
    stdev = _metric_float(out.get("STDEV"))
    full = _metric_float(out.get("_FULL_SCORE") or out.get("FULL_SCORE"))
    if full is None or full <= 0:
        full = _infer_full_score_for_segments(rows, [], out) or 100.0
    if avg is None and max_score is None and pass_rate is None:
        return

    def _pct_of_full(v: float | None) -> float:
        if v is None:
            return 0.0
        return round(max(0.0, min(100.0, v / float(full) * 100.0)), 1)

    # 成绩均衡：标准差占满分比例越低越好
    balance = 0.0
    if stdev is not None and full > 0:
        balance = round(max(0.0, min(100.0, 100.0 - (stdev / float(full) * 100.0))), 1)

    levels = ["平均分", "及格率", "优秀率", "最高分", "成绩均衡"]
    values = [
        _pct_of_full(avg),
        round(max(0.0, min(100.0, pass_rate or 0.0)), 1),
        round(max(0.0, min(100.0, exc_rate or 0.0)), 1),
        _pct_of_full(max_score),
        balance,
    ]
    out["SUBJECT_RADAR_CHART"] = build_chart_option(
        "ability_radar",
        {"levels": levels, "values": values},
        title=portrait_title,
    )


def _parse_structured_blob(val: Any) -> Any | None:
    """解析 LLM 误填的 list/dict / JSON / Python 字面量。"""
    if isinstance(val, (dict, list)):
        return val
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.startswith("<"):
        return None
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        import ast

        return ast.literal_eval(s)
    except Exception:
        pass
    try:
        import json

        return json.loads(s)
    except Exception:
        return None


def _dict_to_kv_table_html(data: dict[str, Any]) -> str:
    # 嵌套 list/dict 勿直接 str()，否则会出现 items 一格塞满 JSON
    simple: dict[str, Any] = {}
    nested_html: list[str] = []
    for k, v in data.items():
        if v is None or (not isinstance(v, (list, dict)) and str(v).strip() == ""):
            continue
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            nested_html.append(_list_of_dicts_to_table_html(_normalize_rank_item_rows(v)))
        elif isinstance(v, dict):
            nested_html.append(_dict_to_kv_table_html(v))
        elif isinstance(v, list):
            continue
        else:
            simple[k] = v
    parts: list[str] = []
    if simple:
        rows = "".join(
            f"<tr><th>{_html_escape(str(k))}</th><td>{_html_escape(str(v))}</td></tr>"
            for k, v in simple.items()
        )
        parts.append(
            '<div class="edu-table-wrap">'
            f'<table class="edu-table"><tbody>{rows}</tbody></table>'
            "</div>"
        )
    parts.extend(nested_html)
    return "".join(parts)


_RANK_ITEM_KEY_LABELS: dict[str, str] = {
    "指标": "指标",
    "metric": "指标",
    "name": "指标",
    "value": "本班",
    "数值": "本班",
    "rank": "年级排名",
    "排名": "年级排名",
    "total": "参评班数",
    "cohort_avg": "年级对照",
    "grade_avg": "年级对照",
    "avg": "年级对照",
}


def _normalize_rank_item_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """年级排名 items：统一中文列名，并格式化「第 x / 共 y」。"""
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row: dict[str, Any] = {}
        # 优先按标准列顺序输出
        metric = r.get("指标") or r.get("metric") or r.get("name") or ""
        value = r.get("value") if "value" in r else r.get("数值")
        rank = r.get("rank") if "rank" in r else r.get("排名")
        total = r.get("total") if "total" in r else r.get("参评班数")
        cohort = r.get("cohort_avg") or r.get("grade_avg") or r.get("年级对照")

        if metric != "" or any(k in r for k in ("value", "数值", "rank", "排名")):
            row["指标"] = metric
            if value is not None:
                row["本班"] = value
            if rank is not None:
                if total is not None:
                    row["年级排名"] = f"第 {rank} / 共 {total} 班"
                else:
                    row["年级排名"] = f"第 {rank}"
            elif total is not None:
                row["参评班数"] = total
            if cohort is not None:
                row["年级对照"] = cohort
            # 保留未映射字段
            used = {
                "指标", "metric", "name", "value", "数值", "rank", "排名",
                "total", "参评班数", "cohort_avg", "grade_avg", "年级对照", "avg",
            }
            for k, v in r.items():
                if k in used:
                    continue
                label = _RANK_ITEM_KEY_LABELS.get(str(k), str(k))
                if label not in row:
                    row[label] = v
            out.append(row)
            continue

        mapped: dict[str, Any] = {}
        for k, v in r.items():
            mapped[_RANK_ITEM_KEY_LABELS.get(str(k), str(k))] = v
        out.append(mapped)
    return out


def _format_rank_info_html(parsed: Any) -> str:
    """年级排名专用：支持 {scope, items, summary} / items 列表 / 扁平 KV。"""
    if isinstance(parsed, list) and parsed and all(isinstance(x, dict) for x in parsed):
        return _list_of_dicts_to_table_html(_normalize_rank_item_rows(parsed))

    if not isinstance(parsed, dict):
        return ""

    # 嵌套结构：scope + items(+ summary)
    items = parsed.get("items") or parsed.get("Items") or parsed.get("排名明细")
    if isinstance(items, str):
        items = _parse_structured_blob(items)
    scope = parsed.get("scope") or parsed.get("Scope") or parsed.get("范围") or ""
    summary = parsed.get("summary") or parsed.get("Summary") or parsed.get("综述") or ""

    if isinstance(items, list) and items and all(isinstance(x, dict) for x in items):
        parts: list[str] = []
        if scope:
            parts.append(f'<p class="prose-card"><strong>对比范围</strong>：{_html_escape(str(scope))}</p>')
        parts.append(_list_of_dicts_to_table_html(_normalize_rank_item_rows(items)))
        if summary:
            parts.append(f'<p class="prose-card">{_html_escape(str(summary))}</p>')
        # 其它附加字段（排除已渲染）
        rest = {
            k: v
            for k, v in parsed.items()
            if k not in {
                "items", "Items", "排名明细", "scope", "Scope", "范围",
                "summary", "Summary", "综述",
            }
            and v is not None
            and not isinstance(v, (list, dict))
            and str(v).strip() != ""
        }
        if rest:
            parts.append(_dict_to_kv_table_html(rest))
        return "".join(parts)

    # 扁平 dict：若值里仍有 list[dict]，走通用 dict 渲染
    return _dict_to_kv_table_html(parsed)


def _list_of_dicts_to_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(str(k))
    head = "".join(f"<th>{_html_escape(k)}</th>" for k in keys)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{_html_escape(str(r.get(k, '')))}</td>" for k in keys)
        + "</tr>"
        for r in rows
    )
    return (
        '<div class="edu-table-wrap">'
        f'<table class="edu-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
        "</div>"
    )


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _coerce_class_overview_structured_fields(out: dict[str, Any]) -> None:
    """SUBJECT_BREAKDOWN / RANK_INFO：把 JSON/字面量转成表格 HTML。"""
    # SUBJECT_BREAKDOWN
    raw_bd = out.get("SUBJECT_BREAKDOWN")
    if raw_bd is not None and not (isinstance(raw_bd, str) and "<table" in raw_bd.lower()):
        parsed_bd = _parse_structured_blob(raw_bd)
        if parsed_bd is None:
            s = str(raw_bd).strip()
            if s.startswith(("{", "[")) or "'科目'" in s:
                out["SUBJECT_BREAKDOWN"] = ""
        elif isinstance(parsed_bd, list) and parsed_bd and all(isinstance(x, dict) for x in parsed_bd):
            out["SUBJECT_BREAKDOWN"] = _list_of_dicts_to_table_html(parsed_bd)
        elif isinstance(parsed_bd, dict):
            out["SUBJECT_BREAKDOWN"] = _dict_to_kv_table_html(parsed_bd)
        else:
            out["SUBJECT_BREAKDOWN"] = ""

    # RANK_INFO（含 {scope, items, summary} 嵌套）
    raw_rank = out.get("RANK_INFO")
    if raw_rank is None:
        return
    if isinstance(raw_rank, str) and "<table" in raw_rank.lower():
        # 已正确渲染且无 JSON 泄漏
        if not (("[{'" in raw_rank or '[{"' in raw_rank) and ("'rank'" in raw_rank or '"rank"' in raw_rank or "'指标'" in raw_rank)):
            return
        # 坏表：尝试从页面可见的 Python/JSON 片段抢救
        m = re.search(r"(\[[^<>]*\{[^<>]*'指标'[^<>]*\}[^<>]*\])", raw_rank)
        if not m:
            m = re.search(r"(\[[^<>]*\{[^<>]*\"指标\"[^<>]*\}[^<>]*\])", raw_rank)
        if m:
            rescued = _parse_structured_blob(m.group(1))
            if isinstance(rescued, list):
                scope_m = re.search(r"<td>([^<]*共\s*\d+\s*个班[^<]*)</td>", raw_rank)
                summary_m = re.search(r"<td>([^<]*排名[^<]*)</td>", raw_rank)
                payload = {
                    "scope": scope_m.group(1) if scope_m else "",
                    "items": rescued,
                    "summary": summary_m.group(1) if summary_m else "",
                }
                out["RANK_INFO"] = _format_rank_info_html(payload)
                return
        return

    parsed_rank = _parse_structured_blob(raw_rank)
    if parsed_rank is None:
        s = str(raw_rank).strip()
        if s.startswith(("{", "[")) or "'年级排名'" in s or "'指标'" in s:
            out["RANK_INFO"] = ""
        return

    out["RANK_INFO"] = _format_rank_info_html(parsed_rank)


@tool()
def find_related_tables(
    datasource_id: int,
    question: str,
    limit: int = _FIND_RELATED_DEFAULT_LIMIT,
    workspace_oid: int | None = None,
) -> ToolResult:
    """根据问题关键词从当前数据源**筛出可能相关的表**（top-K），降低 Schema 推理成本。

    **当前实现：LIKE/token 兜底**——把 ``question`` 切成中英 token（中文走 2-gram），
    在每张表的 ``name`` / ``comment`` / 字段 ``name`` / 字段 ``comment`` 里做子串命中，
    按命中数降序取前 K 张。当 embedding 检索（见 ``MVP_PLAN.md`` Phase 4）就绪后，
    可以在**不变签名**的前提下换成向量召回。

    **适用时机**：表很多（>20）或问题有明确业务关键词时优先用，能显著减少后续
    ``describe_table`` 的盲目调用；表很少（< 10）时直接 ``list_tables`` 更合适。

    Args:
        question: 用户的自然语言问题（会自动分词）。
        limit: 返回表数上限，默认 10，范围 1~20。

    Returns:
        命中非空：按分数降序的 ``[{name, comment, score, matched_tokens}, ...]``，
        content 是可读的 markdown 列表；
        命中为空：仍返回 ``list_tables`` 全量清单 + 提示"未命中关键词，请直接 describe_table"，
        避免 LLM 因为"找不到"而死在这一步。
    """
    from src.datasource.db.db import get_schema_info

    limit = max(1, min(int(limit or _FIND_RELATED_DEFAULT_LIMIT), _FIND_RELATED_HARD_CAP))
    db_type, config, ds_name = _load_datasource(datasource_id, workspace_oid)
    schema = get_schema_info(db_type, config)

    tokens = _tokenize_question(question)
    if not tokens:
        # 问题里没有可抽取的关键词（极少发生），降级为 list_tables 的行为
        items = [{"name": t["name"], "comment": t.get("comment", ""), "score": 0, "matched_tokens": []} for t in schema]
        return ToolResult(
            content=f"数据源 `{ds_name}` 共 {len(items)} 张表（未从问题提取到关键词，返回全量）。",
            data=items,
        )

    scored: list[tuple[int, list[str], dict[str, Any]]] = []
    for t in schema:
        score, matched = _score_table_against_tokens(t, tokens)
        if score > 0:
            scored.append((score, matched, t))
    scored.sort(key=lambda x: (-x[0], x[2].get("name") or ""))
    top = scored[:limit]

    if not top:
        fallback_items = [
            {"name": t["name"], "comment": t.get("comment", ""), "score": 0, "matched_tokens": []}
            for t in schema[:limit]
        ]
        lines = [
            f"未命中关键词 {tokens[:6]}；数据源 `{ds_name}` 共 {len(schema)} 张表，列出前 {len(fallback_items)} 张：",
        ]
        for it in fallback_items:
            suffix = f" — {it['comment']}" if it["comment"] else ""
            lines.append(f"- {it['name']}{suffix}")
        lines.append("\n建议：直接 `describe_table` 最可能相关的一张再决定是否查询。")
        return ToolResult(content="\n".join(lines), data=fallback_items)

    items = [
        {
            "name": t["name"],
            "comment": t.get("comment", ""),
            "score": score,
            "matched_tokens": matched,
        }
        for score, matched, t in top
    ]
    lines = [
        f"数据源 `{ds_name}` 中与问题最相关的 {len(items)} 张表（按命中数降序）：",
    ]
    for it in items:
        suffix = f" — {it['comment']}" if it["comment"] else ""
        hit = ", ".join(it["matched_tokens"][:6])
        lines.append(f"- **{it['name']}** (score={it['score']}; 命中: {hit}){suffix}")
    return ToolResult(content="\n".join(lines), data=items)


@tool()
def describe_table(
    datasource_id: int,
    table_name: str,
    user_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """返回指定表的列清单（name / type / comment）。

    Args:
        table_name: 要查询的表名，支持 ``schema.table`` 形式。
    """
    from src.common.core.database import get_db_session
    from src.datasource.db.db import get_schema_info
    from src.datasource.service.query_permission import schema_tables_for_user
    from src.system.crud.crud_user import get_user_by_id

    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    schema = get_schema_info(db_type, config)
    match = next((t for t in schema if t["name"] == table_name), None)
    if match is None:
        available = ", ".join(t["name"] for t in schema[:20]) or "（空）"
        return ToolResult(
            content=f"表 `{table_name}` 不存在。已知表（前 20）：{available}",
            data=None,
        )

    if user_id is not None:
        with get_db_session() as session:
            user = get_user_by_id(session, user_id)
            filtered = schema_tables_for_user(session, user, datasource_id, [dict(match)])
            match = filtered[0] if filtered else match

    fields = match.get("fields", [])
    header = f"表 `{match['name']}`" + (f"（{match['comment']}）" if match.get("comment") else "")
    if not fields:
        content = header + "\n（无字段信息）"
    else:
        lines = [header, "", "| 字段 | 类型 | 注释 |", "| --- | --- | --- |"]
        for f in fields:
            lines.append(f"| {f['name']} | {f.get('type', '')} | {f.get('comment', '')} |")
        content = "\n".join(lines)

    return ToolResult(content=content, data=match)


@tool()
def sample_rows(
    datasource_id: int,
    table_name: str,
    limit: int = SAMPLE_ROWS_DEFAULT,
    where_clause: str = "",
    user_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """采样表的若干行（可带 WHERE 过滤），用于理解真实数据样貌、枚举值、业务含义。

    典型用法：``describe_table`` 之后仍不确定字段取值语义时，调本工具做条件采样，
    例如只看 ``status='active'`` 的行、只看最近日期的行。

    Args:
        table_name: 表名（限制 ``[A-Za-z0-9_.]``，防 SQL 注入）。
        limit: 采样行数，默认 3，建议 1~10；超过 10 会被截断。
        where_clause: 可选 WHERE 条件**不含 WHERE 关键字本身**，例如
            ``status = 'active' AND created_at > '2024-01-01'``。留空表示不过滤。
            仅允许只读表达式；含 INSERT/UPDATE/DELETE/UNION-子 DML 等写操作一律拒绝；
            多条语句（分号）也会拒绝。
    """
    limit = max(1, min(int(limit or SAMPLE_ROWS_DEFAULT), SAMPLE_ROWS_LLM_MAX))
    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    quoted = _safe_identifier(table_name, db_type)

    clause = (where_clause or "").strip().rstrip(";").strip()
    where_sql = f" WHERE {clause}" if clause else ""
    sql = f"SELECT * FROM {quoted}{where_sql} LIMIT {limit}"

    if clause:
        try:
            _validate_read_only_select(sql, db_type)
        except ValueError as e:
            # 业务错误：返回 ToolResult 让 LLM 在 observation 里自修正；不抛。
            return ToolResult(
                content=f"采样失败：where_clause 不合法（{e}）。请只用 AND/OR 组合的过滤条件。",
                data=None,
            )

    if user_id is not None:
        from src.datasource.service.execute_with_permission import execute_sql_with_permission_by_user_id
        from src.datasource.service.sql_auto_fix import format_auto_fix_note

        success, message, result, sql_run = execute_sql_with_permission_by_user_id(
            user_id,
            datasource_id,
            workspace_oid,
            sql,
            tables_hint=[table_name.split(".")[-1]],
        )
        if not success:
            note = ""
            if isinstance(result, dict) and result.get("fixes_applied"):
                note = " " + format_auto_fix_note(result["fixes_applied"], success=False)
            return ToolResult(content=f"采样失败：{message}{note}", data=None)
    else:
        from src.datasource.service.sql_auto_fix import format_auto_fix_note, run_sql_with_auto_fix

        outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
        if not outcome.success:
            note = format_auto_fix_note(outcome.fixes_applied, success=False)
            msg = f"采样失败：{outcome.message}"
            if note:
                msg += f" {note}"
            return ToolResult(content=msg, data=None)
        result = outcome.result
        sql_run = outcome.sql_run

    if not isinstance(result, dict):
        return ToolResult(content="采样失败：无结果", data=None)

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    header = f"`{table_name}` 采样 {len(rows)} 行" + (f"（WHERE {clause}）" if clause else "") + "："
    content = header + "\n\n" + _format_rows_as_markdown(columns, rows, SAMPLE_ROWS_HARD_CAP)
    return ToolResult(content=content, data=result)


@tool()
def execute_sql(
    datasource_id: int,
    sql: str,
    user_id: int | None = None,
    workspace_oid: int | None = None,
) -> ToolResult:
    """在当前数据源上执行只读 SQL 并返回结果。

    执行失败时会根据数据库 error 自动尝试常见改写（如 st.student_id→st.id）并重试。

    Args:
        sql: 要执行的 SELECT 语句；非只读会被拒绝。
    """
    from src.datasource.service.execute_with_permission import execute_sql_with_permission_by_user_id
    from src.datasource.service.sql_auto_fix import format_auto_fix_note, run_sql_with_auto_fix

    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    fixes: list[str] = []

    if user_id is not None:
        success, message, result, sql_run = execute_sql_with_permission_by_user_id(
            user_id, datasource_id, workspace_oid, sql
        )
        fixes = (result or {}).get("fixes_applied") if isinstance(result, dict) else []
    else:
        outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
        success = outcome.success
        message = outcome.message
        result = outcome.result if isinstance(outcome.result, dict) else None
        sql_run = outcome.sql_run
        fixes = outcome.fixes_applied
        note = format_auto_fix_note(fixes, success=success)
        if note:
            message = f"{message} {note}" if not success else f"{message}{note}"

    if not success:
        return ToolResult(
            content=f"SQL 执行失败：{message}",
            data={"sql": sql_run, "error": message, "fixes_applied": fixes},
        )

    if not isinstance(result, dict) or "rows" not in result:
        return ToolResult(
            content=f"SQL 执行成功，影响 {result.get('row_count', 0) if isinstance(result, dict) else 0} 行。",
            data={"sql": sql_run, **(result if isinstance(result, dict) else {})},
        )

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    preview = _format_rows_as_markdown(columns, rows, EXECUTE_SQL_PREVIEW_ROWS)
    fix_hint = format_auto_fix_note(fixes, success=True) if fixes else ""
    content = f"SQL 执行成功{fix_hint}，返回 {len(rows)} 行：\n\n{preview}"
    return ToolResult(
        content=content,
        data={
            "sql": sql_run,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "fixes_applied": fixes,
        },
    )


@tool()
def find_related_datasources(question: str, workspace_oid: int = 1) -> ToolResult:
    """列出当前工作空间内可用数据源，供 LLM 根据问题自选。

    Note:
        MVP 阶段暂不做向量匹配，先返回本空间 active 数据源；后续接入 embedding
        后再做真正的 top-k 召回。
    """
    from src.common.core.database import get_db_session
    from src.datasource.crud import crud_datasource

    with get_db_session() as session:
        all_ds = crud_datasource.get_datasources(
            session=session, skip=0, limit=200, oid=int(workspace_oid)
        )
    items = [
        {"id": d.id, "name": d.name, "description": d.description or "", "type": d.type}
        for d in all_ds
        if (d.status or "active") == "active"
    ]
    if not items:
        return ToolResult(content="当前没有可用数据源。", data=[])

    lines = [f"共 {len(items)} 个可用数据源："]
    for it in items:
        desc = f" — {it['description']}" if it["description"] else ""
        lines.append(f"- [{it['id']}] {it['name']} ({it['type']}){desc}")
    return ToolResult(
        content="\n".join(lines) + f"\n\n（对照问题：{question}）",
        data=items,
    )


@tool()
def recent_questions(datasource_id: int, user_id: int, limit: int = 10) -> ToolResult:
    """返回当前用户在该数据源上近期成功提问的问题列表，用于启发式追问。

    Args:
        limit: 返回条数，默认 10，上限 50。
    """
    from src.chat.crud import chat as chat_crud
    from src.common.core.database import get_db_session

    limit = max(1, min(int(limit or 10), 50))
    with get_db_session() as session:
        questions = chat_crud.get_recent_questions(
            session=session,
            datasource_id=datasource_id,
            user_id=user_id,
            limit=limit,
        )
    if not questions:
        return ToolResult(content="该数据源暂无历史问题。", data=[])
    content = "近期历史问题：\n" + "\n".join(f"- {q}" for q in questions)
    return ToolResult(content=content, data=list(questions))


def default_business_tools() -> list[FunctionTool | TerminateTool]:
    """返回默认业务工具的全新实例列表（不含 bindings）。

    当前清单（14 件）：
    - ``list_tables`` / ``find_related_tables`` / ``describe_table`` / ``sample_rows``
      / ``execute_sql``：对接数据源的 Schema 探查 & SQL 执行；
    - ``find_related_datasources``：多数据源场景的"选源"启发式；
    - ``recent_questions``：看自己在该数据源的历史问题，启发追问；
    - ``calculate``：**纯算术沙盒**（asteval），用于百分比/同比/均值等后处理
      运算——LLM 心算容易错，剥离给确定性求值器；
    - ``render_html_report``：HTML 报告生成（模板/文件/内联三模式）；
    - ``resolve_score_schema`` / ``compute_score_stats_tool`` /
      ``compute_rankings_tool`` / ``identify_at_risk_students_tool`` /
      ``build_chart_option_tool`` / ``select_report_template_tool``：
      教育学情领域工具（见 ``src/agent/education/tools.py``）。

    每次调用返回新 list，避免在多会话场景下共享同一组引用造成状态串扰。
    """
    # 教育学情工具延迟导入，避免 education 包在测试 mock business 时循环引用。
    from src.agent.education.tools import EDUCATION_TOOLS

    return [
        list_tables,
        find_related_tables,
        describe_table,
        sample_rows,
        execute_sql,
        find_related_datasources,
        recent_questions,
        calculate,
        render_html_report,
        *EDUCATION_TOOLS,
    ]


def build_default_toolpack(
    *,
    datasource_id: int | None = None,
    user_id: int | None = None,
    workspace_oid: int | None = 1,
    include_terminate: bool = True,
    report_data: dict[str, Any] | None = None,
    sub_task: str = "",
    tool_runtime_ctx: dict[str, Any] | None = None,
) -> ToolPack:
    """构造默认业务 ToolPack，并按需绑定运行时参数。

    Args:
        datasource_id: 绑定当前会话的数据源 ID；为 None 则工具必须由 LLM
            自行通过 ``find_related_datasources`` 选择。
        user_id: 当前用户 ID，``recent_questions`` 需要。
        include_terminate: 是否挂载 ``terminate`` 工具（ReAct 终止信号）。
    """
    tools: list[Any] = list(default_business_tools())
    if include_terminate:
        tools.append(TerminateTool())
    pack = ToolPack(tools=tools)

    bindings: dict[str, Any] = {}
    if datasource_id is not None:
        bindings["datasource_id"] = datasource_id
    if user_id is not None:
        bindings["user_id"] = user_id
    if workspace_oid is not None:
        bindings["workspace_oid"] = workspace_oid
    if report_data is not None:
        bindings["report_data"] = report_data
    if sub_task:
        bindings["sub_task"] = sub_task
    if tool_runtime_ctx is not None:
        bindings["tool_runtime_ctx"] = tool_runtime_ctx
    return pack.bind(**bindings) if bindings else pack
