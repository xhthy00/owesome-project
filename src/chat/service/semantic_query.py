"""Plan read-only queries against a published semantic model and reuse host permissions."""

import time
from difflib import get_close_matches
from threading import Lock
from urllib.parse import quote

import httpx
from fastapi.encoders import jsonable_encoder
from sqlglot import exp, parse, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from src.agent.education.privacy_mode import apply_result_privacy, apply_sql_privacy
from src.agent.education.line_reach import normalize_fraction_bars
from src.chat.service.semantic_permission_validator import (
    PermissionValidator,
    verify_compiled_permissions,
)
from src.common.core.config import DataSemanticBinding, Settings
from src.common.core.database import get_db_session
from src.datasource.db.db import execute_sql
from src.datasource.edu_connection import parse_edu_database_url
from src.datasource.service.education_authorization import (
    AuthorizationError,
    assert_workspace_member,
    context_for,
    manifest_for,
)
from src.datasource.service.query_permission import (
    apply_permissions_for_execute,
    collect_row_predicate_sqls,
    filter_exec_result_by_column_permissions,
    validate_sql_column_permissions,
)
from src.datasource.service.semantic_permission_contract import (
    CONTRACT_VERSION,
    PROVER_VERSION,
    SQL_GRAMMAR,
    permission_error_detail,
)
from src.datasource.service.sql_auto_fix import run_sql_with_auto_fix
from src.system.crud.crud_user import get_user_by_id
from system.authz import bypasses_data_row_column_scope
from system.workspace_scope import assert_datasource_accessible

_MAX_ROWS = 200
_MAX_DESCRIBE_NAMES = 12
_MAX_FIELD_ERRORS = 64
# A 503 means the planner/executor rejected the call before doing any work
# (busy, stopping or not ready), so a bounded retry is safe for plan and execute.
_SERVICE_UNAVAILABLE_RETRIES = 2
_SERVICE_RETRY_BACKOFF_SECONDS = 0.5
_DIALECTS = {"pg": "postgres", "mysql": "mysql"}
# The database can contain user-defined functions. Admit the analytical operations used by MDL.
_FUNCTIONS = frozenset(SQL_GRAMMAR.functions)

# These published views return cutoffs or grouped reach statistics. Keep the
# exception on the view, even when its definition reads the score fact table.
PUBLIC_LINE_REACH_VIEWS = {
    "v_exam_cutoff_lines": {"tb_fraction_bar"},
    "v_city_line_reach": {"tb_score_indicator"},
    "v_district_line_reach": {"tb_score_indicator"},
    "v_school_line_reach": {"tb_score_indicator"},
    "v_class_combo_line_reach": {"tb_fraction_bar", "tb_score_overview"},
    "v_line_reach_change": {"tb_exam_batch", "tb_score_indicator"},
    "v_tier_line_reach": {"tb_school", "tb_score_indicator"},
}

# 分数线/计划线是参考配置，任何有授权的账号都可读；区县/学校/班级/变化/分层的
# 达线明细属于业务数据，只允许市级（city）授权查询，否则会把本范围外的学校数据放出去。
CITYWIDE_ONLY_PUBLIC_VIEWS = frozenset(PUBLIC_LINE_REACH_VIEWS) - {"v_exam_cutoff_lines"}


def _is_citywide_authorization(context) -> bool:
    return context is not None and any(
        getattr(grant, "level", "") == "city" for grant in context.grants
    )


def _granted_districts(context) -> tuple[str, ...]:
    if context is None:
        return ()
    return tuple(
        sorted(
            {
                str(grant.district_id)
                for grant in context.grants
                if getattr(grant, "level", "") == "district"
                and getattr(grant, "district_id", None)
            }
        )
    )


def assert_public_line_reach_allowed(used_views, context) -> None:
    """公开达线视图同样受 V2 范围约束。

    - ``v_exam_cutoff_lines``（分数线配置）任何有授权账号都可读；
    - ``v_district_line_reach`` 对区县授权放行，执行时强制注入本区县过滤；
    - 其余学校/全市/变化/分层明细视图仅市级授权可读。
    """
    restricted = set(used_views) & CITYWIDE_ONLY_PUBLIC_VIEWS
    if not restricted or _is_citywide_authorization(context):
        return
    if restricted == {"v_district_line_reach"} and _granted_districts(context):
        return
    raise AuthorizationError(
        "AUTH_FEATURE_NOT_AVAILABLE",
        "该达线明细视图仅支持市级或本区县授权；请改用「达线看板」或本范围聚合口径",
        skip_label="达线率（学校/班级达线明细）",
        repair_hint=(
            "本账号范围无法查询任何 v_*_line_reach 明细视图，换用同族视图仍会被拒。"
            "该数据项已跳过：不要重复提交达线率查询，改用分数线阈值、本班/年级均分与分档分布作答。"
        ),
    )


