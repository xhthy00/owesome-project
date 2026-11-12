"""v2 分级权限在分析工具（宿主生成 SQL 的报告/看板/提醒）上的数据范围适配。

分析工具的 SQL 由宿主模板生成，不是模型 SQL，因此宿主必须自己保证：

- 每个实际读取成绩事实的 SELECT 都被 v2 grants 收口（学校/届次/班级/学生）；
- 全市/年级/区县对照只通过对应 capability 放行，未开通时降级或拒绝；
- 请求里点名的学校、区县、班级必须落在当前 grants 内；
- 报告历史按同一授权指纹重新鉴权。

没有 ``edu_auth_v2`` 文档的账号返回 ``None``，调用方继续走旧的 ``EduScope`` 路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from src.datasource.service.education_authorization import (
    AUTH_KEY,
    AuthorizationError,
    EducationGrant,
    PermissionManifest,
    context_for,
)

#: 总览表的权限列（与 environment_manifest.overview_policy 保持同源，可在 manifest 中覆盖）。
_OVERVIEW_TABLE = "tb_score_overview"
_DEFAULT_SCOPE_COLUMNS = {
    "district_id": "dq",
    "school_id": "xx",
    "cohort_id": "jc",
    "class_id": "bj",
    "student_id": "anon_stu_id",
}
_OVERVIEW_NAMES = frozenset({_OVERVIEW_TABLE, "score_overview"})
_SCORE_NAMES = frozenset({"tb_score", "score"})
_DETAIL_NAMES = frozenset({"tb_score_detail", "score_detail"})

_CODE_PREFIX_RE = re.compile(r"^[A-Za-z]\d{2}")
_SPACE_RE = re.compile(r"\s+")


class ReportScopeError(AuthorizationError):
    """分析工具范围/能力不满足；``detail`` 可直接返回给前端。"""


def _norm_text(value: Any) -> str:
    text = _SPACE_RE.sub("", str(value or "").strip().lower())
    text = text.replace("（", "(").replace("）", ")")
    return text


def _school_key(value: Any) -> str:
    """校名归一：去空白、去 ``A01`` 校码前缀，便于 grants 与库内展示名对齐。"""
    text = _norm_text(value)
    return _CODE_PREFIX_RE.sub("", text)


def _class_key(value: Any) -> str:
    text = _norm_text(value)
    return text.replace("(", "").replace(")", "")


def _aliases_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    # 只在足够长的名称上做包含匹配，避免「一中」误配到其他学校。
    return len(shorter) >= 4 and shorter in longer


@dataclass(frozen=True)
class ReportScope:
    """一名 v2 账号在固定学情库上的分析工具数据范围。"""

    user_id: int
    workspace_oid: int
    datasource_id: int
    manifest: PermissionManifest
    grants: tuple[EducationGrant, ...]
    levels: frozenset[str]
    schools: tuple[str, ...]
    districts: tuple[str, ...]
    cohorts: tuple[str, ...]
    classes: tuple[str, ...]
    students: tuple[str, ...]
    capabilities: frozenset[str]
    fingerprint: str

    # ---- 范围判定 ---------------------------------------------------------
    @property
    def citywide(self) -> bool:
        return "city" in self.levels

    @property
    def needs_grade_reference(self) -> bool:
        """只有班级/学生级授权时，同校年级对照必须依赖 grade_baseline。"""
        return bool(self.levels & {"class", "student"}) and not self.levels & {
            "city",
            "district",
            "school",
            "grade",
        }

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def assert_active(self, now: datetime | None = None) -> None:
        """执行前后复核授权有效期；到期/停用立即放弃结果。"""
        moment = now or datetime.now(timezone.utc)
        if not all(grant.active(moment) for grant in self.grants):
            raise ReportScopeError("AUTH_REVOKED", "教育授权已失效，请刷新后重试")

    def legacy_role(self) -> str:
        """映射到旧模板/旧校验使用的角色语义（只影响展示与既有入参校验）。"""
        if self.citywide or "district" in self.levels:
            return "bureau_admin"
        if self.levels & {"school", "grade"}:
            return "school_admin"
        if self.levels & {"class", "student"}:
            return "teacher"
        return ""

    def allowed_district_ids(self) -> tuple[str, ...]:
        if self.citywide:
            return tuple(
                sorted(
                    {
                        str(row.get("district_id"))
                        for row in self.manifest.catalog
                        if row.get("district_id")
                    }
                )
            )
        if self.districts:
            return self.districts
        schools = {_school_key(s) for s in self.schools}
        return tuple(
            sorted(
                {
                    str(row.get("district_id"))
                    for row in self.manifest.catalog
                    if row.get("district_id")
                    and _school_key(row.get("school_id")) in schools
                }
            )
        )

    def allowed_class_ids(self) -> tuple[str, ...]:
        """异常提醒等无 cohort 字段的表：用 catalog 把年级/学生授权展开成班级集合。"""
        if self.classes:
            return self.classes
        if self.levels & {"city", "district", "school"} and not self.levels & {
            "grade",
            "student",
        }:
            return ()
        schools = {_school_key(s) for s in self.schools}
        cohorts = {_norm_text(c) for c in self.cohorts}
        out: set[str] = set()
        for row in self.manifest.catalog:
            if _school_key(row.get("school_id")) not in schools:
                continue
            if cohorts and _norm_text(row.get("cohort_id")) not in cohorts:
                continue
            cls = str(row.get("class_id") or "").strip()
            if cls:
                out.add(cls)
        return tuple(sorted(out))

    def allows_city_reference(self) -> bool:
        return self.citywide or self.has("city_baseline")

    def allows_grade_reference(self) -> bool:
        return not self.needs_grade_reference or self.has("grade_baseline")

    def allows_district_reference(self) -> bool:
        return self.citywide or self.has("district_self_rank")

    def allows_city_position(self) -> bool:
        """全市个人/班级名次不在 capability 目录内，只对市级授权开放。"""
        return self.citywide

    def can_view_anomaly_alerts(self) -> bool:
        return bool(self.levels & {"school", "grade", "class"})

    # ---- 名称对齐 ---------------------------------------------------------
    def matches_school(self, value: Any) -> bool:
        key = _school_key(value)
        if not key:
            return False
        return any(_aliases_match(key, _school_key(s)) for s in self.schools)

    def matches_district(self, value: Any) -> bool:
        key = _norm_text(value)
        return bool(key) and any(_aliases_match(key, _norm_text(d)) for d in self.districts)

    def matches_class(self, value: Any) -> bool:
        key = _class_key(value)
        if not key:
            return False
        if not self.classes:
            return True
        return any(_class_key(c) == key for c in self.classes)

    def scope_label(self) -> str:
        if self.citywide:
            return "全市"
        if self.districts and not self.schools:
            return self.districts[0]
        if self.schools:
            label = self.schools[0]
            return _CODE_PREFIX_RE.sub("", label) or label
        return "本范围"

    def summary(self) -> dict[str, Any]:
        """``/me`` 用摘要：前端据此选择数据源、预填学校/班级并收口报告类型。"""
        single_school = self.schools[0] if len(self.schools) == 1 else ""
        return {
            "edu_role": "v2",
            "datasource_id": self.datasource_id,
            "levels": sorted(self.levels),
            "capabilities": sorted(self.capabilities),
            "schools": list(self.schools),
            "districts": list(self.districts),
            "cohorts": list(self.cohorts),
            "class_names": list(self.classes),
            "students": list(self.students),
            "scope_label": self.scope_label(),
            "citywide": self.citywide,
            "school_id": single_school or None,
            "school_name": (
                _CODE_PREFIX_RE.sub("", single_school) if single_school else None
            ),
        }

    # ---- 请求点名校验 -----------------------------------------------------
    def assert_in_scope(
        self,
        *,
        school_name: str = "",
        district: str = "",
        class_name: str = "",
    ) -> None:
        if self.citywide:
            return
        if district and not self.matches_district(district):
            raise ReportScopeError("AUTH_SCOPE_OUTSIDE_GRANT", "请求的区县不在当前授权范围内")
        if school_name and not self.matches_school(school_name):
            raise ReportScopeError("AUTH_SCOPE_OUTSIDE_GRANT", "请求的学校不在当前授权范围内")
        if class_name and self.classes and not self.matches_class(class_name):
            raise ReportScopeError("AUTH_SCOPE_OUTSIDE_GRANT", "请求的班级不在当前授权范围内")

    # ---- SQL 范围下推 -----------------------------------------------------
    def _columns(self) -> dict[str, str]:
        for policy in self.manifest.relations.values():
            if policy.physical_table.strip().lower() == _OVERVIEW_TABLE:
                return {**_DEFAULT_SCOPE_COLUMNS, **policy.scope_columns}
        raise ReportScopeError("AUTH_MAPPING_MISSING", "权限发布映射缺少学情总览表范围字段")

    def _column(self, logical: str, alias: str, dialect: str) -> str:
        quote = "`" if dialect == "mysql" else '"'
        name = self._columns()[logical]
        if not alias:
            return f"{quote}{name}{quote}"
        return f"{alias.rstrip('.')}.{quote}{name}{quote}"

    def _grant_clause(
        self,
        grant: EducationGrant,
        alias: str,
        dialect: str,
        *,
        include_class: bool = True,
    ) -> str | None:
        if grant.level == "city":
            return None
        if grant.level == "district":
            return f"{self._column('district_id', alias, dialect)} = {_sql_literal(grant.district_id)}"
        if grant.level == "school":
            return f"{self._column('school_id', alias, dialect)} = {_sql_literal(grant.school_id)}"
        parts = [
            f"{self._column('school_id', alias, dialect)} = {_sql_literal(grant.school_id)}"
        ]
        if grant.cohort_id:
            parts.append(
                f"{self._column('cohort_id', alias, dialect)} = {_sql_literal(grant.cohort_id)}"
            )
        if grant.level == "class" and include_class and grant.class_id:
            parts.append(
                f"{self._column('class_id', alias, dialect)} = {_sql_literal(grant.class_id)}"
            )
        if grant.level == "student" and include_class and grant.student_id:
            parts.append(
                f"{self._column('student_id', alias, dialect)} = {_sql_literal(grant.student_id)}"
            )
        return "(" + " AND ".join(parts) + ")"

    def overview_predicate(
        self,
        alias: str = "",
        dialect: str = "postgres",
        *,
        include_class: bool = True,
    ) -> str | None:
        """总览表范围谓词；``None`` 表示全市、无需收口。"""
        clauses: list[str] = []
        for grant in self.grants:
            clause = self._grant_clause(
                grant, alias, dialect, include_class=include_class
            )
            if clause is None:
                return None
            clauses.append(clause)
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return "(" + " OR ".join(clauses) + ")"

    def _student_scope_clause(
        self,
        alias: str,
        dialect: str,
        *,
        include_class: bool,
        district_only: bool = False,
    ) -> str | None:
        overview = (
            self.district_predicate("", dialect)
            if district_only
            else self.overview_predicate("", dialect, include_class=include_class)
        )
        if overview is None:
            return None
        quote = "`" if dialect == "mysql" else '"'
        student = (
            f"{alias.rstrip('.')}.{quote}student_id{quote}"
            if alias
            else f"{quote}student_id{quote}"
        )
        inner_student = self._column("student_id", "", dialect)
        return (
            f"{student} IN (SELECT {inner_student} "
            f"FROM {_OVERVIEW_TABLE} WHERE {overview})"
        )

    def table_predicate(
        self, table: str, alias: str, dialect: str, *, include_class: bool = True
    ) -> str | None:
        name = (table or "").strip().lower()
        if name in _OVERVIEW_NAMES:
            return self.overview_predicate(alias, dialect, include_class=include_class)
        if name in _SCORE_NAMES or name in _DETAIL_NAMES:
            return self._student_scope_clause(alias, dialect, include_class=include_class)
        return None

    def apply_sql(
        self, sql: str, db_type: str, *, reference: str | None = None
    ) -> str:
        """把范围谓词并入每个直接读取成绩事实表的 SELECT。

        ``reference`` 只由宿主模板显式标注：
        ``city``/``grade``/``district`` 分别要求 ``city_baseline`` /
        ``grade_baseline`` / ``district_self_rank``，未开通时抛
        ``ReportScopeError``，绝不放宽成更大范围。
        """
        if not sql or not sql.strip():
            return sql
        if reference not in (None, "own", "city", "grade", "district"):
            raise ReportScopeError("AUTH_QUERY_SHAPE_UNSUPPORTED", "未知的报告对照范围")
        if reference == "city":
            if not self.allows_city_reference():
                raise ReportScopeError(
                    "AUTH_CAPABILITY_MISSING", "当前账号未开通全市基准对比能力"
                )
            return sql
        if reference == "grade" and not self.allows_grade_reference():
            raise ReportScopeError(
                "AUTH_CAPABILITY_MISSING", "当前账号未开通年级均分基准能力"
            )
        if reference == "district" and not self.allows_district_reference():
            raise ReportScopeError(
                "AUTH_CAPABILITY_MISSING", "当前账号未开通区县名次对比能力"
            )
        if reference == "district" and not any(
            grant.level == "district" for grant in self.grants
        ):
            raise ReportScopeError(
                "AUTH_CAPABILITY_MISSING", "区县名次对比仅适用于区县级授权"
            )

        dialect = "mysql" if db_type == "mysql" else "postgres"
        include_class = reference != "grade"
        from sqlglot import exp, parse_one

        try:
            tree = parse_one(sql, dialect=dialect)
        except Exception as exc:  # noqa: BLE001 - 宿主模板异常应显式失败
            raise ReportScopeError("AUTH_QUERY_SHAPE_UNSUPPORTED", "报告 SQL 无法解析") from exc

        changed = False
        for select in list(tree.find_all(exp.Select)):
            clauses: list[str] = []
            for table in _direct_tables(select):
                name = (table.name or "").strip().lower()
                clause = self.table_predicate(
                    name,
                    _table_alias(table),
                    dialect,
                    include_class=include_class,
                )
                if reference == "district":
                    if name in _OVERVIEW_NAMES:
                        clause = self.district_predicate("", dialect)
                    elif name in _SCORE_NAMES or name in _DETAIL_NAMES:
                        clause = self._student_scope_clause(
                            _table_alias(table), dialect, include_class=True, district_only=True
                        )
                if clause:
                    clauses.append(clause)
            if not clauses:
                continue
            replacement = select
            for clause in clauses:
                replacement = replacement.where(clause, append=True)
            # ``Select.where`` returns a copy; copy the resulting WHERE back onto
            # the node that is actually attached to the tree.
            select.args["where"] = replacement.args.get("where")
            changed = True
        return tree.sql(dialect=dialect) if changed else sql

    def district_predicate(self, alias: str, dialect: str) -> str | None:
        clauses = [
            f"{self._column('district_id', alias, dialect)} = {_sql_literal(grant.district_id)}"
            for grant in self.grants
            if grant.level == "district"
        ]
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"

    # ---- 结果行收敛 -------------------------------------------------------
    def filter_indicator_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """``tb_score_indicator`` 只有学校/区县维度：按授权学校或区县收敛。"""
        if self.citywide:
            return list(rows)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            school_values = [
                str(row.get("school_name") or "").strip(),
                str(row.get("school_id") or "").strip(),
            ]
            district = str(row.get("district") or "").strip()
            if self.schools and any(
                self.matches_school(value) for value in school_values if value
            ):
                out.append(row)
                continue
            if self.districts and district and self.matches_district(district):
                out.append(row)
        return out

    def filter_catalog_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 grants 收敛组织目录（供 meta options / 报告下拉使用）。"""
        if self.citywide:
            return list(rows)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            school = row.get("school_id") or row.get("xx") or ""
            if school and self.schools and not self.matches_school(school):
                continue
            cohort = row.get("cohort_id") or row.get("jc") or ""
            if cohort and self.cohorts and _norm_text(cohort) not in {
                _norm_text(c) for c in self.cohorts
            }:
                continue
            cls = row.get("class_id") or row.get("bj") or ""
            if cls and self.classes and not self.matches_class(cls):
                continue
            out.append(row)
        return out


