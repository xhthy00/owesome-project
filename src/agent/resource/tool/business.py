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


def _load_datasource(datasource_id: int) -> tuple[str, dict[str, Any], str]:
    """返回 (db_type, decrypted_config, datasource_name)。

    仅在"数据源不存在"时抛 ValueError——这属于调用方传入的 binding 错误，
    不是业务错误。其余数据库访问错误由调用方处理。
    """
    from src.common.core.database import get_db_session
    from src.common.utils.aes import decrypt_conf
    from src.datasource.crud import crud_datasource

    with get_db_session() as session:
        ds = crud_datasource.get_datasource_by_id(session, datasource_id)
        if ds is None:
            raise ValueError(f"datasource not found: id={datasource_id}")
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
def list_tables(datasource_id: int) -> ToolResult:
    """列出当前数据源的所有表。

    Returns:
        每项含 name / comment；供 LLM 决定进一步要 describe 哪张表。
    """
    from src.datasource.db.db import get_schema_info

    db_type, config, ds_name = _load_datasource(datasource_id)
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
_REPORT_TEMPLATE_SAFE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
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


def _render_template_html(template_name: str, data: dict[str, Any]) -> str:
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

    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        return str(data.get(key, ""))

    return _REPORT_PLACEHOLDER_RE.sub(_replace, raw)


def _parse_report_data(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
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

    raise ValueError(f"文件不存在: {file_path}")


@tool()
def render_html_report(
    html: str = "",
    title: str = "Report",
    template_name: str = "",
    template_path: str = "",
    data: dict[str, Any] | None = None,
    file_path: str = "",
) -> ToolResult:
    """生成 HTML 报告载荷（DB-GPT html_interpreter 风格）。

    三种模式（优先级从高到低）：
    1. template_name/template_path + data：读取模板并替换 ``{{KEY}}``；
    2. file_path：读取工作区内已有 HTML 文件；
    3. html：直接使用传入 HTML 字符串。
    """
    mode = "inline"
    try:
        template = (template_name or "").strip() or (template_path or "").strip()
        report_data = _parse_report_data(data)

        if template:
            mode = "template"
            try:
                html = _render_template_html(template, report_data)
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
        payload = {
            "output_type": "html",
            "title": (title or "Report").strip() or "Report",
            "html": safe_html,
            "mode": mode,
            "chunks": [
                {
                    "output_type": "html",
                    "title": (title or "Report").strip() or "Report",
                    "content": safe_html,
                }
            ],
        }
        return ToolResult(content=f"HTML 报告已生成（mode={mode}）。", data=payload)
    except Exception as e:
        return ToolResult(content=f"报告生成失败：{e}", data=None)


@tool()
def find_related_tables(
    datasource_id: int,
    question: str,
    limit: int = _FIND_RELATED_DEFAULT_LIMIT,
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
    db_type, config, ds_name = _load_datasource(datasource_id)
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
def describe_table(datasource_id: int, table_name: str) -> ToolResult:
    """返回指定表的列清单（name / type / comment）。

    Args:
        table_name: 要查询的表名，支持 ``schema.table`` 形式。
    """
    from src.datasource.db.db import get_schema_info

    db_type, config, _ = _load_datasource(datasource_id)
    schema = get_schema_info(db_type, config)
    match = next((t for t in schema if t["name"] == table_name), None)
    if match is None:
        available = ", ".join(t["name"] for t in schema[:20]) or "（空）"
        return ToolResult(
            content=f"表 `{table_name}` 不存在。已知表（前 20）：{available}",
            data=None,
        )

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
    from src.datasource.db.db import execute_sql as db_execute_sql

    limit = max(1, min(int(limit or SAMPLE_ROWS_DEFAULT), SAMPLE_ROWS_LLM_MAX))
    db_type, config, _ = _load_datasource(datasource_id)
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

    success, message, result = db_execute_sql(db_type=db_type, config=config, sql=sql)
    if not success:
        return ToolResult(content=f"采样失败：{message}", data=None)

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    header = f"`{table_name}` 采样 {len(rows)} 行" + (f"（WHERE {clause}）" if clause else "") + "："
    content = header + "\n\n" + _format_rows_as_markdown(columns, rows, SAMPLE_ROWS_HARD_CAP)
    return ToolResult(content=content, data=result)


@tool()
def execute_sql(datasource_id: int, sql: str) -> ToolResult:
    """在当前数据源上执行只读 SQL 并返回结果。

    Args:
        sql: 要执行的 SELECT 语句；非只读会被拒绝。
    """
    from src.datasource.db.db import execute_sql as db_execute_sql

    db_type, config, _ = _load_datasource(datasource_id)
    success, message, result = db_execute_sql(db_type=db_type, config=config, sql=sql)
    if not success:
        return ToolResult(
            content=f"SQL 执行失败：{message}",
            data={"sql": sql, "error": message},
        )

    if not isinstance(result, dict) or "rows" not in result:
        return ToolResult(
            content=f"SQL 执行成功，影响 {result.get('row_count', 0) if isinstance(result, dict) else 0} 行。",
            data={"sql": sql, **(result if isinstance(result, dict) else {})},
        )

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    preview = _format_rows_as_markdown(columns, rows, EXECUTE_SQL_PREVIEW_ROWS)
    content = f"SQL 执行成功，返回 {len(rows)} 行：\n\n{preview}"
    return ToolResult(
        content=content,
        data={"sql": sql, "columns": columns, "rows": rows, "row_count": len(rows)},
    )


@tool()
def find_related_datasources(question: str) -> ToolResult:
    """列出全部可用数据源，供 LLM 根据问题自选。

    Note:
        MVP 阶段暂不做向量匹配，先返回全量 active 数据源；后续接入 embedding
        后再做真正的 top-k 召回。
    """
    from src.common.core.database import get_db_session
    from src.datasource.crud import crud_datasource

    with get_db_session() as session:
        all_ds = crud_datasource.get_datasources(session=session, skip=0, limit=200)
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

    当前清单（8 件）：
    - ``list_tables`` / ``find_related_tables`` / ``describe_table`` / ``sample_rows``
      / ``execute_sql``：对接数据源的 Schema 探查 & SQL 执行；
    - ``find_related_datasources``：多数据源场景的"选源"启发式；
    - ``recent_questions``：看自己在该数据源的历史问题，启发追问；
    - ``calculate``：**纯算术沙盒**（asteval），用于百分比/同比/均值等后处理
      运算——LLM 心算容易错，剥离给确定性求值器。

    每次调用返回新 list，避免在多会话场景下共享同一组引用造成状态串扰。
    """
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
    ]


def build_default_toolpack(
    *,
    datasource_id: int | None = None,
    user_id: int | None = None,
    include_terminate: bool = True,
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
    return pack.bind(**bindings) if bindings else pack
