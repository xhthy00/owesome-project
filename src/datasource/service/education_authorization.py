"""Versioned education grants stored in sys_user.system_variables; no new tables.

This module contains policy facts, not SQL generation. Resource manifests are
administrator-reviewed deployment configuration, never supplied by the model.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.datasource.service.semantic_permission_contract import (
    CAPABILITIES,
    CAPABILITY_CATALOG,
    CONTRACT_INSTRUCTIONS,
    build_query_contract,
    permission_error_detail,
    relation_contract,
)
from src.datasource.service.semantic_permission_examples import canonical_examples

AUTH_KEY = "edu_auth_v2"
EDUCATION_RESOURCE_ID = "education"
_LEGACY_EDUCATION_ID = re.compile(r"^education-[0-9a-f]{16}$")
SCOPE_KEYS = ("city_id", "district_id", "school_id", "cohort_id", "class_id", "student_id")
REQUIRED = {
    "city": ("city_id",),
    "district": ("city_id", "district_id"),
    "school": ("city_id", "school_id"),
    "grade": ("city_id", "school_id", "cohort_id"),
    "class": ("city_id", "school_id", "cohort_id", "class_id"),
    "student": ("city_id", "school_id", "cohort_id", "student_id"),
}


class AuthorizationError(ValueError):
    def __init__(self, code: str, message: str, *, retryable=None, **details):
        super().__init__(message)
        self.detail = permission_error_detail(code, message, retryable=retryable, **details)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EducationGrant(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    workspace_oid: int = Field(gt=0)
    datasource_id: int = Field(gt=0)
    level: Literal["city", "district", "school", "grade", "class", "student"]
    city_id: str = Field(min_length=1, max_length=128)
    district_id: str | None = Field(default=None, min_length=1, max_length=128)
    school_id: str | None = Field(default=None, min_length=1, max_length=128)
    cohort_id: str | None = Field(default=None, min_length=1, max_length=128)
    class_id: str | None = Field(default=None, min_length=1, max_length=128)
    student_id: str | None = Field(default=None, min_length=1, max_length=128)
    capabilities: tuple[Literal["grade_baseline", "city_baseline", "district_self_rank"], ...] = ()
    can_delegate: bool = False
    enabled: bool = True
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    history_from: date | None = None
    history_to: date | None = None
    remark: str = Field(default="", max_length=200)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def aware_time(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("授权时间必须包含时区")
        return value

    @model_validator(mode="after")
    def coherent(self):
        for key in REQUIRED[self.level]:
            if not getattr(self, key):
                raise ValueError(f"{self.level} 权限必须提供 {key}")
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("valid_to 必须晚于 valid_from")
        if self.history_from and self.history_to and self.history_from > self.history_to:
            raise ValueError("历史访问区间无效")
        allowed = {name for name, rule in CAPABILITIES.items() if self.level in rule.grant_levels}
        if not set(self.capabilities) <= allowed:
            raise ValueError("该范围不允许配置所选对照能力")
        return self

    def keys(self):
        return {key: getattr(self, key) for key in SCOPE_KEYS if getattr(self, key) is not None}

    def active(self, now: datetime):
        return (
            self.enabled
            and (self.valid_from is None or self.valid_from <= now)
            and (self.valid_to is None or now < self.valid_to)
        )


class EducationAuthorization(StrictModel):
    schema_version: Literal[2] = 2
    revision: int = Field(default=0, ge=0)
    grants: tuple[EducationGrant, ...] = Field(default=(), max_length=500)
    managed_resources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self):
        if len({g.id for g in self.grants}) != len(self.grants):
            raise ValueError("授权 ID 不能重复")
        return self


class RelationPolicy(StrictModel):
    # Logical column -> reviewed physical column/expression. SQL is never generated
    # from these expressions; they are used only to recognize a compiled AST.
    physical_table: str
    columns: dict[str, str] = Field(min_length=1)
    scope_columns: dict[str, str]
    implicit_scope: dict[str, str] = Field(default_factory=dict)
    metric_samples: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, str] = Field(default_factory=dict)
    public: bool = False
    forbidden_columns: tuple[str, ...] = ()
    filter_only_columns: tuple[str, ...] = ()
    exam_column: str = "exam_batch_id"
    date_column: str | None = None
    subject_column: str | None = "subject"
    sample_column: str | None = "score_value"
    population_filters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_columns(self):
        names = set(self.columns)
        if not set(self.scope_columns) <= set(SCOPE_KEYS):
            raise ValueError("未知权限维度")
        used = set(self.scope_columns.values()) | set(self.population_filters)
        used |= set(self.forbidden_columns) | set(self.filter_only_columns)
        if not used <= names:
            raise ValueError("策略引用了未发布字段")
        if not set(self.implicit_scope) <= {"city_id"}:
            raise ValueError("只有单一学情连接的城市范围可以是隐式绑定")
        if set(self.implicit_scope) & set(self.scope_columns):
            raise ValueError("同一权限维度不能同时由逻辑资源和数据库字段定义")
        if not set(self.metric_samples.values()) <= names:
            raise ValueError("指标样本字段未发布")
        if not self.public and (
            "city_id" not in self.scope_columns
            and "city_id" not in self.implicit_scope
            or self.exam_column not in names
        ):
            raise ValueError("业务来源必须提供城市和考试键")
        return self


class ConnectionIdentity(StrictModel):
    host: str = Field(min_length=1)
    port: int = Field(gt=0, le=65535)
    database: str = Field(min_length=1)
    username: str = Field(min_length=1)


class PermissionManifest(StrictModel):
    workspace_oid: int = Field(gt=0)
    datasource_id: int = Field(gt=0)
    bundle_id: str
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_version: int = Field(default=1, gt=0)
    dialect: Literal["postgres", "mysql"] = "postgres"
    physical_schema: str = "public"
    connection: ConnectionIdentity
    relations: dict[str, RelationPolicy] = Field(min_length=1)
    catalog: tuple[dict[str, str], ...] = ()
    minimum_sample_size: int = Field(default=10, ge=2)
    minimum_reference_units: int = Field(default=3, ge=3)
    precision: int = Field(default=2, ge=0, le=4)

    @model_validator(mode="after")
    def distinct_sources(self):
        if len({r.physical_table.lower() for r in self.relations.values()}) != len(self.relations):
            raise ValueError("本版本要求逻辑来源与物理表一一对应")
        try:
            build_query_contract(self)
        except Exception as exc:
            raise ValueError("权限契约中的指标或样本定义无效") from exc
        return self


def fingerprint(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def manifests(path: str | None = None) -> dict[int, PermissionManifest]:
    if path is None:
        from src.common.core.config import get_settings

        path = get_settings().semantic_permission_manifest_path
    if not path:
        return {}
    # A configured but unreadable/malformed manifest fails closed.
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    result = {}
    for item in raw["bindings"]:
        manifest = PermissionManifest.model_validate(item)
        if manifest.datasource_id in result:
            raise ValueError("数据源权限 manifest 重复")
        result[manifest.datasource_id] = manifest
    return result


def manifest_for(datasource_id: int, path: str | None = None):
    try:
        return manifests(path).get(int(datasource_id))
    except Exception as exc:
        raise AuthorizationError(
            "AUTH_MAPPING_MISSING", "权限发布映射配置无效，查询已停止"
        ) from exc


def normalize_education_grant(grant: EducationGrant, manifest: PermissionManifest | None):
    """Read compatibility for the former connection-derived city markers.

    Only the fixed education resource's own workspace/datasource is normalized.
    No school, cohort, class, capability, validity or stored revision is changed.
    """
    if (
        manifest is not None
        and (grant.workspace_oid, grant.datasource_id)
        == (manifest.workspace_oid, manifest.datasource_id)
        and any(
            p.implicit_scope.get("city_id") == EDUCATION_RESOURCE_ID
            for p in manifest.relations.values()
        )
        and _LEGACY_EDUCATION_ID.fullmatch(grant.city_id)
    ):
        return grant.model_copy(update={"city_id": EDUCATION_RESOURCE_ID})
    return grant


def read_authorization(
    user, *, manifest: PermissionManifest | None = None
) -> EducationAuthorization:
    variables = getattr(user, "system_variables", None) or {}
    if not isinstance(variables, dict):
        raise AuthorizationError("AUTH_CONTEXT_INVALID", "用户变量必须为对象")
    try:
        document = EducationAuthorization.model_validate(variables.get(AUTH_KEY, {}))
        if manifest is not None:
            document = document.model_copy(
                update={
                    "grants": tuple(normalize_education_grant(g, manifest) for g in document.grants)
                }
            )
        return document
    except (ValueError, TypeError) as exc:
        raise AuthorizationError("AUTH_CONTEXT_INVALID", "教育权限配置无效") from exc


def context_for(
    user,
    workspace_oid: int,
    datasource_id: int,
    manifest: PermissionManifest,
    *,
    now=None,
    session=None,
):
    if user is None or int(getattr(user, "status", 0)) != 1:
        raise AuthorizationError("AUTH_REVOKED", "账号不可用")
    if manifest.workspace_oid != workspace_oid or manifest.datasource_id != datasource_id:
        raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "数据源权限映射不属于当前空间")
    document = read_authorization(user, manifest=manifest)
    now = now or datetime.now(timezone.utc)
    grants = tuple(
        g
        for g in document.grants
        if g.workspace_oid == workspace_oid and g.datasource_id == datasource_id and g.active(now)
    )
    if not grants:
        raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "当前数据源没有有效的教育授权")
    for grant in grants:
        validate_grant_catalog(grant, manifest)
    # Active grant IDs change on expiry, invalidating checkpoints and in-flight results.
    context = AuthorizationContext(
        user_id=int(user.id),
        document=document,
        grants=grants,
        manifest=manifest,
        fingerprint=fingerprint(
            {
                "user": user.id,
                "document": document.model_dump(mode="json"),
                "active": [g.id for g in grants],
                "manifest": manifest.model_dump(mode="json"),
                "query_contract": build_query_contract(manifest).identity(),
            }
        ),
    )
    return with_legacy_constraints(session, user, context) if session is not None else context


class AuthorizationContext(StrictModel):
    user_id: int
    document: EducationAuthorization
    grants: tuple[EducationGrant, ...]
    manifest: PermissionManifest
    fingerprint: str
    required_row_rules: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    def model_context(self):
        contract = build_query_contract(self.manifest)
        levels = {grant.level for grant in self.grants}
        student_scope = (
            "本班学生成绩明细（匿名学号、各科分数、总分）"
            if levels & {"class", "student"}
            else "授权范围内的成绩聚合（学校/区县/班级维度）"
        )
        return {
            "schema_version": 2,
            "authorization_fingerprint": self.fingerprint,
            "query_contract": {
                **contract.identity(),
                "grammar": contract.grammar.model_dump(mode="json"),
                "recovery": contract.recovery.model_dump(mode="json"),
            },
            "binding": {
                "bundle_id": self.manifest.bundle_id,
                "manifest_hash": self.manifest.manifest_hash,
                "mapping_version": self.manifest.mapping_version,
                "mapping_fingerprint": fingerprint(self.manifest),
            },
            "scope_grants": [g.model_dump(mode="json") for g in self.grants],
            "scope_answers": [
                student_scope,
                "授权范围内的排名、分数分布、均分/及格率等聚合",
                "授权范围与参照基准的对照（需对应 capability）",
            ],
            "scope_not_answers": [
                "授权范围外的其他班级/学校明细",
                "未开通的学生个人全校名次、跨校排名",
            ],
            "capability_catalog": [
                {
                    "id": name,
                    **CAPABILITY_CATALOG[name],
                    "granted": any(name in g.capabilities for g in self.grants),
                }
                for name in CAPABILITIES
            ],
            "reference_examples": canonical_examples(self.manifest, self.grants),
            "relations": {
                name: {
                    "scope_columns": p.scope_columns,
                    "implicit_scope": p.implicit_scope,
                    "metrics": p.metrics,
                    "exam_column": p.exam_column,
                    "date_column": p.date_column,
                    "subject_column": p.subject_column,
                    "sample_column": p.sample_column,
                    "metric_samples": p.metric_samples,
                    "population_filters": p.population_filters,
                    "forbidden_columns": p.forbidden_columns,
                    "filter_only_columns": p.filter_only_columns,
                    "columns": list(p.columns),
                    "public": p.public,
                    "reference_query_contract": contract.relations[name].model_dump(mode="json"),
                }
                for name, p in self.manifest.relations.items()
            },
            "reference_policy": contract.reference_policy.model_dump(mode="json"),
            "required_row_rules": self.required_row_rules,
            "instructions": CONTRACT_INSTRUCTIONS,
        }


def reference_query_contract(policy: RelationPolicy):
    """Compatibility view of the same typed rules used by the proof engine."""
    return relation_contract(policy).model_dump(mode="json")


def validate_grant_catalog(grant: EducationGrant, manifest: PermissionManifest):
    grant = normalize_education_grant(grant, manifest)
    if not manifest.catalog:
        raise AuthorizationError("AUTH_MAPPING_MISSING", "请先配置并核验该数据源的组织/届次目录")
    keys = {k: v for k, v in grant.keys().items() if k != "student_id"}
    if not any(all(row.get(k) == v for k, v in keys.items()) for row in manifest.catalog):
        raise AuthorizationError(
            "AUTH_SCOPE_OUTSIDE_GRANT", "组织、届次或班级不属于审核目录中的同一授权元组"
        )


def reject_unverified_execution(datasource_id: int, user=None):
    managed = user is not None and any(
        key.endswith(f":{datasource_id}") for key in read_authorization(user).managed_resources
    )
    if managed or manifest_for(datasource_id) is not None:
        raise AuthorizationError(
            "AUTH_QUERY_SHAPE_UNSUPPORTED", "此数据源只接受已验证的语义SQL，请使用智能问数入口"
        )


def record_accessible(session, actor, conversation, record):
    """Legacy records on a newly enabled source are unavailable until regenerated."""
    from src.datasource.service.education_source import permission_manifest_for_user

    policy = (
        permission_manifest_for_user(actor, conversation.oid, conversation.datasource_id)
        if conversation.datasource_id
        else None
    )
    variables = getattr(actor, "system_variables", None) or {}
    managed = (
        AUTH_KEY in variables
        and f"{conversation.oid}:{conversation.datasource_id}"
        in read_authorization(actor).managed_resources
    )
    if policy is None:
        return not managed
    try:
        assert_workspace_member(session, actor, conversation.oid)
        ctx = context_for(
            actor, conversation.oid, conversation.datasource_id, policy, session=session
        )
        stored = json.loads(record.exec_result or "{}")
        return (
            stored.get("permission_mode") == "model_explicit_v2"
            and stored.get("authorization_fingerprint") == ctx.fingerprint
        )
    except (AuthorizationError, ValueError, TypeError):
        return False


def assert_workspace_member(session, user, workspace_oid):
    from system.authz import is_platform_admin
    from system.models.workspace import SysUserWorkspace

    if user is None or int(getattr(user, "status", 0)) != 1:
        raise AuthorizationError("AUTH_REVOKED", "账号不可用")
    if is_platform_admin(user):
        return
    if (
        session.query(SysUserWorkspace)
        .filter(SysUserWorkspace.uid == user.id, SysUserWorkspace.oid == workspace_oid)
        .first()
        is None
    ):
        raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "不是当前工作空间成员")


def with_legacy_constraints(session, user, context):
    """Read legacy hard rules as validation obligations, never inject them into SQL."""
    from sqlglot import exp, parse_one

    from src.datasource.service.query_permission import (
        collect_hidden_column_names_for_logical_table,
        collect_row_predicate_sqls,
    )

    policies = dict(context.manifest.relations)
    rules = {}
    for name, policy in policies.items():
        fragments = collect_row_predicate_sqls(
            session,
            user.id,
            context.manifest.datasource_id,
            [policy.physical_table],
            "mysql" if context.manifest.dialect == "mysql" else "pg",
        )
        inverse = {
            expression: logical
            for logical, expression in policy.columns.items()
            if expression.isidentifier()
        }
        normalized = []
        for fragment in fragments:
            tree = parse_one(fragment, read=context.manifest.dialect)
            for col in tree.find_all(exp.Column):
                if col.name not in inverse:
                    raise AuthorizationError(
                        "AUTH_MAPPING_MISSING", "原行权限字段无法映射至语义模型"
                    )
                col.set("this", exp.to_identifier(inverse[col.name]))
                col.set("table", None)
            normalized.append(tree.sql())
        if normalized:
            rules[name] = tuple(normalized)
        hidden = collect_hidden_column_names_for_logical_table(
            session, user.id, context.manifest.datasource_id, policy.physical_table
        )
        mapped_hidden = {
            logical
            for logical, expression in policy.columns.items()
            if any(
                c.name.lower() in {h.lower() for h in hidden}
                for c in parse_one(expression).find_all(exp.Column)
            )
        }
        policies[name] = policy.model_copy(
            update={
                "forbidden_columns": tuple(sorted(set(policy.forbidden_columns) | mapped_hidden))
            }
        )
    manifest = context.manifest.model_copy(update={"relations": policies})
    return context.model_copy(
        update={
            "manifest": manifest,
            "required_row_rules": rules,
            "fingerprint": fingerprint(
                {
                    "base": context.fingerprint,
                    "rules": rules,
                    "manifest": manifest.model_dump(mode="json"),
                }
            ),
        }
    )


def assert_can_manage(
    session, actor, target, workspace_oid, datasource_id, proposed=(), *, manifest=None
):
    from system.authz import is_platform_admin
    from system.models.workspace import SysUserWorkspace

    if int(getattr(actor, "status", 0)) != 1:
        raise AuthorizationError("AUTH_REVOKED", "账号不可用")
    if (
        session.query(SysUserWorkspace)
        .filter(SysUserWorkspace.uid == target.id, SysUserWorkspace.oid == workspace_oid)
        .first()
        is None
    ):
        raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "目标用户不属于当前工作空间")
    if is_platform_admin(actor):
        return
    manager = (
        session.query(SysUserWorkspace)
        .filter(
            SysUserWorkspace.uid == actor.id,
            SysUserWorkspace.oid == workspace_oid,
            SysUserWorkspace.weight == 1,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    delegations = [
        g
        for g in read_authorization(actor, manifest=manifest).grants
        if g.workspace_oid == workspace_oid
        and g.datasource_id == datasource_id
        and g.active(now)
        and g.can_delegate
    ]
    if manager is None or not delegations:
        raise AuthorizationError(
            "AUTH_SCOPE_OUTSIDE_GRANT", "仅获委派的当前空间管理员可管理此数据源权限"
        )
    # Also inspect existing grants, so a delegate cannot delete/edit a broader grant.
    existing = [
        g
        for g in read_authorization(target, manifest=manifest).grants
        if g.workspace_oid == workspace_oid and g.datasource_id == datasource_id
    ]
    assert_delegated_grants(
        delegations, (*existing, *(normalize_education_grant(g, manifest) for g in proposed))
    )


def assert_delegated_grants(delegations, grants):
    """Pure containment check; callers must verify manager identity and active delegations."""
    for grant in grants:

        def contains(parent):
            return (
                all(grant.keys().get(k) == v for k, v in parent.keys().items())
                and set(grant.capabilities) <= set(parent.capabilities)
                and (
                    parent.valid_from is None
                    or grant.valid_from is not None
                    and grant.valid_from >= parent.valid_from
                )
                and (
                    parent.valid_to is None
                    or grant.valid_to is not None
                    and grant.valid_to <= parent.valid_to
                )
                and (
                    parent.history_from is None
                    or grant.history_from is not None
                    and grant.history_from >= parent.history_from
                )
                and (
                    parent.history_to is None
                    or grant.history_to is not None
                    and grant.history_to <= parent.history_to
                )
            )

        if not any(contains(parent) for parent in delegations):
            raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "不能管理或授予委派范围以外的权限")


def replace_resource_grants(user, workspace_oid, datasource_id, expected_revision, grants):
    current = read_authorization(user)
    if expected_revision != current.revision:
        raise AuthorizationError("AUTH_CONTEXT_STALE", "权限已被修改，请刷新后重试")
    if any(g.workspace_oid != workspace_oid or g.datasource_id != datasource_id for g in grants):
        raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "只能修改当前空间和数据源的授权")
    remaining = tuple(
        g
        for g in current.grants
        if (g.workspace_oid, g.datasource_id) != (workspace_oid, datasource_id)
    )
    updated = EducationAuthorization(
        revision=current.revision + 1,
        grants=(*remaining, *grants),
        managed_resources=tuple(
            sorted(set(current.managed_resources) | {f"{workspace_oid}:{datasource_id}"})
        ),
    )
    variables = dict(getattr(user, "system_variables", None) or {})
    variables[AUTH_KEY] = updated.model_dump(mode="json")
    user.system_variables = variables
    return updated