def _sql_literal(value: Any) -> str:
    return "'" + str(value if value is not None else "").replace("'", "''") + "'"


def _table_alias(table: Any) -> str:
    from sqlglot import exp

    alias = table.args.get("alias")
    if alias is None:
        return ""
    inner = alias.this if hasattr(alias, "this") else alias
    return inner.name if isinstance(inner, exp.Identifier) else str(inner)


def _direct_tables(select: Any) -> list[Any]:
    """只返回直接属于该 SELECT 的表，跳过派生表/子查询内部的表。"""
    from sqlglot import exp

    out: list[Any] = []
    for table in select.find_all(exp.Table):
        parent = table.parent
        while parent is not None and not isinstance(parent, exp.Select):
            parent = parent.parent
        if parent is select:
            out.append(table)
    return out


def load_report_scope(
    session,
    user,
    workspace_oid: int,
    datasource_id: int,
) -> ReportScope | None:
    """加载当前账号的分析工具 v2 范围。

    - 没有 ``edu_auth_v2`` 文档：返回 ``None``，继续旧权限路径；
    - 有文档但不在固定学情库/无有效授权：抛 ``ReportScopeError``（失败关闭）。
    """
    variables = getattr(user, "system_variables", None) or {}
    if not isinstance(variables, dict) or AUTH_KEY not in variables:
        return None
    from src.datasource.service.education_source import permission_manifest_for_user

    try:
        policy = permission_manifest_for_user(user, workspace_oid, datasource_id)
        if policy is None:
            raise ReportScopeError(
                "AUTH_SCOPE_OUTSIDE_GRANT", "分级权限仅适用于固定学情库，请切换到学情数据源"
            )
        context = context_for(user, workspace_oid, datasource_id, policy, session=session)
    except ReportScopeError:
        raise
    except AuthorizationError as exc:
        detail = getattr(exc, "detail", None) or {}
        raise ReportScopeError(
            detail.get("code", "AUTH_SCOPE_OUTSIDE_GRANT"),
            detail.get("user_message") or str(exc),
        ) from exc
    grants = context.grants
    levels = frozenset(grant.level for grant in grants)
    capabilities = frozenset(cap for grant in grants for cap in grant.capabilities)

    def _collect(field: str) -> tuple[str, ...]:
        return tuple(
            sorted({str(getattr(grant, field)) for grant in grants if getattr(grant, field)})
        )

    return ReportScope(
        user_id=int(getattr(user, "id", 0) or 0),
        workspace_oid=int(workspace_oid),
        datasource_id=int(datasource_id),
        manifest=context.manifest,
        grants=grants,
        levels=levels,
        schools=_collect("school_id"),
        districts=_collect("district_id"),
        cohorts=_collect("cohort_id"),
        classes=_collect("class_id"),
        students=_collect("student_id"),
        capabilities=capabilities,
        fingerprint=context.fingerprint,
    )


__all__ = [
    "ReportScope",
    "ReportScopeError",
    "load_report_scope",
]
