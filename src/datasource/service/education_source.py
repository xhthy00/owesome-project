"""The permission editor's single education resource: DATABASE_EDU_URL.

Only organization metadata is read here. There are no model-generated queries,
student scores, writes, or datasource credentials returned to the browser.
"""

from __future__ import annotations

from threading import Lock
from time import monotonic

from src.datasource.edu_connection import parse_edu_database_url
from src.datasource.service.education_authorization import (
    AUTH_KEY,
    EDUCATION_RESOURCE_ID,
    AuthorizationError,
    ConnectionIdentity,
    PermissionManifest,
    RelationPolicy,
    fingerprint,
    read_authorization,
)

_CACHE: dict[str, tuple[float, PermissionManifest]] = {}
_LOCK = Lock()
_TTL = 300
_LIMIT = 50_000


def source_settings(settings=None):
    if settings is None:
        from src.common.core.config import get_settings

        settings = get_settings()
    if not settings.database_edu_url.strip():
        raise AuthorizationError("AUTH_MAPPING_MISSING", "尚未配置学情库 DATABASE_EDU_URL")
    try:
        kind, config = parse_edu_database_url(settings.database_edu_url)
    except ValueError as exc:
        raise AuthorizationError("AUTH_MAPPING_MISSING", "学情库连接配置无效") from exc
    bindings = settings.datasemantic_bindings
    legacy_id = settings.datasemantic_datasource_id
    if (
        legacy_id is not None
        and settings.datasemantic_bundle_id
        and settings.datasemantic_manifest_hash
    ):
        from src.common.core.config import DataSemanticBinding

        bindings = {
            legacy_id: DataSemanticBinding(
                bundle_id=settings.datasemantic_bundle_id,
                manifest_hash=settings.datasemantic_manifest_hash,
            ),
            **bindings,
        }
    if len(bindings) != 1:
        raise AuthorizationError(
            "AUTH_MAPPING_MISSING", "学情库需要唯一的现有语义发布绑定，请核对 DATASEMANTIC_BINDINGS"
        )
    datasource_id, binding = next(iter(bindings.items()))
    return settings, kind, config, int(datasource_id), binding


def source_info(workspace_oid, *, settings=None):
    _, _, _, datasource_id, _ = source_settings(settings)
    return {
        "datasource_id": datasource_id,
        "workspace_oid": workspace_oid,
        "name": "学情业务库",
        "source_type": "DATABASE_EDU_URL",
        "city_id": city_identity(),
        "city_label": "全市",
        "supports_history": False,
    }


def city_identity():
    # Authorization belongs to the logical education resource, not its deployment.
    return EDUCATION_RESOURCE_ID


def has_environment_authorization(user, workspace_oid, datasource_id):
    variables = getattr(user, "system_variables", None)
    if not isinstance(variables, dict) or AUTH_KEY not in variables:
        return False
    doc = read_authorization(user)
    return f"{workspace_oid}:{datasource_id}" in doc.managed_resources or any(
        g.workspace_oid == workspace_oid and g.datasource_id == datasource_id for g in doc.grants
    )


def _schema(kind, config):
    from src.datasource.db.db import get_schema_info

    return get_schema_info(kind, {**config, "timeout": 5})


def _catalog(kind, config, policy):
    from src.datasource.db.db import execute_sql

    quote = "`" if kind == "mysql" else '"'
    fields = ["school_id", "district_id", "cohort_id", "class_id"]
    columns = [policy.scope_columns.get(key) for key in fields]
    expressions = [
        f"{quote}{col}{quote} AS {quote}{key}{quote}" if col else f"NULL AS {quote}{key}{quote}"
        for key, col in zip(fields, columns)
    ]
    # All identifiers come from the fixed, introspected table below, not request input.
    sql = (
        f"SELECT DISTINCT {', '.join(expressions)} FROM {quote}{policy.physical_table}{quote} "
        f"WHERE {quote}{policy.scope_columns['school_id']}{quote} IS NOT NULL "
        f"LIMIT {_LIMIT + 1}"
    )
    ok, _, data = execute_sql(
        kind, {**config, "read_only": True, "timeout": 5, "query_timeout_seconds": 15}, sql
    )
    if not ok or not isinstance(data, dict):
        raise AuthorizationError(
            "AUTH_MAPPING_MISSING", "无法读取学情库组织目录，请检查连接及表权限"
        )
    if len(data.get("rows", [])) > _LIMIT:
        raise AuthorizationError(
            "AUTH_MAPPING_MISSING", "组织目录超过加载上限，请联系管理员检查数据"
        )
    names = data.get("columns", [])
    rows = []
    for raw in data.get("rows", []):
        row = raw if isinstance(raw, dict) else dict(zip(names, raw))
        result = {"city_id": policy.implicit_scope["city_id"], "city_name": "全市"}
        for key in fields:
            value = row.get(key)
            if value is not None and str(value).strip():
                result[key] = str(value).strip()
        if result.get("school_id"):
            rows.append(result)
    if not rows:
        raise AuthorizationError("AUTH_MAPPING_MISSING", "学情库尚无可配置的学校、届次和班级目录")
    return tuple(sorted(rows, key=lambda row: tuple(row.get(key, "") for key in fields)))


