# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Awesome-DB is a FastAPI + LLM natural-language-to-SQL (NL2SQL) system. Users ask questions in natural language; the system generates SQL, validates/formats it, executes it on a configured datasource, and returns structured results plus visualization/analysis reports. Chat supports both normal and SSE streaming modes.

The current branch (`edu`) extends the base with an education-focused student-exam analysis feature (multi-report grouping, audience switching, PDF/docx export) and permission/workspace/user modules.

### Related project: edu-offline

A companion Electron-based desktop app at `../related-edu-offline/` (or `edu-offline-app/` in this branch's root). Its role is data maintenance for the education module:

| Aspect | Web (`awesome-data`) | Offline (`edu-offline`) |
|--------|---------------------|------------------------|
| Purpose | Smart NL2SQL query & report generation | Teacher-facing data maintenance |
| Data flow | Consumes anonymized `tb_score` data | Manages raw school/student/exam/knowledge/score data |
| Users | Administrators, school leaders, teachers | Teachers (data entry operators) |
| Key operations | Agent-based chat, report viewing, PDF/docx export | Upload student info, exam papers, knowledge points, scores; export anonymized score details |

**Offline app data pipeline:**
1. Teacher uploads raw data (school info, student info, exam papers, knowledge points, student scores)
2. App stores and maintains this data locally
3. Teacher triggers export — the app **anonymizes** (desensitizes) student personally identifiable information
4. The anonymized `tb_score` / `tb_score_detail` data is uploaded to the web database
5. The web app's education module can then query and generate reports on this data

> **Note:** The offline app source lives in `../related-edu-offline/` (separate repository). The `edu-offline-app/` directory in this repo's root exists as a deployment/local reference placeholder.

## Commands

### Backend (Python ≥3.11, uv-managed)
```bash
uv sync                                  # install deps
uv sync --extra dev                      # install + dev deps (pytest, ruff)
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000   # run backend
uv run pytest                            # run all tests (testpaths=tests, pythonpath=.)
uv run pytest tests/agent/test_planner.py            # single test file
uv run pytest tests/agent/test_planner.py::test_xxx # single test
uv run pytest --cov=src --cov-report=term-missing    # coverage
uv run ruff check .                       # lint (line-length=100, py311, rules E/F/I/N/W)
uv run python -m src.agent.smoke --datasource-id 1 "本月订单最多的前三名用户是谁"  # agent smoke test
```

### Frontend (React — the active frontend on this branch, Next.js 14)
```bash
cd frontend-react
npm install
npm run dev        # dev server on :3001 (WATCHPACK_POLLING=true — for WSL/shared-folder setups)
npm run devW       # Windows variant (no WATCHPACK_POLLING)
npm run build / npm run start
npm run lint       # next lint
npm run typecheck  # tsc --noEmit
```
Tech stack: **Next.js 14 (pages router) + React 18 + Ant Design 5 + @antv/g2 charts + Tailwind CSS + TypeScript**. Report export uses html2canvas + jspdf (PDF) and xlsxwriter/docx (server-side Office export). Chat SSE via `src/utils/sse.ts`. Backend URL is configured via `frontend-react/.env.local` (`NEXT_PUBLIC_API_BASE_URL`, plus `NEXT_PUBLIC_DEFAULT_DATASOURCE_ID` and `NEXT_PUBLIC_AGENT_MODE`).

### Frontend (Vue — legacy MVP at `frontend/`)
```bash
cd frontend && npm install && npm run dev   # :5173, VITE_API_BASE_URL in .env.local
```

### Database migrations (Alembic)
```bash
uv run alembic upgrade head     # apply
uv run alembic revision --autogenerate -m "msg"
```

### Cross-project commands (edu-offline companion)
```bash
# When working across both projects, edu-offline lives at:
#   ../related-edu-offline/
cd ../related-edu-offline && npm install && npm run dev   # launch offline app
```

## Architecture

### Backend layout (`src/`)
Each top-level module under `src/` is a feature package exposing `api/`, `crud/`, `models/`, `service/` layers. The FastAPI app is built in `src/main.py` (logging + trace_id factory installed at module import; DB init and Agent resource registration happen in the `lifespan`).

- `system/` — auth (register/login/me), JWT, plus `permission`, `workspace`, `user` routers added on this branch.
- `datasource/` — datasource CRUD, connection testing (`db/db.py`), config encryption/decryption (AES), SQL execution across many DB types (PostgreSQL, MySQL, ClickHouse, Elasticsearch, Oracle, SQL Server, Redshift). Service layer (`service/`) adds row/column-level permission enforcement (`execute_with_permission.py`) and LLM-SQL auto-fix (`sql_auto_fix.py`).
- `chat/` — NL2SQL endpoints (`generate-sql`, `execute-sql`, `chat-stream`), conversation CRUD/history. `service/team_graph/` holds the LangGraph StateGraph team orchestrator.
- `agent/` — the AI/agent layer:
  - `awel/` — DAG + operator abstractions (DB-GPT-style AWEL).
  - `core/` — `base_agent.py` (ConversableAgent with 5-stage loop: thinking → review → act → verify → self-optimization), `agent.py` (AgentMessage, AgentGenerateContext), `profile.py` (ProfileConfig dataclass for agent identity), `action/base.py` (Action, ActionOutput), `memory/` (AgentMemory).
  - `resource/` — `ResourceManager` + `ToolPack` registry; `tool/` has `pack.py`, `builtin`, `business` (6 production tools: sample_rows, execute_sql, chart builder, stats calculator, report renderer, etc.), `calc`, `function_tool`. `manager.py::install_default_resources()` is called from lifespan (kept out of module import so tests can import `main` without side effects).
  - `expand/` — team agents: `PlannerAgent`, `DataAnalystAgent`, `CharterAgent`, `SummarizerAgent`, `UserProxyAgent`, `EchoAgent`, `ToolAgent`. Wired through `ChatAwelTeam` route decision.
  - `adapter/llm_adapter.py` — bridges `src.llm.service` to the agent `LlmClient` protocol (message format conversion, observation truncation, MiniMax content-safety error handling).
  - `education/` — branch-specific student-exam analysis: `orchestrator.py` (ReportOrchestrator, ReportIntentResolver), `student_exam.py`, `comprehensive.py`, `charts.py`, `stats.py` (pure-function KPI computation), `query_parse.py`, `schema_mapping.py`, `report_types.py`, `templates.py`, `config_store.py`, `tools.py` (ReAct tools for LLM), `config.py`, `anomaly_persistence.py`, `models_anomaly.py`, `data_adapter.py`, `prompt_context.py`, `capability.py`, `cross_analysis.py`, `dimension_parse.py`, `aggregation.py`, `school_intervention.py`, `group_feature.py`, `trend_tracking.py`, `subject_diagnosis.py`, `knowledge_tier.py`, `diagnostic_report.py`, `score_import.py`, plus `api.py` (the `education_router`).
  - `audit/tool_call_log.py` — records every agent tool invocation.
  - `smoke.py` — CLI smoke test for end-to-end Agent + LLM + datasource verification.
  - `util/` — `function_utils.py`, `json_parser.py`.
- `common/` — `core/config.py` (pydantic-settings `Settings`, `lru_cache`d via `get_settings()`), `core/database.py` (engine, session factory, `init_db()`, lightweight column migration via `_ensure_columns()`, `get_session()` FastAPI dependency), `core/trace.py` (trace_id LogRecord factory), `core/security.py` (password hashing, token creation), `middlewares/exception.py`, `schemas/` (unified response `success_response()`), and `router.py` which aggregates **all** routers under the `/api/v1` prefix. Add new routers there.
- `llm/` — LLM provider abstraction (`base.py`, `openai.py`, `ollama.py`, `service.py`); OpenAI/Ollama-compatible (also supports DashScope via `dashscope` dependency).
- `audit/` — operation-level audit logging with three endpoints (access, operation, login logs) under `api/audit.py`, plus CRUD in `crud/audit.py`, service in `service/`, models in `models/`.
- `db/`, `templates/` — SQL-gen helpers and Jinja2 prompt templates (`templates/sql_gen_prompt.py`).

### Chat execution modes
`POST /api/v1/chat/chat-stream` is the unified entry point. `agent_mode` selects:
- **legacy** (`single`) → `SQLGenerator` hand-written coroutine.
- **agent** → single ReAct agent.
- **team** → `ChatAwelTeam` (AWELBaseManager) with `Planner → DataAnalyst → Charter → Summarizer (→ ToolAgent)` agents, each backed by a `ResourcePack` (DatasourceResource + ToolPack) and `GptsMemory`/`AgentMemory` (persisted to `gpts_plan`/`gpts_message`/`tool_registry`).

`Settings.team_orchestrator` switches the team implementation: `"langgraph"` (default, StateGraph in `chat/service/team_graph/`) vs `"legacy"` (hand-written coroutines). Pick via env var `TEAM_ORCHESTRATOR`.

### Router registration
All routers are centralized in `common/router.py::register_routers()`. Each feature module exposes a `router` (e.g. `system.api.system.router`), imported there and registered under `/api/v1`. **Never** call `app.include_router` directly in feature modules.

To add a new API router:
1. Create the router in `src/<module>/api/<name>.py` with `APIRouter(prefix="/<name>")`.
2. Import and append it to the list in `common/router.py::get_all_routers()`.

### Agent tooling
Tools are `@tool`-decorated functions collected into a `ToolPack` via `build_default_toolpack()`. The template pack is registered with `ResourceManager` during `lifespan`. Each request binds runtime context (datasource_id, user_id) via `pack.bind(...)` — the template pack remains read-only shared.

**Design philosophy** (from `business.py`):
- **Business failures don't raise**: SQL errors, empty results, missing tables return a normal `ToolResult` so the LLM sees the error in its ReAct observation and self-corrects. Only framework-level errors (datasource not found, illegal params) throw, letting `ToolAction` classify as `is_exe_success=False` and trigger a retry.
- **SQL safety**: `_validate_read_only_select()` uses `sqlglot` AST parsing to reject any non-SELECT statement, blocking INSERT/UPDATE/DELETE/DDL via `;` injection or CTE tricks.
- Tools open their own short-lived DB transactions (`with get_db_session() as s`), never cross threads.

New tools should be:
1. Registered via `@tool` decorator / FunctionTool in `agent/resource/tool/business.py`.
2. Automatically included in `build_default_toolpack()` (or a new pack registered via `install_default_resources()`).

### Education module architecture
The `agent/education/` module implements a deterministic report pipeline (Phase 3+):

```
User question → ReportIntentResolver (rule-based, no LLM) → ReportSpec
    → ReportOrchestrator.run_spec(spec)
        → fetch score rows via callback (SQL)
        → compute stats (pure functions in stats.py)
        → build charts (charts.py, using build_chart_option)
        → render HTML via Jinja2 template (templates.py / select_report_template)
```

Report types map to `_fill_*` methods on `ReportOrchestrator`:
`CLASS_OVERVIEW`, `GRADE_COMPARISON`, `SUBJECT_DIAGNOSIS`, `STUDENT_PROFILE`, `TREND_TRACKING`, `TIER_ALERT`, `GROUP_FEATURE`, `COMPREHENSIVE`, `DIAGNOSTIC_REPORT`.

ReAct-agent-accessible tools live in `tools.py` (wrapping `stats.py`, `charts.py`, `schema_mapping.py`, `templates.py`) — LLM calls these via `@tool()` decorator rather than performing computation inline.

Key patterns:
- **`ReportIntentResolver`** maps natural language to `ReportSpec` via keyword matching — no LLM overhead. Falls back to `class_overview`.
- **`ReportOrchestrator`** depends on injected `execute_sql` and `resolve_schema` callbacks — testable with mocks.
- Reports are rendered from Jinja2 templates in `agent/resource/templates/education/`.
- `schema_mapping.py` handles both `"config_edu"` (normalized schema from `tb_score`/`tb_school`/`tb_exam`) and `"wide"` (denormalized wide-table) modes.
- `stats.py` exports pure functions only (no LLM calls, no I/O). `compute_score_stats` is the core KPI engine.
- `tool_call_log` table records every agent tool invocation for audit/traceability.

### Configuration persistence
`EducationConfig` (score thresholds, pass/excellent ratios, anomaly rules) uses a layered store:
1. System DB table `edu_anomaly_config` (via `anomaly_persistence.py`) — first priority.
2. Falls back to env / code defaults (`config.py::load_config()`).
3. Process-level override cache in `config_store.py` with thread lock.
4. API at `GET/PUT /api/v1/education/report-config`.

### LangGraph team graph
When `team_orchestrator=langgraph` (default), the team is a compiled `StateGraph` in `chat/service/team_graph/`:
```
START → planner → sub_tasks_loop → charter → summarizer → persist_success → END
                        ↓ (on failure)
                   persist_failure → END
```
Each node is a LangChain `Runnable` that reads/writes `TeamState` (a `TypedDict` with `messages`, `plans`, `plan_items`, `sub_phases`, `chart_config`, `summary_text`, `upstream_report_data`, `overall_error`, `record_id`, and `constraints_ctx` — see `chat/service/team_graph/state.py`).

### Logging / tracing
`common/core/trace.py` installs a `LogRecord` factory so every log line carries a `[trace_id]`. Logging is configured once at module import in `main.py` (format `%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s`, `force=True`). Uvicorn's own loggers are not overridden.

The `audit/` module enriches this with operation-level audit logging: `@audit_access` decorator on API endpoints (`audit/service/decorators.py`), `tool_call_log` table for agent tool invocations, and login audit records with three log types: access, operation, login.

### MCP server
A `fastapi-mcp` server is mounted via `fastapi-mcp` dependency — the FastAPI app exposes tools for external MCP clients (e.g. Claude Desktop). Configured in `main.py`.

### Configuration
All runtime config comes from `.env` via `common/core/config.py::Settings` (loaded by `get_settings()`, cached). Key vars: `DATABASE_URL` (PostgreSQL default), `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`, `TEAM_ORCHESTRATOR`. Copy `.env.example` → `.env`.

### Workspace / permission model
Multi-tenant via workspace (`oid`). Users belong to one or more workspaces (`sys_user_ws`). Data sources are scoped per workspace. Permission APIs (`system/api/permission.py`, `datasource/api/permission.py`) enforce access at datasource, table, and field levels. The `edu_permission` extension (`system/api/edu_permission.py`, `datasource/service/edu_permission.py`) scopes education data by school and class list.

Auth helpers in `system/authz.py` — `is_platform_admin()`, `is_workspace_admin()`, `can_manage_data_permissions()`, `bypasses_column_visibility()` and `bypasses_data_row_column_scope()` for authorization checks.

## Conventions

- **Python target**: 3.11+, `ruff` lint (E/F/I/N/W, line-length 100, isort with first-party `awesome`). Run `uv run ruff check .` before committing.
- **Tests**: pytest, `testpaths=["tests"]`, `pythonpath=["."]`. Tests live under `tests/<module>/` mirroring `src/`. Test files are named `test_<module>.py`. The agent layer has broad coverage — when adding agent/education behavior, prefer adding a test under `tests/agent/`.
- **Test patterns**: tests use `pytest-asyncio` for async tests; `conftest.py` fixtures in `tests/audit/` set up DB tables. Education tests use `MockOrchestrator` with mocked `execute_sql`/`resolve_schema` callbacks. A regression test file (`test_education_regression.py`) guards known edge cases.
- **Routers**: every public API router is wired through `common/router.py::register_routers()` under `/api/v1`. Don't call `app.include_router` directly in feature modules.
- **Agent tooling**: register new tools as `@tool`-decorated functions / ToolPacks and let `install_default_resources()` pick them up rather than invoking LLM-based tools ad hoc. Importing `main` must not trigger tool registration (it lives in `lifespan`).
- **Education module**: add new report types by extending `ReportType` enum in `report_types.py`, adding a keyword entry in `ReportIntentResolver`, implementing a `_fill_*` method in `ReportOrchestrator`, and creating the corresponding Jinja2 template in `agent/resource/templates/education/`.
- **`__all__`**: public API modules and key domain modules are expected to expose `__all__` for their public symbols (as seen in `orchestrator.py`, `config_store.py`, `manager.py`).
- **Audit decorator**: use `@audit_access(...)` from `audit/service/decorators.py` on endpoints that need operation audit logs.
- **Database migrations**: Alembic migrations live in `alembic/versions/`, named with date prefix (`20260721_01_edu_anomaly_config.py`). The `init_db()` function in `common/core/database.py` also runs lightweight column-level migrations via `_ensure_columns()` for dev environments.
- **`get_settings()`**: always use `from common.core.config import get_settings` — the function is `lru_cache`d, never instantiate `Settings()` directly.
- **`get_session()`** FastAPI dependency: use `from common.core.database import get_session` for request-scoped DB sessions. Use `get_db_session()` context manager for non-request code (background tasks, scripts).

## Windows notes (from global rules)
- PowerShell: set UTF-8 first — `chcp 65001; [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)`.
- Don't assume source-file corruption from terminal mojibake; validate bytes with strict UTF-8 decoding before rewriting files containing non-ASCII (much of this repo is Chinese-language).

## References
- `README.md` — full feature list, API examples, architecture diagrams.
- `MVP_PLAN.md` — planning doc.
- `.cursor/rules/karpathy-guidelines.mdc` — behavioral guidelines (simplicity, surgical changes, surface assumptions, goal-driven with verifiable success criteria). Follow this spirit.
- `../related-edu-offline/` — companion Electron offline app for education data maintenance (separate repo).