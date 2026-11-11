"""Conservative, deterministic permission proof for model-authored SELECTs.

No predicates are added to the executed query. Normalization works on a copy to
recognize compiler wrappers; unsupported relational shapes fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from sqlglot import exp, parse_one
from sqlglot.optimizer.merge_subqueries import merge_subqueries
from sqlglot.optimizer.pushdown_predicates import pushdown_predicates
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from src.datasource.service.education_authorization import (
    AuthorizationContext,
    AuthorizationError,
    fingerprint,
)
from src.datasource.service.semantic_permission_contract import SQL_GRAMMAR, build_query_contract

_SHAPE_REASON = {
    "参照池不能通过UNION输出": "pool_union_not_allowed",
    "最终结果包含未经授权释放的参照池数据": "pool_release_incomplete",
    "参照池在授权释放前不能参与关联": "pool_join_before_release",
    "参照池不能先排序、截断或去重": "pool_order_limit_distinct_not_allowed",
    "不能在已确定的参照池上再添加人群过滤": "reference_filter_not_allowed",
    "必须先按审核单位聚合参照池": "reference_aggregate_required",
    "排名前不能再次筛选单位": "pool_filter_before_rank",
    "参照池列必须可追溯到审核字段": "pool_column_unresolved",
    "参照池字段无法追溯": "pool_column_unresolved",
    "无法识别参照池表达式": "pool_expression_unresolved",
    "参照池投影不能增加筛选": "pool_projection_filter_not_allowed",
    "请将参照池聚合和排名分为独立CTE": "window_requires_separate_cte",
    "指标或有效样本字段没有审核定义": "metric_definition_missing",
    "参照池HAVING仅允许审核后的样本和单位数门槛": "baseline_having_invalid",
    "缺少参照池有效样本数门槛": "baseline_having_required",
    "不能输出参照池未分组字段": "baseline_projection_not_grouped",
    "整体基准必须按策略精度ROUND后释放": "baseline_round_required",
    "参照池只允许审核后的指标表达式": "baseline_projection_not_allowed",
    "参照池必须计算审核指标": "baseline_metric_required",
    "排名窗口不能改变分区或人群": "rank_window_invalid",
    "排名必须按审核指标降序": "rank_order_invalid",
    "未开放此参照池窗口函数": "rank_window_unsupported",
    "排名必须保留单位键、名次和参评数": "rank_output_incomplete",
    "自身排名输出不能再次聚合或开窗口": "rank_output_aggregate_not_allowed",
    "排名输出必须明确限定自身单位": "rank_target_required",
    "缺少排名后的自身区县过滤": "rank_target_required",
    "缺少最少参评区县数门槛": "rank_units_gate_required",
    "排名输出含未批准筛选条件": "rank_filter_not_allowed",
    "自身排名指标精度无效": "rank_round_required",
    "ROUND只能处理审核指标": "rank_round_target_invalid",
    "排名结果指标必须按策略精度输出": "rank_round_required",
}


def fail(code, message, **details):
    # Every release-shape rejection carries a stable machine-readable reason so
    # the model can repair it and operators can aggregate failure causes. Sites
    # that can locate the offending expression use fail_shape() below.
    if code == "AUTH_RELEASE_SHAPE_INVALID":
        details.setdefault("reason", _SHAPE_REASON.get(message, "release_shape_invalid"))
    raise AuthorizationError(code, message, **details)


def fail_shape(
    message,
    *,
    reason,
    location=None,
    rejected=None,
    expected=None,
    hint=None,
    example=None,
    **details,
):
    fail(
        "AUTH_RELEASE_SHAPE_INVALID",
        message,
        reason=reason,
        location=location or {},
        rejected_expression=rejected,
        expected_expression=expected,
        repair_hint=hint,
        example=example,
        **details,
    )


def cte_name(select, fallback=""):
    """Best-effort name of the CTE/subquery that owns this SELECT."""
    parent = getattr(select, "parent", None)
    return getattr(parent, "alias_or_name", "") or fallback


def plain(node):
    return node.this if isinstance(node, (exp.Alias, exp.Paren)) else node


def canonical(node):
    node = node.copy()
    for col in node.find_all(exp.Column):
        col.set("table", None)
        col.set("db", None)
        col.set("catalog", None)
    for ident in node.find_all(exp.Identifier):
        ident.set("quoted", False)
        ident.set("this", ident.name.lower())
    return node.sql(normalize=True)


def literal(node):
    return str(node.this) if isinstance(node, exp.Literal) else None


def numeric_wrapper(node):
    # SQLGlot's PostgreSQL generator inserts this widening cast for ROUND(double, n).
    if (
        isinstance(node, exp.Cast)
        and node.args["to"].this == exp.DataType.Type.DECIMAL
        and not node.args["to"].expressions
    ):
        return node.this
    return node


def terms(node):
    """Bounded DNF. Every satisfiable branch must imply one complete grant."""
    if node is None:
        return [[]]
    if isinstance(node, exp.Paren):
        return terms(node.this)
    if isinstance(node, exp.Or):
        result = terms(node.left) + terms(node.right)
    elif isinstance(node, exp.And):
        result = [a + b for a, b in product(terms(node.left), terms(node.right))]
    else:
        result = [[node]]
    if len(result) > SQL_GRAMMAR.max_predicate_branches:
        fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "权限条件分支过多，请简化查询")
    return result


def column_values(atoms, alias):
    values = {}
    for atom in atoms:
        key, vals = None, None
        if isinstance(atom, exp.EQ):
            col, val = atom.left, atom.right
            if isinstance(val, exp.Column):
                col, val = val, col
            if isinstance(col, exp.Column) and col.table == alias and literal(val) is not None:
                key, vals = col.name, {literal(val)}
        elif isinstance(atom, exp.In) and isinstance(atom.this, exp.Column):
            if atom.this.table == alias and not atom.args.get("query") and atom.expressions:
                lits = [literal(x) for x in atom.expressions]
                if all(v is not None for v in lits):
                    key, vals = atom.this.name, set(lits)
        if key is not None:
            values[key] = values[key] & vals if key in values else vals
    return values


def history_proven(grant, policy, atoms, alias):
    for bound, cls in ((grant.history_from, exp.GTE), (grant.history_to, exp.LTE)):
        if bound is None:
            continue
        if not policy.date_column:
            return False
        found = False
        for atom in atoms:
            if isinstance(atom, cls) and isinstance(atom.left, exp.Column):
                col, value = atom.left, literal(atom.right)
                if col.table == alias and col.name == policy.date_column and value:
                    # ISO date literals only; no lexical comparison on arbitrary strings.
                    from datetime import date

                    try:
                        parsed = date.fromisoformat(value)
                    except ValueError:
                        continue
                    found |= parsed >= bound if cls is exp.GTE else parsed <= bound
        if not found:
            return False
    return True


@dataclass(frozen=True)
class Value:
    kind: str
    name: str


@dataclass
class Flow:
    kind: str
    columns: dict[str, Value]
    relation: str = ""
    capability: str = ""
    grant_id: str = ""
    target: str = ""


@dataclass(frozen=True)
class QueryProof:
    authorization_fingerprint: str
    sql_hash: str
    scans: tuple[str, ...]
    releases: tuple[str, ...]
    output_columns: tuple[str, ...]
    contract_fingerprint: str


def inline_shared_projections(tree):
    """Expand shared transparent CTEs on the proof copy, preserving all predicates.

    Wren factors repeated reads into a common unfiltered projection. Each consumer
    still supplies its own scope. SQLGlot deliberately keeps multiply-used CTEs,
    so expose those consumers before proving scans. Aggregates, windows, joins,
    limits and other population-changing operations are never expanded here.
    """
    for _ in range(SQL_GRAMMAR.max_normalization_passes):
        references = {}
        for scope in traverse_scope(tree):
            for table, source in scope.selected_sources.values():
                if isinstance(source, Scope) and source.is_cte:
                    references.setdefault(id(source), (source, []))[1].append(table)
        candidate = None
        for source, tables in references.values():
            select = source.expression
            if (
                len(tables) > 1
                and isinstance(select, exp.Select)
                and not source.is_correlated_subquery
                and not select.parent.alias_column_names
                and all(isinstance(plain(e), exp.Column) for e in select.expressions)
                and not any(
                    value
                    for key, value in select.args.items()
                    if key not in {"expressions", "from_", "where"}
                )
            ):
                candidate = source, tables
                break
        if candidate is None:
            return tree
        source, tables = candidate
        if (
            sum(1 for _ in tree.walk()) + len(tables) * sum(1 for _ in source.expression.walk())
            > SQL_GRAMMAR.max_expanded_nodes
        ):
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "共享查询展开过多，请简化查询")
        cte = source.expression.parent
        for table in tables:
            alias = table.args.get("alias")
            table.replace(
                exp.Subquery(
                    this=source.expression.copy(),
                    alias=alias.copy()
                    if alias
                    else exp.TableAlias(this=exp.to_identifier(table.alias_or_name)),
                )
            )
        with_clause = cte.parent
        cte.pop()
        if not with_clause.expressions:
            with_clause.pop()
    fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "共享查询层级过多，请简化查询")


def normalized_tree(tree, context, *, physical=False):
    """Qualify, merge transparent compiler wrappers and push existing predicates.

    These transformations are for validation only. The caller executes its original
    compiled AST, never this copy; no absent permission is manufactured here.
    """
    tree = tree.copy()
    policies = context.manifest.relations
    if physical:
        physical_names = {p.physical_table.lower(): (name, p) for name, p in policies.items()}
        for scope in traverse_scope(tree):
            for alias, (_, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table):
                    continue
                name = source.name.lower()
                if (
                    source.catalog
                    or (source.db and source.db != context.manifest.physical_schema)
                    or name not in physical_names
                ):
                    fail("AUTH_PHYSICAL_PROOF_FAILED", "物理计划引用了未审核来源")
                logical, policy = physical_names[name]
                # Recognize reviewed physical expressions, longest first (e.g. CASE valid_score).
                patterns = [
                    (col, parse_one(expression, read=context.manifest.dialect))
                    for col, expression in policy.columns.items()
                ]
                patterns.sort(key=lambda x: len(x[1].sql()), reverse=True)
                for col_name, pattern in patterns:
                    for node in list(scope.expression.walk()):
                        if node.find_ancestor(exp.Select) is not scope.expression:
                            continue
                        cols = list(node.find_all(exp.Column))
                        if not cols or any(c.table and c.table != alias for c in cols):
                            continue
                        if canonical(node) == canonical(pattern):
                            node.replace(exp.column(col_name, table=alias))
                if not source.alias:
                    source.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
                source.set("this", exp.to_identifier(logical))
                source.set("db", None)
    schema = {name: {col: "UNKNOWN" for col in policy.columns} for name, policy in policies.items()}
    try:
        tree = qualify(
            tree,
            dialect=context.manifest.dialect,
            schema=schema,
            infer_schema=False,
            identify=False,
            quote_identifiers=False,
            validate_qualify_columns=True,
        )
        tree = inline_shared_projections(tree)
        for _ in range(2):
            tree = merge_subqueries(pushdown_predicates(tree, dialect=context.manifest.dialect))
        return qualify(
            tree,
            dialect=context.manifest.dialect,
            schema=schema,
            infer_schema=False,
            identify=False,
            quote_identifiers=False,
            validate_qualify_columns=True,
        )
    except AuthorizationError:
        raise
    except Exception as exc:
        raise AuthorizationError(
            "AUTH_QUERY_SHAPE_UNSUPPORTED", "无法绑定查询字段或证明编译后的作用域", retryable=True
        ) from exc


class PermissionValidator:
    def __init__(self, context: AuthorizationContext):
        self.context = context
        self.manifest = context.manifest
        self.contract = build_query_contract(self.manifest)
        self.scans = []
        self.releases = []

    def prove(self, tree, *, physical=False):
        try:
            return self._prove(tree, physical=physical)
        except AuthorizationError as exc:
            if exc.detail.get("retryable") and not physical:
                # The active context was established before this proof. A rejected
                # query shape must not be mistaken for missing account authorization.
                update = {
                    "granted_capabilities": sorted(
                        {c for g in self.context.grants for c in g.capabilities}
                    ),
                }
                if exc.detail.get("repair_hint"):
                    # A located repair hint is more useful than another full contract dump.
                    relation = exc.detail.get("relation")
                    if relation in self.contract.relations:
                        update["repair_contract"] = {
                            "relation": relation,
                            "precision": self.contract.reference_policy.precision,
                            **self.contract.relations[relation].model_dump(mode="json"),
                        }
                    else:
                        update["reference_query_contracts"] = {
                            name: self.contract.relations[name].model_dump(mode="json")
                            for name, policy in self.manifest.relations.items()
                            if not policy.public
                        }
                else:
                    update["reference_query_contracts"] = {
                        name: self.contract.relations[name].model_dump(mode="json")
                        for name, policy in self.manifest.relations.items()
                        if not policy.public
                    }
                exc.detail.update(update)
            raise

    def _prove(self, tree, *, physical=False):
        tree = normalized_tree(tree, self.context, physical=physical)
        scopes = list(traverse_scope(tree))
        if (
            not scopes
            or len(scopes) > self.contract.grammar.max_scopes
            or any(s.is_correlated_subquery for s in scopes)
        ):
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "不支持相关子查询或过多作用域")
        if any(isinstance(x, (exp.Exists, exp.Intersect, exp.Except)) for x in tree.walk()):
            fail(
                "AUTH_QUERY_SHAPE_UNSUPPORTED",
                "请使用受支持的独立CTE和关联，不能通过存在性或集合差探测数据",
            )
        if any(s.scope_type.name == "SUBQUERY" for s in scopes):
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "暂不支持标量子查询，请改写为独立CTE")
        if any(cte.args.get("recursive") for cte in tree.find_all(exp.With)):
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "不支持递归查询")
        flows = {}
        for scope in scopes:
            if isinstance(scope.expression, exp.Union):
                if scope.expression.args.get("distinct") is not False:
                    fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "集合查询仅支持UNION ALL")
                children = [flows[id(s)] for s in scope.union_scopes]
                if any(f.kind not in {"own", "baseline", "self_rank"} for f in children):
                    fail("AUTH_RELEASE_SHAPE_INVALID", "参照池不能通过UNION输出")
                flows[id(scope)] = Flow("own", children[0].columns)
                continue
            flows[id(scope)] = self.select(scope, flows)
        result = flows[id(scopes[-1])]
        if result.kind not in {"own", "baseline", "self_rank"}:
            fail("AUTH_RELEASE_SHAPE_INVALID", "最终结果包含未经授权释放的参照池数据")
        return QueryProof(
            self.context.fingerprint,
            fingerprint(tree.sql()),
            tuple(sorted(set(self.scans))),
            tuple(sorted(set(self.releases))),
            tuple(result.columns),
            self.contract.fingerprint,
        )

    def source(self, scope, alias, table):
        name = table.name.lower()
        if table.db or table.catalog or name not in self.manifest.relations:
            fail("AUTH_MAPPING_MISSING", "查询来源没有审核后的权限映射")
        policy = self.manifest.relations[name]
        rules = self.contract.relations[name]
        columns = {name: Value("raw", name) for name in policy.columns}
        if policy.public:
            return Flow("own", columns, name)
        where = scope.expression.args.get("where")
        branches = terms(where.this if where else None)
        for branch in branches:
            vals = column_values(branch, alias)
            if not all(
                vals.get(key) == {str(value)} for key, value in policy.population_filters.items()
            ):
                fail("AUTH_SCOPE_MISSING", "缺少审核后的固定人群条件", source_alias=alias)
            for rule in self.context.required_row_rules.get(name, ()):
                required_branches = terms(parse_one(rule, read=self.manifest.dialect))
                actual = {
                    canonical(atom)
                    for atom in branch
                    if all(c.table == alias for c in atom.find_all(exp.Column))
                }
                if not any(
                    {canonical(atom) for atom in required} <= actual
                    for required in required_branches
                ):
                    fail("AUTH_SCOPE_MISSING", "缺少仍有效的强制行权限条件", source_alias=alias)
        ordinary = True
        for branch in branches:
            vals = column_values(branch, alias)
            matching = []
            for grant in self.context.grants:
                if all(
                    (policy.implicit_scope.get(key) == value)
                    or (
                        policy.scope_columns.get(key)
                        and vals.get(policy.scope_columns[key])
                        and vals[policy.scope_columns[key]] <= {value}
                    )
                    for key, value in grant.keys().items()
                ):
                    if history_proven(grant, policy, branch, alias):
                        matching.append(grant)
            ordinary &= bool(matching)
        if ordinary:
            self.record_scan(name, alias, branches, "own")
            return Flow("own", columns, name)
        # A reference pool must use one fully constrained population, not arbitrary ORs.
        rejected_filters = []
        if len(branches) == 1:
            vals = column_values(branches[0], alias)
            for grant in self.context.grants:
                for capability in grant.capabilities:
                    rule = rules.capabilities.get(capability)
                    if rule is None:
                        continue
                    keys = {
                        key: value
                        for key in rule.pool_scope_keys
                        if (value := grant.keys().get(key)) is not None
                    }
                    if grant.cohort_id:
                        keys["cohort_id"] = grant.cohort_id
                    elif policy.scope_columns.get("cohort_id"):
                        cohort_values = vals.get(policy.scope_columns["cohort_id"], set())
                        known = {
                            row.get("cohort_id")
                            for row in self.manifest.catalog
                            if row.get("city_id") == grant.city_id
                        }
                        if len(cohort_values) != 1 or not cohort_values <= known:
                            continue
                        keys["cohort_id"] = next(iter(cohort_values))
                    if not all(
                        policy.implicit_scope.get(k) == v
                        or (
                            policy.scope_columns.get(k) and vals.get(policy.scope_columns[k]) == {v}
                        )
                        for k, v in keys.items()
                    ):
                        continue
                    if not vals.get(policy.exam_column) or len(vals[policy.exam_column]) != 1:
                        continue
                    if not history_proven(grant, policy, branches[0], alias):
                        continue
                    allowed = {
                        policy.scope_columns[k] for k in keys if k in policy.scope_columns
                    } | {policy.exam_column}
                    allowed |= set(policy.population_filters)
                    # Hard restrictions still apply to reference populations.
                    required_atoms = {
                        canonical(atom)
                        for rule in self.context.required_row_rules.get(name, ())
                        for branch in terms(parse_one(rule))
                        for atom in branch
                    }
                    if policy.subject_column:
                        allowed.add(policy.subject_column)
                    if policy.date_column:
                        allowed.add(policy.date_column)
                    for atom in branches[0]:
                        if canonical(atom) in required_atoms:
                            continue
                        cols = list(atom.find_all(exp.Column))
                        if not cols or any(c.table != alias or c.name not in allowed for c in cols):
                            rejected_filters.append(canonical(atom))
                            break
                        if not isinstance(atom, (exp.EQ, exp.In, exp.GTE, exp.LTE)):
                            rejected_filters.append(canonical(atom))
                            break
                        if isinstance(atom, (exp.GTE, exp.LTE)) and any(
                            c.name != policy.date_column for c in cols
                        ):
                            rejected_filters.append(canonical(atom))
                            break
                        if any(literal(x) is None for x in atom.expressions) or (
                            isinstance(atom, (exp.EQ, exp.GTE, exp.LTE))
                            and literal(atom.right) is None
                        ):
                            rejected_filters.append(canonical(atom))
                            break
                    else:
                        if (
                            rule.fixed_subject
                            and policy.subject_column
                            and len(vals.get(policy.subject_column, ())) != 1
                        ):
                            continue
                        self.record_scan(name, alias, branches, capability)
                        return Flow(
                            "reference_raw",
                            columns,
                            name,
                            capability,
                            grant.id,
                            grant.district_id or "",
                        )
        if rejected_filters:
            fail(
                "AUTH_SCOPE_MISSING",
                "参照池包含授权契约以外的人群筛选；AVG自动忽略NULL，请移除额外的成绩非空等条件",
                source_alias=alias,
                reason="reference_population_filter_not_allowed",
                disallowed_filters=sorted(set(rejected_filters)),
            )
        fail(
            "AUTH_SCOPE_MISSING",
            "读取范围不属于完整授权元组，或缺少授权范围/历史条件",
            source_alias=alias,
        )

    def record_scan(self, name, alias, branches, kind):
        # Compare logical/compiled scan populations as well as permission boundaries.
        atoms = []
        for branch in branches:
            atoms.append(
                sorted(
                    canonical(a)
                    for a in branch
                    if all(c.table == alias for c in a.find_all(exp.Column))
                )
            )
        self.scans.append(fingerprint({"relation": name, "kind": kind, "branches": sorted(atoms)}))

    def select(self, scope, flows):
        select = scope.expression
        if not isinstance(select, exp.Select):
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "不支持此查询结构")
        with_clause = select.args.get("with_")
        if (
            select.args.get("qualify")
            or select.args.get("pivots")
            or (with_clause is not None and with_clause.args.get("recursive"))
        ):
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "不支持此查询结构")
        sources = {}
        for alias, (_, source) in scope.selected_sources.items():
            sources[alias] = (
                flows[id(source)]
                if isinstance(source, Scope)
                else self.source(scope, alias, source)
            )
        if not sources:
            fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "查询必须读取已审核语义来源")
        for join in select.args.get("joins") or []:
            if (
                join.args.get("side")
                or join.args.get("kind", "").upper() not in self.contract.grammar.joins
            ):
                fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "暂只支持INNER/CROSS JOIN")
        # Scalar subqueries and expressions with nested SELECT are never accepted.
        for clause in [
            *select.expressions,
            select.args.get("where"),
            select.args.get("having"),
            select.args.get("order"),
        ]:
            if clause is not None and any(
                isinstance(x, (exp.Subquery, exp.Select)) for x in clause.walk()
            ):
                fail("AUTH_QUERY_SHAPE_UNSUPPORTED", "不支持表达式中的子查询")
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table):
                policy = self.manifest.relations[source.name.lower()]
                for col in scope.columns:
                    if col.table != alias:
                        continue
                    if col.name in policy.forbidden_columns:
                        fail("AUTH_COLUMN_FORBIDDEN", "查询引用了禁止使用的字段")
                    if col.name in policy.filter_only_columns:
                        where = select.args.get("where")
                        if where is None or col not in list(where.find_all(exp.Column)):
                            fail("AUTH_COLUMN_FORBIDDEN", "该字段仅可用于授权范围过滤")
                        # No arbitrary predicates, derived tests or field-to-field probes.
                        if not isinstance(col.parent, (exp.EQ, exp.In)) or not any(
                            col.name in column_values(branch, alias) for branch in terms(where.this)
                        ):
                            fail("AUTH_COLUMN_FORBIDDEN", "仅过滤字段必须使用明确授权值")
                        scope_key = next(
                            (k for k, v in policy.scope_columns.items() if v == col.name), None
                        )
                        permitted = {g.keys().get(scope_key) for g in self.context.grants} - {None}
                        if not permitted or not all(
                            column_values(branch, alias).get(col.name, set()) <= permitted
                            for branch in terms(where.this)
                        ):
                            fail("AUTH_COLUMN_FORBIDDEN", "仅过滤字段不可用于探查其他单位或个人")
        internal = [
            (alias, flow)
            for alias, flow in sources.items()
            if flow.kind.startswith("reference") or flow.kind == "rank_pool"
        ]
        if internal:
            if len(sources) != 1:
                fail_shape(
                    "参照池在授权释放前不能参与关联",
                    reason="pool_join_before_release",
                    location={"cte": cte_name(select), "sources": sorted(sources)},
                    expected="参照池先在独立 CTE 中聚合释放，再在最终 SELECT 中与本校结果关联",
                    hint="不要把参照原始池直接 JOIN 到明细或其他来源；先聚合、过门槛、按精度取整，再关联。",
                )
            alias, source = internal[0]
            releases_here = source.kind == "rank_pool" or (
                source.kind == "reference_raw"
                and source.capability != "district_self_rank"
                and (
                    select.args.get("group") or any(e.find(exp.AggFunc) for e in select.expressions)
                )
            )
            if select.args.get("distinct") or (
                not releases_here
                and (
                    select.args.get("limit")
                    or select.args.get("offset")
                    or select.args.get("order")
                )
            ):
                fail_shape(
                    "参照池不能先排序、截断或去重",
                    reason="pool_order_limit_distinct_not_allowed",
                    location={"cte": cte_name(select, source.relation)},
                    expected="参照池先完整聚合，再在最终输出中排序/截断；不要在池上使用 DISTINCT",
                    hint="池上的 ORDER BY/LIMIT/OFFSET/DISTINCT 会改变参照人群；移到最终结果层。",
                )
            if source.kind == "reference_raw":
                if select.args.get("where") and any(
                    isinstance(s, Scope) for _, s in scope.selected_sources.values()
                ):
                    fail_shape(
                        "不能在已确定的参照池上再添加人群过滤",
                        reason="reference_filter_not_allowed",
                        location={"cte": cte_name(select, source.relation)},
                        expected="只保留策略要求的固定人群条件；AVG 自动忽略 NULL",
                        hint="移除成绩非空、排除某班/某校等额外人群条件，它们会改变参照池口径。",
                    )
                if select.args.get("group") or any(e.find(exp.AggFunc) for e in select.expressions):
                    return self.aggregate(select, sources, source)
                if any(e.find(exp.Window) for e in select.expressions):
                    fail_shape(
                        "必须先按审核单位聚合参照池",
                        reason="reference_aggregate_required",
                        location={"cte": cte_name(select, source.relation)},
                        expected="先聚合出各单位指标并过门槛，再单独 CTE 开窗口",
                        hint="聚合和窗口不能写在同一个 SELECT；拆成两个 CTE。",
                    )
                return self.passthrough(select, sources, source)
            if source.kind == "reference_groups":
                if (
                    select.args.get("where")
                    or select.args.get("having")
                    or select.args.get("group")
                ):
                    fail_shape(
                        "排名前不能再次筛选单位",
                        reason="pool_filter_before_rank",
                        location={"cte": cte_name(select, source.relation)},
                        expected="先对全部授权单位排名，再在最终输出中限定自身单位",
                        hint="排名前筛选会改变名次；把自身范围过滤放到排名结果之后。",
                    )
                if any(e.find(exp.Window) for e in select.expressions):
                    return self.rank(select, sources, source)
                return self.passthrough(select, sources, source)
            return self.release_rank(select, sources, source)
        return Flow(
            "own", {e.alias_or_name: Value("own", e.alias_or_name) for e in select.expressions}
        )

    def value(self, node, sources):
        node = plain(node)
        if not isinstance(node, exp.Column) or node.table not in sources:
            fail_shape(
                "参照池列必须可追溯到审核字段",
                reason="pool_column_unresolved",
                location={"expression": node.sql(dialect=self.manifest.dialect)},
                expected="引用已审核的指标、分组维度或单位键列",
                hint="只使用 reference_query_contract 中的列和审核指标表达式。",
            )
        value = sources[node.table].columns.get(node.name)
        if value is None:
            fail_shape(
                "参照池字段无法追溯",
                reason="pool_column_unresolved",
                location={"expression": node.sql(dialect=self.manifest.dialect)},
                expected="引用参照池已释放的列",
                hint="外层只能引用参照池 CTE 实际输出的列。",
            )
        return value

    def expanded(self, node, sources):
        if isinstance(node, exp.Column):
            return canonical(exp.column(self.value(node, sources).name))
        node = node.copy()
        for col in list(node.find_all(exp.Column)):
            value = self.value(col, sources)
            if value.kind not in {"raw", "dimension", "metric", "target", "rank", "units"}:
                fail_shape(
                    "无法识别参照池表达式",
                    reason="pool_expression_unresolved",
                    location={"expression": node.sql(dialect=self.manifest.dialect)},
                    expected="使用审核指标表达式或其算术组合",
                    hint="参照池表达式只能由审核列与审核指标组成。",
                )
            col.replace(exp.column(value.name))
        return canonical(node)

    def passthrough(self, select, sources, source):
        if select.args.get("having"):
            fail_shape(
                "参照池投影不能增加筛选",
                reason="pool_projection_filter_not_allowed",
                location={"cte": cte_name(select, source.relation)},
                expected="参照池投影只输出已释放的列",
                hint="需要筛选时在聚合 CTE 内用审核后的 HAVING 门槛。",
            )
        columns = {e.alias_or_name: self.value(plain(e), sources) for e in select.expressions}
        return Flow(
            source.kind, columns, source.relation, source.capability, source.grant_id, source.target
        )

    def aggregate(self, select, sources, source):
        policy = self.manifest.relations[source.relation]
        rules = self.contract.relations[source.relation]
        capability = rules.capabilities[source.capability]
        if any(e.find(exp.Window) for e in select.expressions):
            fail_shape(
                "请将参照池聚合和排名分为独立CTE",
                reason="window_requires_separate_cte",
                location={"cte": cte_name(select, source.relation)},
                expected="聚合 CTE 只做聚合和门槛；排名放到下一个 CTE 的窗口函数",
                hint="同一个 SELECT 里同时聚合和开窗口无法证明释放边界。",
            )
        grouping = [
            self.value(e, sources).name
            for e in (select.args.get("group") or exp.Group()).expressions
        ]
        is_rank = capability.release == "self_rank"
        allowed = set(rules.group_columns(source.capability))
        if not set(grouping) <= allowed or (
            is_rank and grouping != [policy.scope_columns.get("district_id")]
        ):
            fail_shape(
                "参照池分组超出授权指标粒度；整体基准可不分组，不能按学校或届次拆组",
                reason="reference_grouping_not_allowed",
                location={"cte": cte_name(select, source.relation), "group_by": grouping},
                expected=f"不分组，或只按 {sorted(c for c in allowed if c)} 分组",
                hint="整体基准不能按学校/届次拆组；自身排名只按区县分组。",
                rejected_group_columns=grouping,
                allowed_group_columns=sorted(c for c in allowed if c),
            )
        having = select.args.get("having")
        formulas = {
            canonical(parse_one(formula)): name
            for name, formula in rules.aggregate_expressions.items()
        }
        samples = set()
        for item in select.expressions:
            expr = plain(item)
            if isinstance(expr, exp.Column):
                continue
            if isinstance(expr, exp.Round):
                expr = numeric_wrapper(expr.this)
            if (
                not is_rank
                and isinstance(expr, exp.Count)
                and self.expanded(expr, sources) not in formulas
            ):
                fail_shape(
                    "整体基准不开放人数、学生数或学校数输出；COUNT仅用于HAVING门槛，"
                    "SELECT只输出审核指标和允许的分组维度",
                    reason="baseline_count_output_not_allowed",
                    location={
                        "cte": cte_name(select, source.relation),
                        "projection": item.alias_or_name,
                    },
                    rejected=expr.sql(dialect=self.manifest.dialect),
                    expected="SELECT 只输出审核指标和允许的分组维度；COUNT 只出现在 HAVING",
                    hint="把 COUNT 从 SELECT 移到 HAVING；它不是可释放指标。",
                )
            metric = formulas.get(self.expanded(expr, sources))
            sample = rules.sample_columns.get(metric)
            if metric is None or sample is None:
                fail_shape(
                    "指标或有效样本字段没有审核定义",
                    reason="metric_definition_missing",
                    location={"cte": cte_name(select, source.relation)},
                    expected="只使用 reference_query_contract 中定义的指标和样本列",
                    hint="指标标识不是字段；请展开为审核后的聚合表达式。",
                )
            samples.add(sample)
        required_gates = {
            canonical(
                exp.Count(this=exp.column(sample))
            ): self.contract.reference_policy.minimum_sample_size
            for sample in samples
        }
        if not is_rank:
            unit_key = capability.reference_unit_key
            unit_column = policy.scope_columns.get(unit_key)
            if not unit_column:
                fail("AUTH_MAPPING_MISSING", "整体基准缺少参评单位维度")
            unit_gate = exp.Count(this=exp.Distinct(expressions=[exp.column(unit_column)]))
            required_gates[canonical(unit_gate)] = (
                self.contract.reference_policy.minimum_reference_units
            )
        having_terms = terms(having.this if having else None)
        grouped_gates = {key: [] for key in required_gates}
        if len(having_terms) == 1:
            for atom in having_terms[0]:
                if (
                    not isinstance(atom, exp.GTE)
                    or self.expanded(atom.left, sources) not in grouped_gates
                ):
                    fail_shape(
                        "参照池HAVING仅允许审核后的样本和单位数门槛",
                        reason="baseline_having_invalid",
                        location={
                            "cte": cte_name(select, source.relation),
                            "expression": atom.sql(dialect=self.manifest.dialect),
                        },
                        expected="HAVING "
                        + " AND ".join(f"{key} >= {value}" for key, value in required_gates.items()),
                        hint="HAVING 只能包含审核后的 COUNT(sample) 与 COUNT(DISTINCT unit) 门槛。",
                    )
                grouped_gates[self.expanded(atom.left, sources)].append(atom)
        if (
            not required_gates
            or len(having_terms) != 1
            or not all(
                atoms and self.minimum_gate(exp.and_(*atoms), sources, key, required_gates[key])
                for key, atoms in grouped_gates.items()
            )
        ):
            fail_shape(
                "缺少参照池有效样本数门槛",
                reason="baseline_having_required",
                location={"cte": cte_name(select, source.relation)},
                expected="HAVING "
                + " AND ".join(f"{key} >= {value}" for key, value in required_gates.items()),
                hint="参照池必须在聚合 CTE 内同时满足有效样本数和参评单位数门槛。",
            )
        columns = {}
        for e in select.expressions:
            expression = plain(e)
            if isinstance(expression, exp.Column):
                value = self.value(expression, sources)
                if value.name not in grouping:
                    fail_shape(
                        "不能输出参照池未分组字段",
                        reason="baseline_projection_not_grouped",
                        location={
                            "cte": cte_name(select, source.relation),
                            "projection": e.alias_or_name,
                        },
                        rejected=expression.sql(dialect=self.manifest.dialect),
                        expected="只输出已批准的分组维度或审核指标",
                        hint="未分组的列会泄露池内明细；把它加入允许的分组或移除。",
                    )
                columns[e.alias_or_name] = Value("target" if is_rank else "dimension", value.name)
                continue
            if not is_rank:
                if not isinstance(expression, exp.Round) or literal(
                    expression.args.get("decimals")
                ) != str(self.contract.reference_policy.precision):
                    precision = self.contract.reference_policy.precision
                    rejected = expression.sql(dialect=self.manifest.dialect)
                    fail_shape(
                        "整体基准必须按策略精度ROUND后释放",
                        reason="baseline_round_required",
                        location={
                            "cte": cte_name(select, source.relation),
                            "projection": e.alias_or_name,
                        },
                        rejected=rejected,
                        expected=f"ROUND({rejected}, {precision})",
                        hint="取整必须发生在参照池聚合 CTE 内；外层只引用已取整的值，"
                        "不要在外层再对未取整的 AVG 结果做 ROUND。",
                        precision=precision,
                        relation=source.relation,
                    )
                expression = expression.this
                expression = numeric_wrapper(expression)
            formula = self.expanded(expression, sources)
            if formula not in formulas:
                fail_shape(
                    "参照池只允许审核后的指标表达式",
                    reason="baseline_projection_not_allowed",
                    location={
                        "cte": cte_name(select, source.relation),
                        "projection": e.alias_or_name,
                    },
                    rejected=expression.sql(dialect=self.manifest.dialect),
                    expected="审核指标表达式，例如 "
                    + next(iter(rules.aggregate_expressions.values())),
                    hint="指标标识不是字段；请展开为 reference_query_contract 中的表达式。",
                )
            columns[e.alias_or_name] = Value("metric", formulas[formula])
        if not any(v.kind == "metric" for v in columns.values()):
            fail_shape(
                "参照池必须计算审核指标",
                reason="baseline_metric_required",
                location={"cte": cte_name(select, source.relation)},
                expected="至少输出一个审核指标",
                hint="参照池的用途是释放审核指标；只输出维度不足以构成基准。",
            )
        if not is_rank:
            self.releases.append(
                fingerprint(
                    {
                        "kind": source.capability,
                        "grant": source.grant_id,
                        "metrics": sorted(v.name for v in columns.values()),
                    }
                )
            )
        return Flow(
            "reference_groups" if is_rank else "baseline",
            columns,
            source.relation,
            source.capability,
            source.grant_id,
            source.target,
        )

    def minimum_gate(self, node, sources, expected, minimum):
        if node is None:
            return False
        branches = terms(node)
        if len(branches) != 1:
            return False
        for atom in branches[0]:
            if not isinstance(atom, exp.GTE) or literal(atom.right) is None:
                return False
            try:
                if (
                    self.expanded(atom.left, sources) != expected
                    or int(literal(atom.right)) < minimum
                ):
                    return False
            except (ValueError, TypeError):
                return False
        return bool(branches[0])

    def rank(self, select, sources, source):
        columns = {}
        for e in select.expressions:
            expression = plain(e)
            if isinstance(expression, exp.Column):
                columns[e.alias_or_name] = self.value(expression, sources)
                continue
            if (
                not isinstance(expression, exp.Window)
                or expression.args.get("partition_by")
                or expression.args.get("spec")
            ):
                fail_shape(
                    "排名窗口不能改变分区或人群",
                    reason="rank_window_invalid",
                    location={"cte": cte_name(select, source.relation)},
                    expected="RANK() OVER (ORDER BY 审核指标 DESC)，不分区、不指定 frame",
                    hint="排名必须对同一参照池全体单位进行；不要 PARTITION BY 或加窗口 frame。",
                )
            if isinstance(expression.this, exp.Rank):
                order = expression.args.get("order")
                if not order or len(order.expressions) != 1:
                    fail_shape(
                        "排名必须按审核指标降序",
                        reason="rank_order_invalid",
                        location={"cte": cte_name(select, source.relation)},
                        expected="ORDER BY 审核指标 DESC",
                        hint="排名窗口只能有一个排序键，且必须是审核指标降序。",
                    )
                ordered = order.expressions[0]
                if (
                    not ordered.args.get("desc")
                    or self.value(ordered.this, sources).kind != "metric"
                ):
                    fail_shape(
                        "排名必须按审核指标降序",
                        reason="rank_order_invalid",
                        location={"cte": cte_name(select, source.relation)},
                        expected="ORDER BY 审核指标 DESC",
                        hint="不能按维度、常量或升序排名。",
                    )
                columns[e.alias_or_name] = Value("rank", "city_rank")
                self.releases.append(
                    fingerprint(
                        {
                            "stage": "rank_window",
                            "grant": source.grant_id,
                            "metric": self.value(ordered.this, sources).name,
                            "direction": "DESC",
                        }
                    )
                )
            elif (
                isinstance(expression.this, exp.Count)
                and isinstance(expression.this.this, exp.Star)
                and not expression.args.get("order")
            ):
                columns[e.alias_or_name] = Value("units", "reference_units")
            else:
                fail_shape(
                    "未开放此参照池窗口函数",
                    reason="rank_window_unsupported",
                    location={"cte": cte_name(select, source.relation)},
                    expected="只允许 RANK() 和 COUNT(*) OVER ()",
                    hint="其他窗口函数请先确认是否在授权契约内。",
                )
        if not {"target", "rank", "units"} <= {v.kind for v in columns.values()}:
            fail_shape(
                "排名必须保留单位键、名次和参评数",
                reason="rank_output_incomplete",
                location={"cte": cte_name(select, source.relation)},
                expected="输出单位键、city_rank、reference_units",
                hint="排名结果缺少单位键/名次/参评数，无法证明只释放了自身名次。",
            )
        return Flow(
            "rank_pool", columns, source.relation, source.capability, source.grant_id, source.target
        )

    def release_rank(self, select, sources, source):
        if (
            select.args.get("group")
            or select.args.get("having")
            or any(e.find(exp.Window) for e in select.expressions)
        ):
            fail_shape(
                "自身排名输出不能再次聚合或开窗口",
                reason="rank_output_aggregate_not_allowed",
                location={"cte": cte_name(select, source.relation)},
                expected="最终输出只做自身单位过滤和指标取整",
                hint="排名已经完成；最终 SELECT 不要再 GROUP BY/HAVING/开窗口。",
            )
        alias = next(iter(sources))
        where = select.args.get("where")
        branches = terms(where.this if where else None)
        if len(branches) != 1:
            fail_shape(
                "排名输出必须明确限定自身单位",
                reason="rank_target_required",
                location={"cte": cte_name(select, source.relation)},
                expected="在排名结果上使用自身的区县条件（EQ 或 IN 明确值）",
                hint="不能省略自身范围，也不能用 OR 放宽到其他区县。",
            )
        target_fields = [k for k, v in source.columns.items() if v.kind == "target"]
        vals = column_values(branches[0], alias)
        if not any(vals.get(k) == {source.target} for k in target_fields):
            fail_shape(
                "缺少排名后的自身区县过滤",
                reason="rank_target_required",
                location={"cte": cte_name(select, source.relation)},
                expected="限定为授权给的自身区县值",
                hint="排名结果必须过滤到自己的区县，且只能使用授权值。",
            )
        gates = [a for a in branches[0] if isinstance(a, exp.GTE)]
        if not gates or not self.minimum_gate(
            exp.and_(*gates),
            sources,
            "reference_units",
            self.contract.reference_policy.minimum_reference_units,
        ):
            fail_shape(
                "缺少最少参评区县数门槛",
                reason="rank_units_gate_required",
                location={"cte": cte_name(select, source.relation)},
                expected=f"reference_units >= {self.contract.reference_policy.minimum_reference_units}",
                hint="参评区县数不足时不能释放名次。",
            )
        # No additional metric/target probes beyond the two release conditions.
        for atom in branches[0]:
            if isinstance(atom, exp.GTE):
                continue
            if not isinstance(atom, (exp.EQ, exp.In)) or not set(
                column_values([atom], alias)
            ) <= set(target_fields):
                fail_shape(
                    "排名输出含未批准筛选条件",
                    reason="rank_filter_not_allowed",
                    location={
                        "cte": cte_name(select, source.relation),
                        "expression": atom.sql(dialect=self.manifest.dialect),
                    },
                    expected="只保留自身区县条件和参评数门槛",
                    hint="额外谓词可以用来做精度探测，必须移除。",
                )
        columns = {}
        for e in select.expressions:
            expression = plain(e)
            if isinstance(expression, exp.Round):
                if literal(expression.args.get("decimals")) != str(
                    self.contract.reference_policy.precision
                ):
                    precision = self.contract.reference_policy.precision
                    fail_shape(
                        "自身排名指标精度无效",
                        reason="rank_round_required",
                        location={
                            "cte": cte_name(select, source.relation),
                            "projection": e.alias_or_name,
                        },
                        rejected=expression.sql(dialect=self.manifest.dialect),
                        expected=f"ROUND(指标, {precision})",
                        hint="排名结果指标必须按策略精度取整。",
                    )
                value = self.value(numeric_wrapper(expression.this), sources)
                if value.kind != "metric":
                    fail_shape(
                        "ROUND只能处理审核指标",
                        reason="rank_round_target_invalid",
                        location={"cte": cte_name(select, source.relation)},
                        rejected=expression.sql(dialect=self.manifest.dialect),
                        expected="ROUND 只包裹审核指标",
                        hint="单位键、名次和参评数不需要也不允许取整。",
                    )
            else:
                value = self.value(expression, sources)
                if value.kind not in {"target", "rank", "units"}:
                    fail_shape(
                        "排名结果指标必须按策略精度输出",
                        reason="rank_round_required",
                        location={
                            "cte": cte_name(select, source.relation),
                            "projection": e.alias_or_name,
                        },
                        rejected=expression.sql(dialect=self.manifest.dialect),
                        expected="指标列使用 ROUND(指标, 策略精度)",
                        hint="不要直接输出未取整的排名指标。",
                    )
            columns[e.alias_or_name] = value
        self.releases.append(
            fingerprint(
                {
                    "kind": source.capability,
                    "grant": source.grant_id,
                    "target": source.target,
                    "output": sorted(v.name for v in columns.values()),
                }
            )
        )
        return Flow("self_rank", columns)


def verify_compiled_permissions(tree, context, logical_proof):
    if logical_proof.contract_fingerprint != build_query_contract(context.manifest).fingerprint:
        fail("AUTH_CONTRACT_MISMATCH", "逻辑证明与当前查询契约不一致")
    if logical_proof.authorization_fingerprint != context.fingerprint:
        fail("AUTH_CONTEXT_STALE", "逻辑证明与当前有效授权不一致")
    try:
        proof = PermissionValidator(context).prove(tree, physical=True)
        if (proof.scans, proof.releases, proof.output_columns) != (
            logical_proof.scans,
            logical_proof.releases,
            logical_proof.output_columns,
        ):
            fail("AUTH_PHYSICAL_PROOF_FAILED", "编译结果与已验证语义查询的范围或输出契约不一致")
        return proof
    except AuthorizationError as exc:
        raise AuthorizationError(
            "AUTH_PHYSICAL_PROOF_FAILED", "无法证明编译计划保留了授权范围和输出边界"
        ) from exc