def overview_policy(schema, city_id):
    """Verified code-owned mapping for the existing overview model (no guessing)."""
    table = next((t for t in schema if t["name"] == "tb_score_overview"), None)
    if table is None:
        raise AuthorizationError("AUTH_MAPPING_MISSING", "学情库缺少 tb_score_overview 成绩总览表")
    names = {f["name"] for f in table["fields"]}
    if not {"xx", "dq", "jc", "bj", "exam_batch_id"} <= names:
        raise AuthorizationError(
            "AUTH_MAPPING_MISSING", "学情总览表缺少学校、区县、届次、班级或考试标识字段"
        )
    scope = {"school_id": "xx", "district_id": "dq", "cohort_id": "jc", "class_id": "bj"}
    if "anon_stu_id" in names:
        scope["student_id"] = "anon_stu_id"
    scores = [
        col
        for col in (
            "zf6m",
            "zf4m",
            "zf3m",
            "yw",
            "sx",
            "yy",
            "wl",
            "ls",
            "hx",
            "sw",
            "zz",
            "dl",
            "hxzh",
            "swzh",
            "zzzh",
            "dlzh",
        )
        if col in names
    ]
    return RelationPolicy(
        physical_table="tb_score_overview",
        columns={name: name for name in sorted(names)},
        scope_columns=scope,
        implicit_scope={"city_id": city_id},
        exam_column="exam_batch_id",
        subject_column=None,
        sample_column="zf6m" if "zf6m" in names else None,
        metrics={f"mean_{col}": f"AVG({col})" for col in scores},
        metric_samples={f"mean_{col}": col for col in scores},
        forbidden_columns=tuple(col for col in ("xm", "sfzh", "ksh") if col in names),
    )


def environment_manifest(workspace_oid, datasource_id=None, *, refresh=False, settings=None):
    settings, kind, config, configured_id, binding = source_settings(settings)
    if datasource_id is not None and int(datasource_id) != configured_id:
        raise AuthorizationError(
            "AUTH_SCOPE_OUTSIDE_GRANT", "分级权限仅适用于 DATABASE_EDU_URL 配置的学情库"
        )
    cache_key = fingerprint(
        {
            "url": settings.database_edu_url,
            "binding": binding.model_dump(),
            "workspace": workspace_oid,
        }
    )
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if not refresh and cached is not None and monotonic() - cached[0] < _TTL:
            return cached[1]
        schema = _schema(kind, config)
        policy = overview_policy(schema, city_identity())
        catalog = _catalog(kind, config, policy)
        manifest = PermissionManifest(
            workspace_oid=workspace_oid,
            datasource_id=configured_id,
            bundle_id=binding.bundle_id,
            manifest_hash=binding.manifest_hash,
            dialect="mysql" if kind == "mysql" else "postgres",
            physical_schema=config["database"] if kind == "mysql" else "public",
            connection=ConnectionIdentity(
                **{k: config[k] for k in ("host", "port", "database", "username")}
            ),
            relations={"tb_score_overview": policy},
            catalog=catalog,
        )
        if len(_CACHE) >= 16:
            _CACHE.clear()
        _CACHE[cache_key] = (monotonic(), manifest)
        return manifest


def permission_manifest_for_user(user, workspace_oid, datasource_id, *, path=None, settings=None):
    from src.datasource.service.education_authorization import manifest_for

    configured = manifest_for(datasource_id, path)
    if configured is not None:
        return configured
    if has_environment_authorization(user, workspace_oid, datasource_id):
        return environment_manifest(workspace_oid, datasource_id, settings=settings)
    return None
