"""Versioned, executable permission contract shared by context, proof and recovery.

This module describes accepted SQL and recovery decisions. It never generates,
rewrites or executes SQL and never accepts permission rules from a model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse

CONTRACT_VERSION = "education-sql/1"
PROVER_VERSION = "relational-proof/2"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SQLGrammar(ContractModel):
    max_sql_length: int = 50_000
    functions: tuple[str, ...] = tuple(
        """
        ABS AVG CAST CASE CEIL CEILING COALESCE CONCAT CONCAT_WS COUNT COUNT_IF
        CURRENT_DATE CURRENT_TIMESTAMP DATE DATE_ADD DATE_DIFF DATE_PART DATE_SUB DATE_TRUNC
        DENSE_RANK EXTRACT FIRST_VALUE FLOOR GREATEST IF IFNULL LAG LAST_VALUE LEAD
        LEAST LENGTH LOWER LTRIM MAX MIN MOD NULLIF NTILE PERCENT_RANK PERCENTILE_CONT
        PERCENTILE_DISC POWER RANK REPLACE ROUND ROW_NUMBER RTRIM SQRT STDDEV STDDEV_POP
        STDDEV_SAMP SUBSTR SUBSTRING SUM TIMESTAMPDIFF TO_CHAR TRIM TRY_CAST UPPER
        VARIANCE VAR_POP VAR_SAMP CORR CUME_DIST STR_TO_DATE DATE_FORMAT TIME_TO_STR
    """.split()
    )
    max_scopes: int = 64
    max_predicate_branches: int = 64
    max_expanded_nodes: int = 50_000
    max_normalization_passes: int = 64
    joins: tuple[str, ...] = ("", "INNER", "CROSS")
    set_operations: tuple[str, ...] = ("UNION ALL",)
    correlated_subqueries: bool = False
    scalar_subqueries: bool = False
    recursive_ctes: bool = False
    shared_projection_ctes: bool = True
    reference_pool_joins_before_release: bool = False
    reference_pool_extra_population_filters: bool = False


SQL_GRAMMAR = SQLGrammar()


class CapabilityContract(ContractModel):
    pool_scope_keys: tuple[str, ...]
    reference_unit_key: str
    release: Literal["baseline", "self_rank"]
    fixed_subject: bool = False
    grant_levels: tuple[str, ...]


CAPABILITIES = {
    "grade_baseline": CapabilityContract(
        pool_scope_keys=("city_id", "school_id", "cohort_id"),
        reference_unit_key="class_id",
        release="baseline",
        grant_levels=("class",),
    ),
    "city_baseline": CapabilityContract(
        pool_scope_keys=("city_id", "cohort_id"),
        reference_unit_key="school_id",
        release="baseline",
        grant_levels=("district", "school"),
    ),
    "district_self_rank": CapabilityContract(
        pool_scope_keys=("city_id", "cohort_id"),
        reference_unit_key="district_id",
        release="self_rank",
        fixed_subject=True,
        grant_levels=("district",),
    ),
}

# Human- and model-readable capability boundaries. Structural rules stay in
# CapabilityContract; this catalog tells the planner what a capability can and
# cannot answer so an unavailable question is declined before SQL is written.
CAPABILITY_CATALOG = {
    "grade_baseline": {
        "label": "本校同届次年级整体基准",
        "answers": ["本班与本校同届次、同考试的年级均值/分差"],
        "not_answers": ["其他班级/其他学校的原始明细", "学生个人全校名次", "跨校排名"],
    },
    "city_baseline": {
        "label": "全市整体基准",
        "answers": ["本校/本区与全市同届次、同考试的均值对照"],
        "not_answers": ["其他学校明细", "全市学生个人排名"],
    },
    "district_self_rank": {
        "label": "本区县全市名次（仅自身）",
        "answers": ["本区县自身的全市名次和参评区县数"],
        "not_answers": ["其他区县名次", "全市学校/学生排名"],
    },
}


class RelationQueryContract(ContractModel):
    metric_identifiers_are_fields: bool = False
    aggregate_expressions: dict[str, str]
    sample_columns: dict[str, str | None]
    scope_columns: dict[str, str]
    implicit_scope: dict[str, str]
    population_filters: dict[str, str]
    forbidden_columns: tuple[str, ...]
    filter_only_columns: tuple[str, ...]
    fixed_exam_column: str
    subject_column: str | None
    date_column: str | None
    allowed_baseline_group_columns: tuple[str, ...]
    capabilities: dict[str, CapabilityContract]
    baseline_scope_keys: dict[str, tuple[str, ...]]
    additional_population_filters_allowed: bool = False
    sample_not_null_filter_required: bool = False
    baseline_count_output_allowed: bool
    approved_count_metric_ids: tuple[str, ...]
    baseline_projection: str = "ROUND(approved aggregate expression, precision)"
    comparison_shape: str = (
        "Aggregate own scope and reference pool separately, then join released results"
    )

    def group_columns(self, capability: str):
        rule = self.capabilities[capability]
        return (
            (self.scope_columns.get(rule.reference_unit_key),)
            if rule.release == "self_rank"
            else self.allowed_baseline_group_columns
        )


class ReferenceThresholds(ContractModel):
    minimum_sample_size: int
    minimum_reference_units: int
    precision: int


class RecoveryContract(ContractModel):
    max_sql_repairs: int = 2
    technical_failures_allow_human_retry: bool = False
    compiled_proof_failures_allow_model_retry: bool = False


RECOVERY_CONTRACT = RecoveryContract()
CONTRACT_INSTRUCTIONS = (
    "SQL由模型完整生成，宿主不补WHERE。多授权按完整元组OR组合。implicit_scope无需编造数据库字段。"
    "metrics/aggregate_expressions的键是指标标识，必须展开为批准的表达式；宽表直接按各科字段计算。"
    "参照池只在scope_grants包含对应capability时可用，按capability.pool_scope_keys固定人群，"
    "固定一个考试及一个届次，保留population_filters和required_row_rules，不添加额外人群筛选。"
    "AVG忽略NULL，不添加成绩IS NOT NULL。基准可不分组，或只按allowed_baseline_group_columns分组。"
    "SELECT只输出批准指标及分组维度，基准按precision做ROUND；未经批准的COUNT只放HAVING。"
    "每个指标对应sample_columns的COUNT须达到minimum_sample_size；整体基准还须对"
    "capability.reference_unit_key对应列COUNT(DISTINCT)达到minimum_reference_units。"
    "本校与参照池先分别聚合再关联计算分差，不在参照原始池用CASE/窗口提取本校。"
    "自身排名按区县聚合、RANK/COUNT OVER，再限定自身区县及参评数，不输出其他区县明细。"
    "错误的category和recovery_action由宿主决定；repair_sql时自行修正，不能让用户确认继续、"
    "重新授权或缩小范围来代替技术修复。stop/contact_admin/refresh_context时停止查询并说明对应原因。"
)


class PermissionQueryContract(ContractModel):
    version: str = CONTRACT_VERSION
    prover_version: str = PROVER_VERSION
    grammar: SQLGrammar = SQL_GRAMMAR
    recovery: RecoveryContract = RECOVERY_CONTRACT
    reference_policy: ReferenceThresholds
    relations: dict[str, RelationQueryContract]

    @property
    def fingerprint(self):
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def identity(self):
        return {
            "version": self.version,
            "prover_version": self.prover_version,
            "fingerprint": self.fingerprint,
        }


def relation_contract(policy):
    count_metrics = []
    for name, formula in policy.metrics.items():
        expressions = parse(formula)
        if len(expressions) != 1 or expressions[0] is None:
            raise ValueError("指标必须是单个聚合表达式")
        expression = expressions[0]
        if (
            not expression.find(exp.AggFunc)
            or expression.find(exp.Query)
            or expression.find(exp.Window)
            or any(col.name not in policy.columns for col in expression.find_all(exp.Column))
        ):
            raise ValueError("指标必须使用已发布字段的聚合表达式，不能包含子查询或窗口")
        sample = policy.metric_samples.get(name, policy.sample_column)
        if sample not in policy.columns:
            raise ValueError("每个审核指标都必须有已发布的有效样本字段")
        if isinstance(expression, exp.Count):
            count_metrics.append(name)
    return RelationQueryContract(
        aggregate_expressions=policy.metrics,
        sample_columns={
            name: policy.metric_samples.get(name, policy.sample_column) for name in policy.metrics
        },
        scope_columns=policy.scope_columns,
        implicit_scope=policy.implicit_scope,
        population_filters=policy.population_filters,
        forbidden_columns=policy.forbidden_columns,
        filter_only_columns=policy.filter_only_columns,
        fixed_exam_column=policy.exam_column,
        subject_column=policy.subject_column,
        date_column=policy.date_column,
        allowed_baseline_group_columns=tuple(
            c for c in (policy.exam_column, policy.subject_column) if c
        ),
        capabilities={} if policy.public else CAPABILITIES,
        baseline_scope_keys={
            name: rule.pool_scope_keys
            for name, rule in CAPABILITIES.items()
            if rule.release == "baseline"
        },
        baseline_count_output_allowed=bool(count_metrics),
        approved_count_metric_ids=tuple(sorted(count_metrics)),
    )


def build_query_contract(manifest):
    return PermissionQueryContract(
        reference_policy=ReferenceThresholds(
            minimum_sample_size=manifest.minimum_sample_size,
            minimum_reference_units=manifest.minimum_reference_units,
            precision=manifest.precision,
        ),
        relations={name: relation_contract(policy) for name, policy in manifest.relations.items()},
    )


# One classification for API errors, model repair and user-visible messages.
# Unknown codes fail closed and are never made repairable by a caller's flag.
_ERROR_RULES = {
    "QUERY_SQL_UNSUPPORTED": ("query_unsupported", "sql_validation", "repair_sql"),
    "QUERY_PLAN_UNSUPPORTED": ("query_unsupported", "planning", "repair_sql"),
    "QUERY_SERVICE_UNAVAILABLE": ("service_unavailable", "planning", "contact_admin"),
    "QUERY_PUBLICATION_STALE": ("context_changed", "planning", "refresh_context"),
    "QUERY_PLAN_INVALID": ("compiled_plan_unverifiable", "physical_verification", "contact_admin"),
    "QUERY_EXECUTION_FAILED": ("execution_failed", "execution", "contact_admin"),
    "FIELD_NOT_PUBLISHED": ("query_invalid", "field_resolution", "repair_sql"),
    "FIELD_WRONG_SOURCE": ("query_invalid", "field_resolution", "repair_sql"),
    "AUTH_SCOPE_MISSING": ("query_invalid", "sql_validation", "repair_sql"),
    "AUTH_RELEASE_SHAPE_INVALID": ("query_invalid", "sql_validation", "repair_sql"),
    "AUTH_QUERY_SHAPE_UNSUPPORTED": ("query_unsupported", "sql_validation", "repair_sql"),
    "AUTH_COLUMN_FORBIDDEN": ("access_denied", "authorization", "stop"),
    "AUTH_SCOPE_OUTSIDE_GRANT": ("access_denied", "authorization", "stop"),
    "AUTH_REVOKED": ("access_denied", "authorization", "stop"),
    "AUTH_MAPPING_MISSING": ("policy_invalid", "policy_loading", "contact_admin"),
    "AUTH_CONTEXT_INVALID": ("policy_invalid", "policy_loading", "contact_admin"),
    "AUTH_CONTEXT_STALE": ("context_changed", "authorization", "refresh_context"),
    "AUTH_CONTRACT_MISMATCH": ("context_changed", "contract_verification", "refresh_context"),
    "AUTH_PHYSICAL_PROOF_FAILED": (
        "compiled_plan_unverifiable",
        "physical_verification",
        "contact_admin",
    ),
}

# User-facing attribution. Authorization and SQL-shape failures must never be
# collapsed into one "permission" message: they have different owners and fixes.
_ERROR_DOMAIN = {
    "AUTH_COLUMN_FORBIDDEN": "authorization",
    "AUTH_SCOPE_OUTSIDE_GRANT": "authorization",
    "AUTH_REVOKED": "authorization",
    "AUTH_RELEASE_SHAPE_INVALID": "sql_shape",
    "AUTH_SCOPE_MISSING": "sql_shape",
    "AUTH_QUERY_SHAPE_UNSUPPORTED": "sql_shape",
    "FIELD_NOT_PUBLISHED": "sql_shape",
    "FIELD_WRONG_SOURCE": "sql_shape",
    "QUERY_SQL_UNSUPPORTED": "sql_shape",
    "QUERY_PLAN_UNSUPPORTED": "compiled_plan",
    "QUERY_PLAN_INVALID": "compiled_plan",
    "AUTH_PHYSICAL_PROOF_FAILED": "compiled_plan",
    "QUERY_EXECUTION_FAILED": "execution",
    "QUERY_SERVICE_UNAVAILABLE": "service",
    "AUTH_MAPPING_MISSING": "configuration",
    "AUTH_CONTEXT_INVALID": "configuration",
    "AUTH_CONTEXT_STALE": "context",
    "AUTH_CONTRACT_MISMATCH": "context",
    "QUERY_PUBLICATION_STALE": "context",
}


def permission_error_detail(code, message, *, retryable=None, **details):
    category, stage, action = _ERROR_RULES.get(
        code, ("policy_invalid", "policy_loading", "contact_admin")
    )
    can_repair = action == "repair_sql" and retryable is not False
    if action == "repair_sql" and not can_repair:
        action = "stop"
    domain = _ERROR_DOMAIN.get(code, "configuration")
    public = {
        "query_invalid": f"这条查询的写法未通过规则校验（你的账号权限正常）：{message}",
        "service_unavailable": "语义规划服务暂时不可用，查询已停止，请稍后重试或联系管理员。",
        "execution_failed": "查询执行失败，无法给出本次结果，请联系管理员检查数据源。",
        "query_unsupported": f"当前校验器暂不支持这种查询结构：{message}",
        "access_denied": f"当前授权不允许这项操作：{message}",
        "policy_invalid": "数据源权限配置不可用，查询已停止，请联系管理员处理。",
        "context_changed": "查询上下文或校验契约已变化，请重新发起问题。",
        "compiled_plan_unverifiable": "编译结果未通过安全校验，查询已停止，请联系管理员处理。",
    }[category]
    return {
        **details,
        "code": code,
        "message": message,
        "category": category,
        "failure_domain": domain,
        "is_permission_issue": domain == "authorization",
        "error_schema_version": 2,
        "failure_stage": stage,
        "recovery_action": action,
        "retryable": can_repair,
        "sql_repair_required": can_repair,
        "requires_user_input": False,
        "user_message": public,
        "contract_version": CONTRACT_VERSION,
    }


@dataclass
class PermissionRecovery:
    """Turn-local host state; model text and human replies cannot extend the budget.

    The SQL repair budget is scoped to one analysis stage. Candidate discovery
    (``lookup_question_context``) is validated by the same prover but must not
    consume the query repair budget: a failed probe says nothing about the
    model's ability to repair the business query.
    """

    max_repairs: int = RECOVERY_CONTRACT.max_sql_repairs
    stage_repairs: dict[object, int] = field(default_factory=dict)
    blocked: bool = False
    blocked_stages: set[object] = field(default_factory=set)
    last_error: dict | None = None
    _terminal_error: dict | None = field(default=None, repr=False)

    @property
    def human_retry_blocked(self):
        return self.last_error is not None

    def remaining(self, stage=None):
        if self.blocked or self.stage_blocked(stage):
            return 0
        return max(0, self.max_repairs + 1 - self.stage_repairs.get(stage, 0))

    def stage_blocked(self, stage=None):
        return stage in self.blocked_stages

    def reject(self, detail, *, stage=None, tool="query_semantic"):
        if self.blocked:
            return self.last_error
        # Normalize older tool adapters too; a caller cannot relabel a denial as
        # repairable or let a model-provided flag reopen a terminal failure.
        self.last_error = permission_error_detail(
            detail["code"],
            detail.get("message", "查询校验失败"),
            retryable=detail.get("retryable"),
            **{k: v for k, v in detail.items() if k not in {"code", "message", "retryable"}},
        )
        if tool != "query_semantic":
            # Discovery failures are reported, but they do not spend SQL repairs.
            self.last_error["remaining_sql_repairs"] = self.remaining(stage)
            return self.last_error
        if self.last_error.get("recovery_action") != "repair_sql":
            self.blocked = True
            self.last_error["remaining_sql_repairs"] = 0
            if self._terminal_error is None:
                self._terminal_error = self.last_error
            return self.last_error
        used = self.stage_repairs.get(stage, 0) + 1
        self.stage_repairs[stage] = used
        self.last_error["remaining_sql_repairs"] = max(0, self.max_repairs + 1 - used)
        if used > self.max_repairs:
            # No plan means the whole turn is one stage; stop globally like before.
            if stage is None:
                self.blocked = True
            else:
                self.blocked_stages.add(stage)
            self.last_error.update(
                recovery_action="stop",
                retryable=False,
                sql_repair_required=False,
                repair_budget_exhausted=True,
                remaining_sql_repairs=0,
            )
            if self._terminal_error is None:
                self._terminal_error = self.last_error
        return self.last_error

    def success(self):
        if not self.blocked and not self.blocked_stages:
            self.last_error = None

    def public_error(self):
        error = self._terminal_error or self.last_error
        return error.get("user_message") if error else None
