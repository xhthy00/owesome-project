"""业务工具单元测试。

所有底层数据库调用通过 monkeypatch 拦截，工具逻辑在内存里验证：
- schema 格式化 / 采样 / SQL 执行 / 错误路径
- bindings 注入后 LLM 视角看不到 datasource_id / user_id
- SQL 执行失败返回正常 ToolResult（LLM observation 驱动自修正），而非抛异常
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent.resource.tool import business as biz


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fake_datasource(monkeypatch):
    """注入一个假的 datasource，返回其"加载器"调用计数。"""
    calls = {"loaded": 0}

    def fake_load(datasource_id: int):
        calls["loaded"] += 1
        if datasource_id == 999:
            raise ValueError(f"datasource not found: id={datasource_id}")
        return "pg", {"host": "localhost"}, f"ds-{datasource_id}"

    monkeypatch.setattr(biz, "_load_datasource", fake_load)
    return calls


def _fake_schema_factory(tables):
    """方便 find_related_tables 多组测试共用的 get_schema_info stub。"""
    def _fake(db_type, config):
        return tables
    return _fake


def test_find_related_tables_ranks_by_hit_count(fake_datasource, monkeypatch):
    schema = [
        {
            "name": "student_score",
            "comment": "学生考试成绩",
            "fields": [
                {"name": "math", "comment": "数学成绩"},
                {"name": "reading", "comment": "阅读成绩"},
            ],
        },
        {
            "name": "teacher_info",
            "comment": "教师信息",
            "fields": [{"name": "name", "comment": "姓名"}],
        },
        {
            "name": "orders",
            "comment": "订单",
            "fields": [{"name": "id", "comment": ""}],
        },
    ]
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", _fake_schema_factory(schema))

    result = _run(biz.find_related_tables.execute(datasource_id=1, question="分析学生的数学成绩"))

    # student_score 必须排在第一（同时命中"学生"、"成绩"、"数学"）
    assert result.data[0]["name"] == "student_score"
    assert result.data[0]["score"] >= 2
    # 未命中的 orders 不应出现
    names = [it["name"] for it in result.data]
    assert "orders" not in names


def test_find_related_tables_english_token_matching(fake_datasource, monkeypatch):
    schema = [
        {"name": "user_orders", "comment": "", "fields": [{"name": "order_id", "comment": ""}]},
        {"name": "log_events", "comment": "", "fields": [{"name": "event_id", "comment": ""}]},
    ]
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", _fake_schema_factory(schema))

    result = _run(biz.find_related_tables.execute(datasource_id=1, question="show recent user orders"))
    assert result.data[0]["name"] == "user_orders"


def test_find_related_tables_fallback_when_no_hit(fake_datasource, monkeypatch):
    """问题关键词完全不命中时，返回兜底全量（截断到 limit），保证 LLM 能继续。"""
    schema = [
        {"name": "alpha", "comment": "", "fields": []},
        {"name": "beta", "comment": "", "fields": []},
    ]
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", _fake_schema_factory(schema))

    result = _run(biz.find_related_tables.execute(datasource_id=1, question="完全不相关的问题 gamma"))
    assert "未命中关键词" in result.content
    names = [it["name"] for it in result.data]
    assert set(names) == {"alpha", "beta"}
    assert all(it["score"] == 0 for it in result.data)


def test_find_related_tables_limit_clamp(fake_datasource, monkeypatch):
    """limit 超过 hard cap(20) 应被截断；``limit=0`` 等价于"缺省"走默认 10。"""
    schema = [
        {"name": f"t{i}", "comment": "学生", "fields": []}
        for i in range(30)
    ]
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", _fake_schema_factory(schema))

    result_big = _run(biz.find_related_tables.execute(datasource_id=1, question="学生", limit=999))
    assert len(result_big.data) <= 20, "limit 超过硬上限 20 未被截断"

    # limit=0 走 `int(0 or DEFAULT) == 10` 的 Pythonic 默认语义
    result_zero = _run(biz.find_related_tables.execute(datasource_id=1, question="学生", limit=0))
    assert 1 <= len(result_zero.data) <= 20

    # 负值会被 max(1, ...) 抬到 1
    result_neg = _run(biz.find_related_tables.execute(datasource_id=1, question="学生", limit=-5))
    assert len(result_neg.data) == 1


def test_find_related_tables_ignores_short_tokens(fake_datasource, monkeypatch):
    """长度 < 2 的 token（"的"/"是"/"a"）不应命中所有表。"""
    schema = [
        {"name": "orders", "comment": "", "fields": [{"name": "a", "comment": ""}]},
        {"name": "users", "comment": "", "fields": [{"name": "b", "comment": ""}]},
    ]
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", _fake_schema_factory(schema))

    # 全是短词，应走"未命中"兜底
    result = _run(biz.find_related_tables.execute(datasource_id=1, question="a 的 是 b"))
    assert "未命中关键词" in result.content or all(it["score"] == 0 for it in result.data)


def test_list_tables_formats_content(fake_datasource, monkeypatch):
    def fake_schema(db_type, config):
        return [
            {"name": "users", "comment": "用户表", "fields": []},
            {"name": "orders", "comment": "", "fields": []},
        ]

    monkeypatch.setattr("src.datasource.db.db.get_schema_info", fake_schema)

    result = _run(biz.list_tables.execute(datasource_id=1))
    assert "ds-1" in result.content
    assert "- users — 用户表" in result.content
    assert "- orders" in result.content
    assert result.data == [
        {"name": "users", "comment": "用户表"},
        {"name": "orders", "comment": ""},
    ]


def test_list_tables_empty(fake_datasource, monkeypatch):
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", lambda *_a, **_kw: [])
    result = _run(biz.list_tables.execute(datasource_id=1))
    assert "暂无可见表" in result.content
    assert result.data == []


def test_describe_table_existing(fake_datasource, monkeypatch):
    def fake_schema(*_a, **_kw):
        return [
            {
                "name": "users",
                "comment": "用户",
                "fields": [
                    {"name": "id", "type": "bigint", "comment": "主键"},
                    {"name": "name", "type": "varchar(64)", "comment": ""},
                ],
            }
        ]

    monkeypatch.setattr("src.datasource.db.db.get_schema_info", fake_schema)

    result = _run(biz.describe_table.execute(datasource_id=1, table_name="users"))
    assert "表 `users`" in result.content
    assert "| id | bigint | 主键 |" in result.content
    assert result.data["name"] == "users"


def test_describe_table_missing_returns_hint(fake_datasource, monkeypatch):
    monkeypatch.setattr(
        "src.datasource.db.db.get_schema_info",
        lambda *_a, **_kw: [{"name": "x", "comment": "", "fields": []}],
    )
    result = _run(biz.describe_table.execute(datasource_id=1, table_name="ghost"))
    assert "不存在" in result.content
    assert "x" in result.content
    assert result.data is None


def test_sample_rows_builds_safe_sql(fake_datasource, monkeypatch):
    captured = {}

    def fake_exec(db_type, config, sql):
        captured["sql"] = sql
        captured["db_type"] = db_type
        return True, "ok", {"columns": ["id"], "rows": [[1], [2]], "row_count": 2}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)

    result = _run(biz.sample_rows.execute(datasource_id=1, table_name="users", limit=3))
    assert captured["sql"] == 'SELECT * FROM "users" LIMIT 3'
    assert "| id |" in result.content
    assert result.data["row_count"] == 2


def test_sample_rows_caps_limit_to_llm_max(fake_datasource, monkeypatch):
    """超出 LLM 可见上限的 limit 会被 clamp；兜底 LLM 胡传巨大的值。"""
    captured = {}

    def fake_exec(db_type, config, sql):
        captured["sql"] = sql
        return True, "ok", {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    _run(biz.sample_rows.execute(datasource_id=1, table_name="t", limit=999))
    assert captured["sql"].endswith(f"LIMIT {biz.SAMPLE_ROWS_LLM_MAX}")


def test_sample_rows_rejects_injection(fake_datasource, monkeypatch):
    monkeypatch.setattr("src.datasource.db.db.execute_sql", lambda *_a, **_kw: (True, "", {}))
    with pytest.raises(ValueError):
        _run(biz.sample_rows.execute(datasource_id=1, table_name="users; DROP TABLE x"))


def test_sample_rows_execution_failure_returns_observation(fake_datasource, monkeypatch):
    monkeypatch.setattr(
        "src.datasource.db.db.execute_sql",
        lambda *_a, **_kw: (False, "permission denied", None),
    )
    result = _run(biz.sample_rows.execute(datasource_id=1, table_name="users"))
    assert "采样失败" in result.content
    assert result.data is None


def test_sample_rows_accepts_safe_where_clause(fake_datasource, monkeypatch):
    """典型的 AND/OR 组合 where 能正确拼进 SQL 并传给 DB。"""
    captured = {}

    def fake_exec(db_type, config, sql):
        captured["sql"] = sql
        return True, "ok", {"columns": ["id", "status"], "rows": [[1, "active"]], "row_count": 1}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    result = _run(
        biz.sample_rows.execute(
            datasource_id=1,
            table_name="users",
            limit=5,
            where_clause="status = 'active' AND created_at > '2024-01-01'",
        )
    )
    assert captured["sql"] == (
        "SELECT * FROM \"users\" WHERE status = 'active' "
        "AND created_at > '2024-01-01' LIMIT 5"
    )
    assert "WHERE status = 'active'" in result.content


def test_sample_rows_rejects_multistatement_where(fake_datasource, monkeypatch):
    """用 ``;`` 塞第二条 DELETE 应被拦截，不会碰到 execute_sql。"""
    calls = {"count": 0}

    def fake_exec(*_a, **_kw):
        calls["count"] += 1
        return True, "ok", {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    result = _run(
        biz.sample_rows.execute(
            datasource_id=1,
            table_name="users",
            where_clause="1=1; DELETE FROM users",
        )
    )
    assert "采样失败" in result.content and "where_clause 不合法" in result.content
    assert calls["count"] == 0, "被拒绝的 where 不应触达 DB"


def test_sample_rows_rejects_write_op_buried_in_where(fake_datasource, monkeypatch):
    """子查询里藏 DML 也被拦截——sqlglot 子树扫描覆盖。"""

    def fake_exec(*_a, **_kw):
        return True, "ok", {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    result = _run(
        biz.sample_rows.execute(
            datasource_id=1,
            table_name="users",
            where_clause="id IN (SELECT id FROM tmp); UPDATE users SET x=1",
        )
    )
    assert "采样失败" in result.content


def test_sample_rows_rejects_unparseable_where(fake_datasource, monkeypatch):
    """语法噪声也应被拒（注意 sqlglot 对某些残缺 SQL 会尝试修复，
    所以挑一个明显无法拼成 SELECT 的 case——多条 ``;``）。"""

    def fake_exec(*_a, **_kw):
        return True, "ok", {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    result = _run(
        biz.sample_rows.execute(
            datasource_id=1,
            table_name="users",
            where_clause="1=1; SELECT 2; SELECT 3",
        )
    )
    assert "采样失败" in result.content


def test_sample_rows_empty_where_clause_is_treated_as_no_filter(fake_datasource, monkeypatch):
    """``where_clause=""`` / 全空白应等价于不过滤；同时不会做 sqlglot 解析。"""
    captured = {}

    def fake_exec(db_type, config, sql):
        captured["sql"] = sql
        return True, "ok", {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    _run(biz.sample_rows.execute(datasource_id=1, table_name="t", where_clause="   "))
    assert "WHERE" not in captured["sql"]
    assert captured["sql"] == f'SELECT * FROM "t" LIMIT {biz.SAMPLE_ROWS_DEFAULT}'


def test_execute_sql_success(fake_datasource, monkeypatch):
    def fake_exec(db_type, config, sql):
        return True, "ok", {
            "columns": ["c"],
            "rows": [["a"], ["b"]],
            "row_count": 2,
        }

    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_exec)
    result = _run(biz.execute_sql.execute(datasource_id=1, sql="SELECT c FROM t"))
    assert "返回 2 行" in result.content
    assert result.data["sql"] == "SELECT c FROM t"
    assert result.data["row_count"] == 2


def test_execute_sql_failure_returns_observation_not_exception(fake_datasource, monkeypatch):
    monkeypatch.setattr(
        "src.datasource.db.db.execute_sql",
        lambda *_a, **_kw: (False, "syntax error at or near 'SELEC'", None),
    )
    result = _run(biz.execute_sql.execute(datasource_id=1, sql="SELEC * FROM t"))
    assert result.content.startswith("SQL 执行失败")
    assert "syntax error" in result.content
    assert result.data["error"].startswith("syntax error")


def test_render_html_report_inline_sanitizes_script(fake_datasource):
    result = _run(
        biz.render_html_report.execute(
            datasource_id=1,
            html='<div onclick="alert(1)">ok</div><script>alert(2)</script>',
            title="demo",
        )
    )
    assert "已生成" in result.content
    assert result.data["output_type"] == "html"
    assert result.data["title"] == "demo"
    assert isinstance(result.data.get("chunks"), list) and result.data["chunks"][0]["output_type"] == "html"
    assert "<script" in result.data["html"].lower()
    assert "onclick=" not in result.data["html"].lower()


def test_render_html_report_template_mode(fake_datasource, monkeypatch):
    base = Path(__file__).resolve().parents[2]
    template_dir = base / "src" / "agent" / "resource" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / "unit_test_template.html"
    template_path.write_text("<h1>{{TITLE}}</h1><p>{{BODY}}</p>", encoding="utf-8")
    monkeypatch.setattr(biz, "_report_template_dir", lambda: template_dir)
    try:
        result = _run(
            biz.render_html_report.execute(
                datasource_id=1,
                template_name="unit_test_template.html",
                data={"TITLE": "T", "BODY": "B"},
            )
        )
    finally:
        template_path.unlink(missing_ok=True)
    assert result.data["mode"] == "template"
    assert "<h1>T</h1>" in result.data["html"]


def test_render_html_report_template_path_and_string_data(fake_datasource, monkeypatch):
    base = Path(__file__).resolve().parents[2]
    template_dir = base / "src" / "agent" / "resource" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / "unit_test_template_2.html"
    template_path.write_text("<div>{{A}}-{{B}}</div>", encoding="utf-8")
    monkeypatch.setattr(biz, "_report_template_dir", lambda: template_dir)
    try:
        result = _run(
            biz.render_html_report.execute(
                datasource_id=1,
                template_path="unit_test_template_2.html",
                data='{"A":"x","B":"y"}',
            )
        )
    finally:
        template_path.unlink(missing_ok=True)
    assert result.data["mode"] == "template"
    assert "<div>x-y</div>" in result.data["html"]


def test_render_html_report_template_name_without_suffix_uses_html(fake_datasource, monkeypatch):
    base = Path(__file__).resolve().parents[2]
    template_dir = base / "src" / "agent" / "resource" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / "score_analysis_report.html"
    original = template_path.read_text(encoding="utf-8") if template_path.exists() else None
    template_path.write_text("<div>{{TITLE}}</div>", encoding="utf-8")
    monkeypatch.setattr(biz, "_report_template_dir", lambda: template_dir)
    try:
        result = _run(
            biz.render_html_report.execute(
                datasource_id=1,
                template_name="score_analysis_report",
                data={"TITLE": "ok"},
            )
        )
    finally:
        if original is None:
            template_path.unlink(missing_ok=True)
        else:
            template_path.write_text(original, encoding="utf-8")
    assert result.data["mode"] == "template"
    assert "<div>ok</div>" in result.data["html"]


def test_render_html_report_template_failure_falls_back_to_inline(fake_datasource):
    result = _run(
        biz.render_html_report.execute(
            datasource_id=1,
            template_name="not_exists.html",
            html="<html><body>fallback</body></html>",
            title="fallback",
        )
    )
    assert result.data["mode"] == "inline"
    assert "fallback" in result.data["html"]


def test_render_html_report_file_mode_reads_workspace_file(fake_datasource):
    base = Path(__file__).resolve().parents[2]
    file_path = base / "tmp_report_test.html"
    file_path.write_text("<html><body>hello</body></html>", encoding="utf-8")
    try:
        result = _run(
            biz.render_html_report.execute(
                datasource_id=1,
                file_path=str(file_path),
                title="from-file",
            )
        )
    finally:
        file_path.unlink(missing_ok=True)
    assert result.data["mode"] == "file"
    assert "hello" in result.data["html"]


def test_render_html_report_file_mode_fallbacks_to_template_dir(fake_datasource, monkeypatch):
    base = Path(__file__).resolve().parents[2]
    template_dir = base / "src" / "agent" / "resource" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / "score_analysis_report.html"
    original = template_path.read_text(encoding="utf-8") if template_path.exists() else None
    template_path.write_text("<html><body>template-file-mode</body></html>", encoding="utf-8")
    monkeypatch.setattr(biz, "_report_template_dir", lambda: template_dir)
    try:
        result = _run(
            biz.render_html_report.execute(
                datasource_id=1,
                file_path="score_analysis_report.html",
                title="from-template-dir",
            )
        )
    finally:
        if original is None:
            template_path.unlink(missing_ok=True)
        else:
            template_path.write_text(original, encoding="utf-8")
    assert result.data["mode"] == "file"
    assert "template-file-mode" in result.data["html"]


def test_find_related_datasources_lists_active(monkeypatch):
    @contextlib.contextmanager
    def fake_session():
        yield object()

    def fake_list(*, session, skip=0, limit=200):
        return [
            SimpleNamespace(id=1, name="prod-pg", description="生产", type="pg", status="active"),
            SimpleNamespace(id=2, name="legacy", description="", type="mysql", status="inactive"),
            SimpleNamespace(id=3, name="warehouse", description="", type="pg", status=None),
        ]

    monkeypatch.setattr("src.common.core.database.get_db_session", fake_session)
    monkeypatch.setattr(
        "src.datasource.crud.crud_datasource.get_datasources",
        fake_list,
    )

    result = _run(biz.find_related_datasources.execute(question="销售额"))
    ids = [d["id"] for d in result.data]
    assert ids == [1, 3]
    assert "prod-pg" in result.content
    assert "legacy" not in result.content


def test_recent_questions_renders_list(monkeypatch):
    @contextlib.contextmanager
    def fake_session():
        yield object()

    def fake_questions(*, session, datasource_id, user_id, limit):
        assert datasource_id == 1 and user_id == 42 and limit == 3
        return ["Q1", "Q2", "Q3"]

    monkeypatch.setattr("src.common.core.database.get_db_session", fake_session)
    monkeypatch.setattr("src.chat.crud.chat.get_recent_questions", fake_questions)

    result = _run(
        biz.recent_questions.execute(datasource_id=1, user_id=42, limit=3)
    )
    assert result.data == ["Q1", "Q2", "Q3"]
    assert "- Q1" in result.content


def test_recent_questions_empty(monkeypatch):
    @contextlib.contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr("src.common.core.database.get_db_session", fake_session)
    monkeypatch.setattr(
        "src.chat.crud.chat.get_recent_questions",
        lambda **kw: [],
    )

    result = _run(biz.recent_questions.execute(datasource_id=1, user_id=1))
    assert "暂无历史问题" in result.content
    assert result.data == []


def test_build_default_toolpack_binds_and_hides_params(monkeypatch):
    pack = biz.build_default_toolpack(datasource_id=7, user_id=42)

    assert pack.bindings == {"datasource_id": 7, "user_id": 42}
    assert "list_tables" in pack
    assert "find_related_tables" in pack
    assert "execute_sql" in pack
    assert "calculate" in pack
    assert "terminate" in pack
    # 6 原业务工具 + find_related_tables + calculate + render_html_report + terminate = 10
    assert len(pack) == 10

    prompt = pack.render_prompt()
    assert "datasource_id(" not in prompt
    assert "user_id(" not in prompt
    assert "list_tables:" in prompt
    assert "terminate:" in prompt


def test_build_default_toolpack_without_terminate():
    pack = biz.build_default_toolpack(datasource_id=1, include_terminate=False)
    assert "terminate" not in pack
    # 6 原业务工具 + find_related_tables + calculate + render_html_report = 9（不含 terminate）
    assert len(pack) == 9


def test_build_default_toolpack_no_bindings():
    pack = biz.build_default_toolpack()
    assert pack.bindings == {}


def test_missing_datasource_raises_framework_error(monkeypatch):
    @contextlib.contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr("src.common.core.database.get_db_session", fake_session)
    monkeypatch.setattr(
        "src.datasource.crud.crud_datasource.get_datasource_by_id",
        lambda session, did: None,
    )
    with pytest.raises(ValueError, match="datasource not found"):
        _run(biz.list_tables.execute(datasource_id=999))