def _direct_tables(select) -> list:
    tables = []
    for table in select.find_all(exp.Table):
        parent = table.parent
        while parent is not None and not isinstance(parent, exp.Select):
            parent = parent.parent
        if parent is select:
            tables.append(table)
    return tables


def _apply_public_line_reach_scope(tree, views, context) -> None:
    """区县账号读 ``v_district_line_reach`` 时强制注入本区县过滤。"""
    if context is None or _is_citywide_authorization(context):
        return
    districts = _granted_districts(context)
    if not districts or "v_district_line_reach" not in views:
        return
    # 编译后的物理 SQL 读的是视图底层表（PUBLIC_LINE_REACH_VIEWS 映射），
    # 因此过滤要加在底层表的 district 列上。
    targets = PUBLIC_LINE_REACH_VIEWS["v_district_line_reach"]
    for select in tree.find_all(exp.Select):
        for table in _direct_tables(select):
            if table.name.lower() not in targets:
                continue
            column = exp.column("district", table=table.alias_or_name, quoted=True)
            select.where(
                exp.In(this=column, expressions=[exp.Literal.string(item) for item in districts]),
                append=True,
                copy=False,
            )


class SemanticQueryError(ValueError):
    """A query that could not be planned or safely executed."""


class SemanticContractError(SemanticQueryError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.detail = permission_error_detail(code, message)


class SemanticViewDescriptionRequiredError(SemanticQueryError):
    """The caller can describe the view and resubmit the same SQL."""

    def __init__(self, message: str, names: list[str] | None = None):
        super().__init__(message)
        # 宿主据此自动补读字段，省掉一轮模型往返。
        self.names = list(names or [])


class SemanticFieldError(SemanticQueryError):
    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


class SemanticAuthorizationError(SemanticQueryError):
    def __init__(self, error: AuthorizationError):
        super().__init__(str(error))
        self.detail = error.detail


def _has_projection_star(tree):
    return any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star))


def _query(sql: str, dialect: str, *, compiled: bool = False):
    if not isinstance(sql, str) or len(sql) > SQL_GRAMMAR.max_sql_length:
        raise SemanticQueryError("语义 SQL 长度超出限制。")
    statements = parse(sql, read=dialect)
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise SemanticQueryError("只支持单条只读 SELECT 查询。")
    tree = statements[0]
    for node in tree.walk():
        if isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock, exp.Hint)):
            raise SemanticQueryError("计划包含不支持的 SQL 操作。")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise SemanticQueryError("不支持限定名称的函数。")
        # SQLGlot uses GroupConcat for both PostgreSQL STRING_AGG and MySQL GROUP_CONCAT.
        if isinstance(node, exp.Func) and not isinstance(
            node, (exp.Connector, exp.Exists, exp.GroupConcat)
        ):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
            if name not in _FUNCTIONS:
                raise SemanticQueryError(f"查询函数 {name} 尚未开放。")
    if _has_projection_star(tree):
        if not compiled:
            raise SemanticQueryError(
                "语义 SQL 请显式列出字段。用 describe_semantic_model 查看字段，COUNT(*) 可以直接使用。"
            )
        # Wren can emit SELECT * over CTEs even for explicit semantic projections.
        tree = qualify(tree, dialect=dialect, validate_qualify_columns=False, identify=False)
        if _has_projection_star(tree):
            raise SemanticQueryError("DataSemantic 物理计划包含无法展开的通配列。")
    return tree


def _sources(tree):
    return [
        (scope.expression, source)
        for scope in traverse_scope(tree)
        for _, source in scope.selected_sources.values()
        if isinstance(source, exp.Table)
    ]


