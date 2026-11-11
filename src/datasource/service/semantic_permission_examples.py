"""Canonical SQL shapes for capabilities and ordinary own-scope reads.

Capabilities govern reference-pool comparisons. Plain reads inside the user's
granted scope (class student scores, in-class ranking, distributions) are
allowed independently and must not be described as an unavailable capability.

The prover accepts a narrow AST shape. Prose descriptions alone are not enough
for a model to hit that shape, so the host renders one provable example per
granted capability. Examples are presentation only: the model still authors the
SQL, and the prover still decides whether it is acceptable.

Placeholders are deliberately obvious (``<exam_batch_id>``); the model replaces
them with confirmed values. Tests fill the placeholders and prove the result, so
an example can never drift away from the validator.
"""

from __future__ import annotations

import logging

from sqlglot import exp, parse_one

from src.datasource.service.semantic_permission_contract import build_query_contract

logger = logging.getLogger(__name__)

__all__ = ["canonical_examples"]

_EXAM_PLACEHOLDER = "<exam_batch_id>"
_SUBJECT_PLACEHOLDER = "<subject>"
_COHORT_PLACEHOLDER = "<cohort_id>"


def _literal(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _metric(policy, rules):
    metric_id = "mean_sx" if "mean_sx" in rules.aggregate_expressions else next(
        iter(rules.aggregate_expressions)
    )
    formula = rules.aggregate_expressions[metric_id]
    parsed = parse_one(formula)
    column = next((c.name for c in parsed.find_all(exp.Column)), None)
    sample = rules.sample_columns.get(metric_id) or policy.sample_column
    if not column or not sample:
        raise ValueError("指标缺少可渲染的字段或样本列")
    return metric_id, formula, column, sample


def _where(policy, values, keys) -> str:
    clauses = []
    for key in keys:
        column = policy.scope_columns.get(key)
        value = values.get(key)
        if column and value is not None:
            # Cohort keys are commonly numeric; keep the example free of a
            # text-to-number cast that differs across dialects.
            rendered = (
                str(value)
                if key == "cohort_id" and str(value).isdigit()
                else _literal(value)
            )
            clauses.append(f"{column} = {rendered}")
    if policy.exam_column:
        clauses.append(f"{policy.exam_column} = {_EXAM_PLACEHOLDER}")
    for column, value in policy.population_filters.items():
        clauses.append(f"{column} = {_literal(value)}")
    return " AND ".join(clauses)


def _require_cohort(pool_where, policy, grant) -> str:
    column = policy.scope_columns.get("cohort_id")
    if column and "cohort_id" not in grant.keys():
        return pool_where + f" AND {column} = {_COHORT_PLACEHOLDER}"
    return pool_where


def _own_scope(relation, policy, grant) -> str | None:
    """A plain class/student detail query; no reference pool involved."""
    student = policy.scope_columns.get("student_id")
    score = "sx" if "sx" in policy.columns else policy.sample_column
    if not score or score not in policy.columns:
        return None
    # Anonymized student keys may be publishable; identity keys are forbidden.
    select = (
        f"{student}, {score}"
        if student and student in policy.columns and student not in policy.forbidden_columns
        else score
    )
    keys = [key for key in policy.scope_columns if key in grant.keys()]
    where = _where(policy, grant.keys(), keys)
    return f"""SELECT {select}
FROM {relation}
WHERE {where}
ORDER BY {score} DESC
LIMIT 10"""


def _baseline(manifest, relation, policy, rules, grant, capability) -> str:
    metric_id, formula, column, sample = _metric(policy, rules)
    precision = manifest.precision
    rule = rules.capabilities[capability]
    own_keys = [key for key in policy.scope_columns if key in grant.keys()]
    own_where = _where(policy, grant.keys(), own_keys)
    pool_where = _where(policy, grant.keys(), rule.pool_scope_keys)
    pool_where = _require_cohort(pool_where, policy, grant)
    unit_column = policy.scope_columns.get(rule.reference_unit_key)
    if not unit_column:
        raise ValueError("能力缺少参评单位列")
    baseline = f"{capability}"
    return f"""WITH own_scope AS (
    SELECT {formula} AS {metric_id}
    FROM {relation}
    WHERE {own_where}
    HAVING COUNT({sample}) >= {manifest.minimum_sample_size}
), {baseline} AS (
    SELECT ROUND({formula}, {precision}) AS {metric_id}
    FROM {relation}
    WHERE {pool_where}
    HAVING COUNT({sample}) >= {manifest.minimum_sample_size}
       AND COUNT(DISTINCT {unit_column}) >= {manifest.minimum_reference_units}
)
SELECT ROUND(o.{metric_id}, {precision}) AS own_mean,
       g.{metric_id} AS reference_mean,
       ROUND(o.{metric_id} - g.{metric_id}, {precision}) AS gap
FROM own_scope o JOIN {baseline} g ON g.{metric_id} IS NOT NULL"""


def _self_rank(manifest, relation, policy, rules, grant, capability) -> str:
    metric_id, formula, column, sample = _metric(policy, rules)
    precision = manifest.precision
    rule = rules.capabilities[capability]
    target_key = rule.reference_unit_key
    target_column = policy.scope_columns.get(target_key)
    target = grant.keys().get(target_key)
    if not target_column or target is None:
        raise ValueError("排名能力缺少自身单位值")
    pool_keys = list(rule.pool_scope_keys)
    pool_where = _where(policy, grant.keys(), pool_keys)
    pool_where = _require_cohort(pool_where, policy, grant)
    if policy.subject_column:
        pool_where += f" AND {policy.subject_column} = {_SUBJECT_PLACEHOLDER}"
    return f"""WITH units AS (
    SELECT {target_column} AS target,
           {formula} AS {metric_id}
    FROM {relation}
    WHERE {pool_where}
    GROUP BY {target_column}
    HAVING COUNT({sample}) >= {manifest.minimum_sample_size}
), ranked AS (
    SELECT target, {metric_id},
           RANK() OVER (ORDER BY {metric_id} DESC) AS city_rank,
           COUNT(*) OVER () AS reference_units
    FROM units
)
SELECT target, ROUND({metric_id}, {precision}) AS own_mean, city_rank, reference_units
FROM ranked
WHERE target = {_literal(target)} AND reference_units >= {manifest.minimum_reference_units}"""


def canonical_examples(manifest, grants) -> dict[str, str]:
    """Return one provable SQL example per granted reference capability."""
    contract = build_query_contract(manifest)
    examples: dict[str, str] = {}
    for grant in grants:
        if "class_id" not in grant.keys() and "student_id" not in grant.keys():
            continue
        for name, policy in manifest.relations.items():
            if policy.public:
                continue
            try:
                example = _own_scope(name, policy, grant)
            except (ValueError, KeyError):
                example = None
            if example:
                examples["own_scope"] = example
                break
        break
    for grant in grants:
        for capability in grant.capabilities:
            if capability in examples:
                continue
            relation = next(
                (
                    name
                    for name, policy in manifest.relations.items()
                    if not policy.public
                    and capability in contract.relations[name].capabilities
                ),
                None,
            )
            if relation is None:
                continue
            policy = manifest.relations[relation]
            rules = contract.relations[relation]
            try:
                if rules.capabilities[capability].release == "self_rank":
                    examples[capability] = _self_rank(manifest, relation, policy, rules, grant, capability)
                else:
                    examples[capability] = _baseline(manifest, relation, policy, rules, grant, capability)
            except (ValueError, KeyError, StopIteration) as exc:
                logger.warning("skip canonical example for %s: %s", capability, exc)
    return examples