def _check_sources(tree, allowed=None, *, schema=""):
    sources = _sources(tree)
    if not sources:
        raise SemanticQueryError("查询须使用已发布模型或视图中的数据。")
    if any(
        not isinstance(table.this, exp.Identifier)
        or (allowed is not None and table.name.lower() not in allowed)
        or table.catalog
        or (table.db and table.db.lower() != schema.lower())
        for _, table in sources
    ):
        if allowed is not None:
            raise SemanticQueryError(
                "语义 SQL 只能引用发布目录中的逻辑模型或视图。"
                "请使用不带 schema 的名称，并用 describe_semantic_model 查看字段。"
            )
        raise SemanticQueryError("DataSemantic 生成的物理计划引用了学情库允许范围之外的对象。")
    if allowed is not None and any(
        cte.alias_or_name.lower() in allowed for cte in tree.find_all(exp.CTE)
    ):
        raise SemanticQueryError("查询的 CTE 与注册表名冲突。")
    return sources


def _order_public_postgres_set(tree):
    """Sort computed keys outside a set operation, where PostgreSQL allows them."""
    if not isinstance(tree, exp.SetOperation) or not (order := tree.args.get("order")):
        return tree
    computed = [
        item.this for item in order.expressions
        if not isinstance(item.this, exp.Column)
        and not (isinstance(item.this, exp.Literal) and item.this.is_int)
    ]
    if not computed or not all(isinstance(item, exp.Case) for item in computed):
        return tree

    identifiers = []
    for item in tree.selects:
        identifier = item.args.get("alias") if isinstance(item, exp.Alias) else item.this
        if not isinstance(identifier, exp.Identifier):
            return tree
        identifiers.append(identifier)
    if len({item.name.lower() for item in identifiers}) != len(identifiers):
        return tree
    names = {item.name for item in identifiers}
    if any(column.table or column.name not in names for column in order.find_all(exp.Column)):
        return tree

    combined = tree.copy()
    outer = exp.select(*(exp.Column(this=item.copy()) for item in identifiers)).from_("ordered_results")
    for key in ("order", "limit", "offset"):
        outer.set(key, combined.args.get(key))
        combined.set(key, None)
    return outer.with_("ordered_results", as_=combined, copy=False)


def _check_published_columns(tree, dialect, published_columns):
    # Only check columns that can be attributed to a specific published relation. Wren still
    # resolves unqualified columns across joins, CTE outputs, and correlated scopes.
    tree = normalize_identifiers(tree.copy(), dialect=dialect)
    for scope in traverse_scope(tree):
        if scope.pivots:
            continue
        sources = {
            alias: published_columns[source.name.lower()]
            for alias, (reference, source) in scope.selected_sources.items()
            if isinstance(source, exp.Table)
            and source.name.lower() in published_columns
            and not reference.alias_column_names
        }
        single = None
        if len(scope.selected_sources) == 1 and not scope.can_be_correlated:
            single = next(iter(sources), None)
        for column in scope.columns:
            alias = column.table
            if not column.table and column.find_ancestor(exp.Select) is scope.expression:
                if column.name in scope.expression.named_selects and column.find_ancestor(
                    exp.Group
                ):
                    continue
                alias = single
            if alias not in sources:
                continue
            kind, name, fields = sources[alias]
            if column.name not in fields:
                alternatives = [
                    {"alias": other, "relation": relation, **other_fields[column.name]}
                    for other, (_, relation, other_fields) in sources.items()
                    if other != alias and column.name in other_fields
                ]
                matches = get_close_matches(column.name, fields, n=3)
                raise SemanticFieldError(
                    f"{kind} {name} 未发布字段 {column.name}。可用字段为 {', '.join(fields)}。"
                    "请核对字段口径及关联后修正查询。",
                    {
                        "code": "FIELD_WRONG_SOURCE" if alternatives else "FIELD_NOT_PUBLISHED",
                        "source": {"alias": alias, "relation": name},
                        "field": column.name,
                        "candidates": alternatives or [fields[match] for match in matches],
                    },
                )


class SemanticQuery:
    """One request's published context, bound to the authorized datasource and user."""

    def __init__(self, settings: Settings, datasource_id: int, workspace_oid: int, user_id: int):
        self.settings = settings
        self.datasource_id = datasource_id
        self.workspace_oid = workspace_oid
        self.user_id = user_id
        self._context = None
        self._context_binding = None
        self._metadata_lock = Lock()
        self._descriptions: dict[tuple, dict] = {}
        self._description_locks: dict[tuple, Lock] = {}
        self._field_errors: dict[tuple, tuple[str, dict]] = {}
        self._environment_permission = None

    def _permission_manifest(self):
        try:
            configured = manifest_for(self.datasource_id, getattr(self.settings, "semantic_permission_manifest_path", ""))
            if configured is not None or not getattr(self.settings, "database_edu_url", ""):
                return configured
            return self._environment_permission
        except AuthorizationError as exc:
            raise SemanticAuthorizationError(exc) from exc

    def _authorization(self, session, user):
        policy = self._permission_manifest()
        if policy is None:
            # 教育权限只有 V2 事实源；没有发布映射/授权就拒绝，不回退旧执行路径。
            raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "当前数据源没有有效的教育授权")
        binding = self._binding()
        if (policy.bundle_id, policy.manifest_hash) != (binding["bundle_id"], binding["manifest_hash"]):
            raise AuthorizationError("AUTH_MAPPING_MISSING", "权限映射与当前语义发布物不一致")
        assert_workspace_member(session, user, self.workspace_oid)
        return context_for(user, self.workspace_oid, self.datasource_id, policy, session=session, allow_empty=True)

    def authorization_context(self):
        # Legacy datasources keep their existing context contract.
        try:
            if not getattr(self.settings, "database_edu_url", "") and self._permission_manifest() is None:
                return None
            authorization = self._fresh_authorization()
            return authorization.model_context() if authorization is not None else None
        except AuthorizationError as exc:
            raise SemanticAuthorizationError(exc) from exc

    def _fresh_authorization(self):
        with get_db_session() as session:
            user = get_user_by_id(session, self.user_id)
            from src.datasource.service.education_source import (
                environment_manifest,
                has_environment_authorization,
            )
            if has_environment_authorization(user, self.workspace_oid, self.datasource_id):
                self._environment_permission = environment_manifest(self.workspace_oid, self.datasource_id, settings=self.settings)
            else:
                self._environment_permission = None
            if self._permission_manifest() is not None:
                assert_datasource_accessible(session, user, self.datasource_id, self.workspace_oid)
            return self._authorization(session, user)

    def _binding(self):
        binding = self.settings.datasemantic_bindings.get(self.datasource_id)
        if (
            binding is None
            and self.datasource_id == self.settings.datasemantic_datasource_id
            and self.settings.datasemantic_bundle_id
            and self.settings.datasemantic_manifest_hash
        ):
            binding = DataSemanticBinding(
                bundle_id=self.settings.datasemantic_bundle_id,
                manifest_hash=self.settings.datasemantic_manifest_hash,
            )
        if (
            not binding
            or not self.settings.datasemantic_url
            or not self.settings.datasemantic_token
        ):
            raise SemanticQueryError("当前数据源尚未绑定已发布的 DataSemantic 模型。")
        return binding.model_dump()

    def _call(self, method, path, binding, **kwargs):
        data: dict | None = None
        for attempt in range(_SERVICE_UNAVAILABLE_RETRIES + 1):
            try:
                response = httpx.request(
                    method,
                    f"{self.settings.datasemantic_url.rstrip('/')}/internal/v1/{path}",
                    headers={"Authorization": f"Bearer {self.settings.datasemantic_token}"},
                    timeout=30,
                    **kwargs,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code == 503
                    and attempt < _SERVICE_UNAVAILABLE_RETRIES
                ):
                    time.sleep(_SERVICE_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                if exc.response.status_code in {404, 409}:
                    raise SemanticContractError("QUERY_PUBLICATION_STALE", "DataSemantic 发布物不存在或版本不匹配。") from exc
                code = "QUERY_PLAN_UNSUPPORTED" if exc.response.status_code in {400, 422} else "QUERY_SERVICE_UNAVAILABLE"
                raise SemanticContractError(code, "DataSemantic 无法规划此查询。") from exc
            except (httpx.RequestError, ValueError) as exc:
                raise SemanticContractError("QUERY_SERVICE_UNAVAILABLE", "DataSemantic 服务暂时不可用。") from exc
        if not isinstance(data, dict) or any(
            data.get(key) != value for key, value in binding.items()
        ):
            raise SemanticContractError("QUERY_PUBLICATION_STALE", "DataSemantic 返回的发布版本不匹配。")
        return data

    def context(self):
        binding = self._binding()
        with self._metadata_lock:
            if binding != self._context_binding:
                self._context = self._call(
                    "GET", f"bundles/{quote(binding['bundle_id'], safe='')}/context", binding
                )
                self._context_binding = binding
            policy = self._permission_manifest()
            if policy is None:
                return self._context
            result = {k: v for k, v in self._context.items() if k in {"bundle_id", "manifest_hash", "dialect", "analysis_contract_schema_version"}}
            for kind in ("models", "views"):
                result[kind] = []
                for item in self._context.get(kind, []):
                    name = item["name"].lower()
                    if kind == "views" and name in PUBLIC_LINE_REACH_VIEWS:
                        result[kind].append(item)
                        continue
                    relation = policy.relations.get(name)
                    if relation is not None:
                        result[kind].append({"name": item["name"], "description": item.get("description", ""),
                                             "columns": [{k: v for k, v in col.items() if k in {"name", "type", "description"}}
                                                         for col in item.get("columns", [])
                                                         if col["name"] in relation.columns and col["name"] not in relation.forbidden_columns]})
            result["relationships"] = [r for r in self._context.get("relationships", [])
                                       if set(r.get("models", [])) <= set(policy.relations)]
            return result

    def _plan(self, semantic_sql: str, binding: dict, dialect: str) -> dict:
        plan = self._call("POST", "plan", binding, json={**binding, "semantic_sql": semantic_sql})
        if plan.get("dialect") != dialect:
            raise SemanticContractError("QUERY_PLAN_INVALID", "DataSemantic 计划的方言不匹配。")
        if not isinstance(plan.get("physical_sql"), str) or not plan["physical_sql"].strip():
            raise SemanticContractError("QUERY_PLAN_INVALID", "DataSemantic 未返回物理 SQL。")
        return plan

    def describe(self, name: str) -> dict:
        context = self.context()
        target = name.strip().lower()
        key = (context.get("bundle_id"), context.get("manifest_hash"), target)
        for kind in ("models", "views"):
            for item in context.get(kind, []):
                if item["name"].lower() != target:
                    continue
                with self._metadata_lock:
                    lock = self._description_locks.setdefault(key, Lock())
                with lock:
                    if key not in self._descriptions:
                        columns = item.get("columns", [])
                        if kind == "views" and not columns:
                            columns = self._describe_view_columns(item["name"], context)
                        relationships = [
                            relation for relation in context.get("relationships", [])
                            if item["name"] in relation.get("models", [])
                        ]
                        self._descriptions[key] = {**item, "columns": columns, "relationships": relationships}
                    return self._descriptions[key]
        raise SemanticQueryError("此名称未出现在当前发布物中，请从发布目录选择逻辑模型或视图。")

    def describe_many(self, names: list[str]) -> dict:
        if not names or len(names) > _MAX_DESCRIBE_NAMES or any(not name.strip() for name in names):
            raise SemanticQueryError(f"请提供 1 至 {_MAX_DESCRIBE_NAMES} 个有效的语义模型或视图名称。")
        descriptions = []
        errors = []
        for name in dict.fromkeys(name.strip().lower() for name in names):
            try:
                descriptions.append(self.describe(name))
            except SemanticQueryError as exc:
                errors.append({"name": name, "error": str(exc)})
        return {"descriptions": descriptions, "errors": errors}

    def _describe_view_columns(self, name: str, context: dict) -> list[dict[str, str]]:
        # Plan a zero-row projection to get fields without executing it.
        dialect = context["dialect"]
        binding = {key: context[key] for key in ("bundle_id", "manifest_hash")}
        view = exp.Table(this=exp.to_identifier(name, quoted=True))
        probe = exp.select("*").from_(view).limit(0)
        plan = self._plan(probe.sql(dialect=dialect), binding, dialect)
        try:
            tree = parse_one(plan["physical_sql"], read=dialect)
            if not isinstance(tree, exp.Query):
                raise SemanticQueryError("DataSemantic 未返回可识别的视图字段。")
            fields = qualify(
                tree, dialect=dialect, validate_qualify_columns=False, identify=False
            ).named_selects
        except SqlglotError as exc:
            raise SemanticQueryError("无法解析 DataSemantic 视图字段，请查看相关逻辑模型。") from exc
        if not fields or any(not field or field == "*" for field in fields):
            raise SemanticQueryError("DataSemantic 未返回完整视图字段，请查看相关逻辑模型。")
        return [{"name": field} for field in fields]

    def run(self, sql: str, *, execute: bool = True):
        try:
            return self._run(sql, execute=execute)
        except AuthorizationError as exc:
            raise SemanticAuthorizationError(exc) from exc
        except SemanticQueryError as exc:
            if not hasattr(exc, "detail") and self._permission_manifest() is not None:
                raise SemanticContractError("QUERY_SQL_UNSUPPORTED", str(exc)) from exc
            raise

    def prediction_line_context(self, candidate: dict) -> dict | None:
        """Read official thresholds for one exam proven visible by the v2 lookup."""
        if (
            candidate.get("permission_mode") != "model_explicit_v2"
            or candidate.get("rows_truncated")
            or candidate.get("tables") != ["tb_score_overview"]
        ):
            return None
        db_type, config = parse_edu_database_url(self.settings.database_edu_url)
        dialect = _DIALECTS[db_type]
        select = _query(candidate["sql"], dialect)
        # Only accept batch IDs and names that come directly from the verified facts.
        if not isinstance(select, exp.Select) or len(list(select.find_all(exp.Select))) != 1:
            return None
        aliases = {item.unalias().name.lower(): item.alias_or_name for item in select.expressions
                   if isinstance(item.unalias(), exp.Column)}
        columns = candidate["columns"]
        id_column = aliases.get("exam_batch_id")
        name_column = aliases.get("exam_name")
        if id_column not in columns or name_column not in columns:
            return None
        id_index, name_index = columns.index(id_column), columns.index(name_column)
        exams = {(row[id_index], row[name_index]) for row in candidate["rows"]}
        if len(exams) != 1:
            return None
        batch, name = exams.pop()
        if type(batch) is not int or batch <= 0 or not isinstance(name, str) or not name.strip():
            return None

        try:
            authorization = self._fresh_authorization()
            if authorization is None or authorization.fingerprint != candidate["authorization_fingerprint"]:
                raise AuthorizationError("AUTH_CONTEXT_STALE", "考试查询后授权已变化，请重新提问")
            sql = exp.select("*").from_(exp.Table(
                this=exp.to_identifier("tb_fraction_bar", quoted=True),
                db=exp.to_identifier(authorization.manifest.physical_schema, quoted=True),
            )).where(
                exp.column("exam_batch_id").eq(batch),
                exp.column("exam_name").eq(exp.Literal.string(name)),
            ).sql(dialect=dialect)
            ok, _, result = execute_sql(db_type, {
                **config, "read_only": True, "query_timeout_seconds": self.settings.chat_query_timeout_seconds,
            }, sql)
            current = self._fresh_authorization()
            if current is None or current.fingerprint != authorization.fingerprint:
                raise AuthorizationError("AUTH_REVOKED", "读取预测线时授权已变化，结果已丢弃")
        except AuthorizationError as exc:
            raise SemanticAuthorizationError(exc) from exc

        lines = normalize_fraction_bars([
            dict(zip(result["columns"], row)) for row in result["rows"]
        ]) if ok else []
        return {
            "exam_batch_id": batch, "exam_name": name,
            "status": ("configured" if lines else "missing") if ok else "unavailable",
            "lines": lines,
        }

    def _run(self, sql: str, *, execute: bool = True):
        context = self.context()
        binding = {key: context[key] for key in ("bundle_id", "manifest_hash")}
        url = self.settings.database_edu_url.strip()
        if not url:
            raise SemanticQueryError("未配置 DATABASE_EDU_URL。")
        try:
            db_type, config = parse_edu_database_url(url)
        except ValueError as exc:
            raise SemanticQueryError("DATABASE_EDU_URL 配置无效，请检查数据库连接串。") from exc
        dialect = _DIALECTS.get(db_type)
        if not dialect or context.get("dialect") != dialect:
            raise SemanticQueryError("DataSemantic 发布物与数据源方言不匹配。")
        views = {view["name"].lower(): view["name"] for view in context.get("views", [])}
        models = {model["name"].lower(): model for model in context.get("models", [])}
        logical_names = views.keys() | models.keys()
        release = (context.get("bundle_id"), context.get("manifest_hash"))
        error_key = (*release, dialect, sql) if isinstance(sql, str) else None
        with self._metadata_lock:
            cached_error = self._field_errors.get(error_key)
        if cached_error:
            raise SemanticFieldError(*cached_error)
        try:
            logical = _query(sql, dialect)
            sources = _check_sources(logical, logical_names)
            used_names = {table.name.lower() for _, table in sources}
            used_views = views.keys() & used_names
            described_views = {
                name: self._descriptions[(*release, name)]["columns"]
                for name in used_views if (*release, name) in self._descriptions
            }
            unread = used_views - described_views.keys()
            if unread:
                names = ", ".join(views[key] for key in sorted(unread))
                raise SemanticViewDescriptionRequiredError(
                    f"请先用 describe_semantic_model 读取视图 {names} 的输出字段，再提交语义 SQL。",
                    [views[key] for key in sorted(unread)],
                )
            published_columns = {
                name: ("视图", views[name], columns) for name, columns in described_views.items()
            }
            for name in used_names & models.keys():
                model = models[name]
                if columns := model.get("columns"):
                    published_columns[name] = ("逻辑模型", model["name"], columns)
            if published_columns:
                _check_published_columns(logical, dialect, {
                    name: (kind, relation, {field["name"]: field for field in columns})
                    for name, (kind, relation, columns) in published_columns.items()
                })
            permission_context = self._fresh_authorization() if getattr(self.settings, "database_edu_url", "") else None
            public_views = used_views & PUBLIC_LINE_REACH_VIEWS.keys()
            if used_names <= public_views:
                assert_public_line_reach_allowed(public_views, permission_context)
                if dialect == "postgres":
                    logical = _order_public_postgres_set(logical)
                plan = self._plan(logical.sql(dialect=dialect), binding, dialect)
                return self._execute_public_line_reach(
                    plan, binding, db_type, dialect, config, execute, logical, used_names, permission_context,
                )
            if permission_context is not None and not permission_context.grants:
                raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "当前账号可查询公开达线汇总；此查询需要有效的教育授权")
            proof = PermissionValidator(permission_context).prove(logical) if permission_context else None
            permission_context = self._fresh_authorization()
            proof = PermissionValidator(permission_context).prove(logical)
            plan = self._plan(logical.sql(dialect=dialect), binding, dialect)
            return self._execute_explicit(
                plan,
                binding,
                db_type,
                dialect,
                config,
                execute,
                logical,
                permission_context,
                proof,
            )
        except SemanticFieldError as exc:
            policy = self._permission_manifest()
            relation = policy.relations.get(exc.detail.get("source", {}).get("relation", "").lower()) if policy else None
            formula = relation.metrics.get(exc.detail.get("field")) if relation else None
            if formula:
                exc.detail.update(
                    metric_definition={"id": exc.detail["field"], "expression": formula},
                    repair_hint="该名称是审核指标标识，不是表字段；请展开其表达式，基准输出仍需遵守查询契约。",
                )
            with self._metadata_lock:
                if len(self._field_errors) >= _MAX_FIELD_ERRORS:
                    self._field_errors.pop(next(iter(self._field_errors)))
                self._field_errors[error_key] = (str(exc), exc.detail)
            raise
        except SqlglotError as exc:
            raise SemanticQueryError("无法解析语义 SQL 或生成的物理计划。") from exc

    def _execute_public_line_reach(self, plan, binding, db_type, dialect, config, execute, logical, views, authorization):
        with get_db_session() as session:
            user = get_user_by_id(session, self.user_id)
            if user is None or int(user.status) != 1:
                raise AuthorizationError("AUTH_REVOKED", "账号不可用")
            assert_datasource_accessible(session, user, self.datasource_id, self.workspace_oid)

        schema = authorization.manifest.physical_schema if authorization else (
            "public" if dialect == "postgres" else config["database"]
        )
        if authorization and any(
            config.get(key) != value for key, value in authorization.manifest.connection.model_dump().items()
        ):
            raise AuthorizationError("AUTH_MAPPING_MISSING", "实际连接不属于审核的数据源，查询已停止")
        tree = _query(plan["physical_sql"], dialect, compiled=True)
        _apply_public_line_reach_scope(tree, views, authorization)
        physical_tables = set().union(*(PUBLIC_LINE_REACH_VIEWS[name] for name in views))
        sources = _check_sources(tree, schema=schema)
        if any(table.name.lower() not in physical_tables for _, table in sources):
            raise SemanticContractError("QUERY_PLAN_INVALID", "达线视图引用了未发布的数据表")
        for _, table in sources:
            table.set("db", exp.to_identifier(schema, quoted=True))

        result = {}
        if execute:
            limit = tree.args.get("limit")
            if limit is None or not isinstance(limit.expression, exp.Literal) or not limit.expression.is_int or int(limit.expression.this) > _MAX_ROWS:
                tree = tree.limit(_MAX_ROWS + 1)
            ok, _, data = execute_sql(db_type, {
                **config, "read_only": True, "query_timeout_seconds": self.settings.chat_query_timeout_seconds,
            }, tree.sql(dialect=dialect))
            if not ok or not isinstance(data, dict):
                raise SemanticContractError("QUERY_EXECUTION_FAILED", "达线汇总查询失败，请检查数据源")
            rows = data["rows"]
            result = {"columns": data["columns"], "rows": rows[:_MAX_ROWS],
                      "row_count": min(len(rows), _MAX_ROWS), "rows_truncated": len(rows) > _MAX_ROWS}
        if authorization:
            current = self._fresh_authorization()
            if current is None or current.fingerprint != authorization.fingerprint:
                raise AuthorizationError("AUTH_CONTEXT_STALE", "查询期间账号授权发生变化，请重新查询")
            result.update(permission_mode="model_explicit_v2", authorization_fingerprint=authorization.fingerprint)
        return jsonable_encoder({**result, **binding, "sql": logical.sql(dialect=dialect),
                                 "tables": sorted(views), "plan_request_id": plan.get("request_id")})

    def _execute_explicit(self, plan, binding, db_type, dialect, config, execute, logical, authorization, proof):
        if dialect != authorization.manifest.dialect:
            raise AuthorizationError("AUTH_MAPPING_MISSING", "权限映射的数据库方言不匹配")
        if any(config.get(key) != value for key, value in authorization.manifest.connection.model_dump().items()):
            raise AuthorizationError("AUTH_MAPPING_MISSING", "实际连接不属于审核的数据源，查询已停止")
        try:
            secured = _query(plan["physical_sql"], dialect, compiled=True)
        except (SemanticQueryError, SqlglotError) as exc:
            raise AuthorizationError("AUTH_PHYSICAL_PROOF_FAILED", "编译结果不是可验证的只读查询") from exc
        verify_compiled_permissions(secured, authorization, proof)
        for _, table in _sources(secured):
            table.set("db", exp.to_identifier(authorization.manifest.physical_schema, quoted=True))
        if execute:
            limit = secured.args.get("limit")
            if limit is None or not isinstance(limit.expression, exp.Literal) or not limit.expression.is_int or int(limit.expression.this) > _MAX_ROWS:
                secured = secured.limit(_MAX_ROWS + 1)
        verify_compiled_permissions(secured, authorization, proof)
        current = self._fresh_authorization()
        if current is None or current.fingerprint != authorization.fingerprint:
            raise AuthorizationError("AUTH_CONTEXT_STALE", "执行前权限已变化，请重新生成查询")
        result = {}
        if execute:
            outcome = run_sql_with_auto_fix(secured.sql(dialect=dialect), db_type=db_type, config={
                **config, "read_only": True, "query_timeout_seconds": self.settings.chat_query_timeout_seconds,
            }, max_fix_attempts=0)
            current = self._fresh_authorization()
            if current is None or current.fingerprint != authorization.fingerprint:
                raise AuthorizationError("AUTH_REVOKED", "查询期间权限发生变化，结果已丢弃")
            if not outcome.success or not isinstance(outcome.result, dict):
                raise SemanticContractError("QUERY_EXECUTION_FAILED", "已验证查询执行失败，请检查数据源")
            result = apply_result_privacy(outcome.result) or outcome.result
            if tuple(result.get("columns", ())) != proof.output_columns:
                raise AuthorizationError("AUTH_PHYSICAL_PROOF_FAILED", "数据库结果字段与已验证输出契约不一致")
            rows = result.get("rows", [])
            result = {"columns": result["columns"], "rows": rows[:_MAX_ROWS],
                      "row_count": min(len(rows), _MAX_ROWS), "rows_truncated": len(rows) > _MAX_ROWS}
        # The model and ordinary SSE/history never receive the compiled SQL or table names.
        return jsonable_encoder({**result, "sql": logical.sql(dialect=dialect),
                                 "tables": sorted({t.name for _, t in _sources(logical)}), **binding,
                                 "plan_request_id": plan.get("request_id"), "permission_mode": "model_explicit_v2",
                                 "authorization_fingerprint": authorization.fingerprint,
                                 "query_contract": {"version": CONTRACT_VERSION,
                                                    "prover_version": PROVER_VERSION,
                                                    "fingerprint": proof.contract_fingerprint}})
