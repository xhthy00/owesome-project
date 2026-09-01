"""异常提醒：检测落库、按教育权限列表、确认处理。"""

from __future__ import annotations

import logging
from copy import copy
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, col, select

from datasource.service.edu_permission import EduScope
from src.agent.education.config import (
    ANOMALY_CRITICAL,
    ANOMALY_IMBALANCED,
    ANOMALY_REGRESSION,
)
from src.agent.education.config_store import get_config
from src.agent.education.models_alert import EduAnomalyAlert
from src.agent.education.stats import identify_at_risk_students

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
SOURCE_SCORE_IMPORT = "score_import"
SOURCE_TIER_ALERT = "tier_alert_report"

# 列表粒度：一份报告一条；payload 内嵌学生明细
ANOMALY_REPORT = "tier_alert"

_TYPE_TITLE = {
    ANOMALY_CRITICAL: "临界生",
    ANOMALY_REGRESSION: "大幅退步",
    ANOMALY_IMBALANCED: "偏科",
    ANOMALY_REPORT: "异常报告",
}

_LEGACY_STUDENT_TYPES = frozenset({ANOMALY_CRITICAL, ANOMALY_REGRESSION, ANOMALY_IMBALANCED})

# 库内时间为 UTC naive；接口输出转东八区便于校内查看
_TZ_CN = timezone(timedelta(hours=8))


def _format_alert_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_CN).strftime("%Y-%m-%d %H:%M:%S")

# 教育局 / 学生不看校内异常待办
_BLOCKED_ROLES = frozenset({"bureau_admin", "student", ""})


def can_access_anomaly_alerts(scope: EduScope) -> bool:
    """校长、老师可访问；教育局与学生不可见。"""
    role = (scope.edu_role or "").strip()
    if role in _BLOCKED_ROLES:
        return False
    return role in {"school_admin", "teacher"}


def build_dedupe_key(
    *,
    workspace_oid: int,
    datasource_id: int,
    school_id: str,
    exam_id: str,
    class_name: str,
    subject_name: str = "",
    source: str = "",
) -> str:
    """报告级去重：同校同场同班同科同源只保留一条。"""
    return "|".join(
        [
            str(workspace_oid),
            str(datasource_id),
            str(school_id or "").strip(),
            str(exam_id or "").strip(),
            str(class_name or "").strip(),
            str(subject_name or "").strip(),
            str(source or "").strip(),
            ANOMALY_REPORT,
        ]
    )


def alert_to_dict(row: EduAnomalyAlert) -> dict[str, Any]:
    payload = row.payload_json or {}
    counts = payload.get("counts") if isinstance(payload, dict) else None
    if not isinstance(counts, dict):
        counts = {
            "critical": len((payload or {}).get(ANOMALY_CRITICAL) or []) if isinstance(payload, dict) else 0,
            "regression": len((payload or {}).get(ANOMALY_REGRESSION) or []) if isinstance(payload, dict) else 0,
            "imbalanced": len((payload or {}).get(ANOMALY_IMBALANCED) or []) if isinstance(payload, dict) else 0,
        }
    return {
        "id": row.id,
        "workspace_oid": row.workspace_oid,
        "datasource_id": row.datasource_id,
        "school_id": row.school_id,
        "class_name": row.class_name,
        "student_id": row.student_id,
        "exam_id": row.exam_id,
        "exam_name": row.exam_name,
        "subject_name": row.subject_name,
        "anomaly_type": row.anomaly_type,
        "anomaly_type_label": _TYPE_TITLE.get(row.anomaly_type, row.anomaly_type),
        "title": row.title,
        "reason": row.reason,
        "payload": payload,
        "counts": {
            "critical": int(counts.get("critical") or 0),
            "regression": int(counts.get("regression") or 0),
            "imbalanced": int(counts.get("imbalanced") or 0),
        },
        "source": row.source,
        "status": row.status,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": _format_alert_dt(row.confirmed_at),
        "confirm_note": row.confirm_note or "",
        "create_time": _format_alert_dt(row.create_time),
        "update_time": _format_alert_dt(row.update_time),
    }


def _scope_filter_clause(scope: EduScope):
    """返回 (school_id 条件可用?, class 列表)。调用方负责拼 query。"""
    return (scope.school_id or "").strip(), list(scope.class_names or [])


def _consolidate_legacy_to_reports(
    session: Session,
    *,
    workspace_oid: int,
    school_id: str,
) -> int:
    """把旧版每人一条合并为报告级（同校同场同班同源），便于已落库数据立刻可用。"""
    if not school_id:
        return 0
    legacy = list(
        session.exec(
            select(EduAnomalyAlert).where(
                EduAnomalyAlert.workspace_oid == int(workspace_oid),
                EduAnomalyAlert.school_id == school_id,
                col(EduAnomalyAlert.anomaly_type).in_(list(_LEGACY_STUDENT_TYPES)),
            )
        ).all()
    )
    if not legacy:
        return 0

    groups: dict[tuple[Any, ...], list[EduAnomalyAlert]] = {}
    for row in legacy:
        key = (
            int(row.datasource_id),
            str(row.exam_id or ""),
            str(row.class_name or ""),
            str(row.source or SOURCE_SCORE_IMPORT),
            str(row.exam_name or ""),
        )
        groups.setdefault(key, []).append(row)

    events: list[dict[str, Any]] = []
    for (datasource_id, exam_id, class_name, source, exam_name), rows in groups.items():
        bucket = _empty_at_risk()
        for row in rows:
            item = dict(row.payload_json or {})
            if not item.get("student_id") and row.student_id:
                item["student_id"] = row.student_id
            if not item.get("name") and row.student_id:
                item["name"] = row.student_id
            if not item.get("reason") and row.reason:
                item["reason"] = row.reason
            if not item.get("subject") and row.subject_name and row.anomaly_type != ANOMALY_IMBALANCED:
                item["subject"] = row.subject_name
            if row.anomaly_type in bucket:
                bucket[row.anomaly_type].append(item)
        ev = _build_report_event(
            bucket,
            workspace_oid=workspace_oid,
            datasource_id=datasource_id,
            school_id=school_id,
            exam_id=exam_id,
            exam_name=exam_name,
            class_name=class_name,
            subject_name="",
            source=source,
        )
        if ev:
            # 合并时：若已有报告且已确认，upsert 会保留 confirmed；否则新建 pending
            events.append(ev)

    for row in legacy:
        session.delete(row)
    session.commit()
    if events:
        upsert_alert_events(session, events)
    return len(events)


def list_alerts(
    session: Session,
    scope: EduScope,
    *,
    workspace_oid: int,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[EduAnomalyAlert], int]:
    if not can_access_anomaly_alerts(scope):
        return [], 0

    school_id, class_names = _scope_filter_clause(scope)
    if school_id:
        try:
            _consolidate_legacy_to_reports(
                session, workspace_oid=workspace_oid, school_id=school_id
            )
        except Exception:  # noqa: BLE001
            logger.exception("consolidate legacy anomaly alerts failed school=%s", school_id)

    filters = [
        EduAnomalyAlert.workspace_oid == int(workspace_oid),
        EduAnomalyAlert.anomaly_type == ANOMALY_REPORT,
    ]
    if school_id:
        filters.append(EduAnomalyAlert.school_id == school_id)
    if scope.edu_role == "teacher" and class_names:
        filters.append(col(EduAnomalyAlert.class_name).in_(class_names))
    if status in {STATUS_PENDING, STATUS_CONFIRMED}:
        filters.append(EduAnomalyAlert.status == status)

    total = int(session.exec(select(func.count()).select_from(EduAnomalyAlert).where(*filters)).one() or 0)
    stmt = (
        select(EduAnomalyAlert)
        .where(*filters)
        .order_by(col(EduAnomalyAlert.create_time).desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
    )
    rows = list(session.exec(stmt).all())
    return rows, total


def get_alert_for_scope(
    session: Session, alert_id: int, scope: EduScope, *, workspace_oid: int
) -> EduAnomalyAlert | None:
    if not can_access_anomaly_alerts(scope):
        return None
    row = session.get(EduAnomalyAlert, alert_id)
    if row is None or int(row.workspace_oid) != int(workspace_oid):
        return None
    school_id, class_names = _scope_filter_clause(scope)
    if school_id and row.school_id != school_id:
        return None
    if scope.edu_role == "teacher" and class_names and row.class_name not in class_names:
        return None
    return row


def confirm_alert(
    session: Session,
    alert_id: int,
    scope: EduScope,
    *,
    workspace_oid: int,
    user_id: int,
    note: str = "",
) -> EduAnomalyAlert | None:
    row = get_alert_for_scope(session, alert_id, scope, workspace_oid=workspace_oid)
    if row is None:
        return None
    if row.status == STATUS_CONFIRMED:
        return row
    row.status = STATUS_CONFIRMED
    row.confirmed_by = int(user_id)
    row.confirmed_at = datetime.utcnow()
    row.confirm_note = (note or "").strip()[:512]
    row.update_time = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def upsert_alert_events(
    session: Session,
    events: list[dict[str, Any]],
) -> dict[str, int]:
    """按 dedupe_key upsert。已确认的保持 confirmed，仅更新快照字段；pending 可刷新。"""
    inserted = 0
    updated = 0
    skipped = 0
    now = datetime.utcnow()
    for ev in events:
        key = str(ev.get("dedupe_key") or "").strip()
        if not key:
            skipped += 1
            continue
        existing = session.exec(
            select(EduAnomalyAlert).where(EduAnomalyAlert.dedupe_key == key)
        ).first()
        if existing is None:
            row = EduAnomalyAlert(
                workspace_oid=int(ev["workspace_oid"]),
                datasource_id=int(ev["datasource_id"]),
                school_id=str(ev.get("school_id") or ""),
                class_name=str(ev.get("class_name") or ""),
                student_id=str(ev.get("student_id") or ""),
                exam_id=str(ev.get("exam_id") or ""),
                exam_name=str(ev.get("exam_name") or ""),
                subject_name=str(ev.get("subject_name") or ""),
                anomaly_type=str(ev.get("anomaly_type") or ""),
                title=str(ev.get("title") or ""),
                reason=str(ev.get("reason") or ""),
                payload_json=dict(ev.get("payload") or {}),
                source=str(ev.get("source") or SOURCE_SCORE_IMPORT),
                status=STATUS_PENDING,
                dedupe_key=key,
                confirm_note="",
                create_time=now,
                update_time=now,
            )
            session.add(row)
            inserted += 1
            continue

        # 已确认：只刷新展示快照，不改回 pending
        existing.reason = str(ev.get("reason") or existing.reason)
        existing.title = str(ev.get("title") or existing.title)
        existing.payload_json = dict(ev.get("payload") or existing.payload_json or {})
        existing.exam_name = str(ev.get("exam_name") or existing.exam_name)
        existing.class_name = str(ev.get("class_name") or existing.class_name)
        existing.update_time = now
        if existing.status != STATUS_CONFIRMED:
            existing.status = STATUS_PENDING
            existing.source = str(ev.get("source") or existing.source)
        session.add(existing)
        updated += 1

    session.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def _escape_sql_str(value: str) -> str:
    return (value or "").replace("'", "''")


def _fetch_score_rows(
    db_type: str,
    config: dict[str, Any],
    *,
    school_id: str,
    exam_id: str,
) -> list[dict[str, Any]]:
    from datasource.db.db import execute_sql

    sid = _escape_sql_str(school_id)
    eid = _escape_sql_str(str(exam_id))
    sql = (
        "SELECT student_id, school_id, class, subject_name, exam_id, score, exam_score "
        f"FROM tb_score WHERE school_id = '{sid}' AND CAST(exam_id AS TEXT) = '{eid}'"
    )
    ok, msg, data = execute_sql(db_type, config, sql)
    if not ok or not isinstance(data, dict):
        logger.warning("anomaly alert score fetch failed: %s", msg)
        return []
    columns = list(data.get("columns") or [])
    rows = list(data.get("rows") or [])
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append({columns[i]: row[i] for i in range(min(len(columns), len(row)))})
    return out


def _fetch_prev_scores(
    db_type: str,
    config: dict[str, Any],
    *,
    school_id: str,
    exam_id: str,
    subject_name: str,
) -> dict[str, float]:
    """同校同科、更早一场考试的上次得分 student_id -> score。"""
    from datasource.db.db import execute_sql

    sid = _escape_sql_str(school_id)
    eid = _escape_sql_str(str(exam_id))
    subj = _escape_sql_str(subject_name)
    sql = f"""
    WITH cur AS (
      SELECT exam_time FROM tb_exam WHERE CAST(id AS TEXT) = '{eid}' LIMIT 1
    ),
    prev AS (
      SELECT e.id AS exam_id
      FROM tb_exam e, cur
      WHERE e.subject = '{subj}'
        AND e.exam_time IS NOT NULL
        AND cur.exam_time IS NOT NULL
        AND e.exam_time < cur.exam_time
      ORDER BY e.exam_time DESC
      LIMIT 1
    )
    SELECT sc.student_id, sc.score
    FROM tb_score sc
    JOIN prev ON CAST(sc.exam_id AS TEXT) = CAST(prev.exam_id AS TEXT)
    WHERE sc.school_id = '{sid}' AND sc.subject_name = '{subj}'
    """
    ok, msg, data = execute_sql(db_type, config, sql)
    if not ok or not isinstance(data, dict):
        logger.debug("prev score fetch skipped: %s", msg)
        return {}
    columns = list(data.get("columns") or [])
    rows = list(data.get("rows") or [])
    out: dict[str, float] = {}
    for row in rows:
        if isinstance(row, dict):
            stu = str(row.get("student_id") or "")
            try:
                out[stu] = float(row.get("score"))
            except (TypeError, ValueError):
                continue
        elif isinstance(row, (list, tuple)) and len(columns) >= 2:
            try:
                idx_s = columns.index("student_id") if "student_id" in columns else 0
                idx_v = columns.index("score") if "score" in columns else 1
                out[str(row[idx_s])] = float(row[idx_v])
            except (TypeError, ValueError, IndexError):
                continue
    return out


def _exam_name_lookup(
    db_type: str, config: dict[str, Any], exam_id: str
) -> str:
    from datasource.db.db import execute_sql

    eid = _escape_sql_str(str(exam_id))
    sql = f"SELECT exam_name FROM tb_exam WHERE CAST(id AS TEXT) = '{eid}' LIMIT 1"
    ok, _, data = execute_sql(db_type, config, sql)
    if not ok or not isinstance(data, dict):
        return ""
    rows = list(data.get("rows") or [])
    if not rows:
        return ""
    row = rows[0]
    if isinstance(row, dict):
        return str(row.get("exam_name") or "")
    if isinstance(row, (list, tuple)) and row:
        return str(row[0] or "")
    return ""


def _rows_to_students(
    score_rows: list[dict[str, Any]],
    prev_by_subject: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    students: list[dict[str, Any]] = []
    for r in score_rows:
        stu = str(r.get("student_id") or "").strip()
        if not stu:
            continue
        subj = str(r.get("subject_name") or "").strip()
        try:
            score = float(r.get("score"))
        except (TypeError, ValueError):
            continue
        item: dict[str, Any] = {
            "name": stu,
            "student_id": stu,
            "subject": subj,
            "score": score,
            "class": str(r.get("class") or r.get("class_name") or ""),
            "school_id": str(r.get("school_id") or ""),
            "exam_id": str(r.get("exam_id") or ""),
            "exam_score": r.get("exam_score"),
        }
        prev_map = prev_by_subject.get(subj) or {}
        if stu in prev_map:
            item["prev_score"] = prev_map[stu]
        students.append(item)
    return students


def _empty_at_risk() -> dict[str, list[dict[str, Any]]]:
    return {
        ANOMALY_CRITICAL: [],
        ANOMALY_REGRESSION: [],
        ANOMALY_IMBALANCED: [],
    }


def _normalize_at_risk(at_risk: dict[str, list[dict[str, Any]]] | None) -> dict[str, list[dict[str, Any]]]:
    out = _empty_at_risk()
    for key in out:
        out[key] = [dict(it) for it in ((at_risk or {}).get(key) or []) if isinstance(it, dict)]
    return out


def _split_at_risk_by_class(
    at_risk: dict[str, list[dict[str, Any]]],
    class_by_student: dict[str, str],
    default_class: str = "",
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """按班级拆成多份报告（老师只看本班）。"""
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for anomaly_type, items in _normalize_at_risk(at_risk).items():
        for it in items:
            student_id = str(it.get("student_id") or it.get("name") or "").strip()
            class_name = str(
                it.get("class")
                or it.get("class_name")
                or (class_by_student.get(student_id) if student_id else "")
                or default_class
                or ""
            ).strip()
            if class_name not in buckets:
                buckets[class_name] = _empty_at_risk()
            buckets[class_name][anomaly_type].append(it)
    return buckets


def _build_report_event(
    at_risk: dict[str, list[dict[str, Any]]],
    *,
    workspace_oid: int,
    datasource_id: int,
    school_id: str,
    exam_id: str,
    exam_name: str,
    class_name: str,
    subject_name: str = "",
    source: str,
) -> dict[str, Any] | None:
    """一份报告一条事件；无异常则返回 None。"""
    normalized = _normalize_at_risk(at_risk)
    n_c = len(normalized[ANOMALY_CRITICAL])
    n_r = len(normalized[ANOMALY_REGRESSION])
    n_i = len(normalized[ANOMALY_IMBALANCED])
    if n_c + n_r + n_i <= 0:
        return None

    class_label = class_name or "全校"
    exam_label = (exam_name or "").strip() or str(exam_id)
    subject_part = f" · {subject_name}" if (subject_name or "").strip() else ""
    title = f"{class_label} · {exam_label}{subject_part} · 异常报告"
    reason = f"临界生 {n_c} 人，大幅退步 {n_r} 人，偏科 {n_i} 人"
    dedupe = build_dedupe_key(
        workspace_oid=workspace_oid,
        datasource_id=datasource_id,
        school_id=school_id,
        exam_id=str(exam_id),
        class_name=class_name,
        subject_name=subject_name,
        source=source,
    )
    return {
        "workspace_oid": workspace_oid,
        "datasource_id": datasource_id,
        "school_id": school_id,
        "class_name": class_name,
        "student_id": "",
        "exam_id": str(exam_id),
        "exam_name": exam_name,
        "subject_name": subject_name or "",
        "anomaly_type": ANOMALY_REPORT,
        "title": title,
        "reason": reason,
        "payload": {
            "counts": {"critical": n_c, "regression": n_r, "imbalanced": n_i},
            ANOMALY_CRITICAL: normalized[ANOMALY_CRITICAL],
            ANOMALY_REGRESSION: normalized[ANOMALY_REGRESSION],
            ANOMALY_IMBALANCED: normalized[ANOMALY_IMBALANCED],
        },
        "source": source,
        "dedupe_key": dedupe,
    }


def _purge_legacy_student_alerts(
    session: Session,
    *,
    workspace_oid: int,
    datasource_id: int,
    school_id: str,
    exam_id: str,
    class_name: str | None = None,
) -> int:
    """清理旧版「每人一条」记录，避免与报告级列表混杂。"""
    filters = [
        EduAnomalyAlert.workspace_oid == int(workspace_oid),
        EduAnomalyAlert.datasource_id == int(datasource_id),
        EduAnomalyAlert.school_id == str(school_id),
        EduAnomalyAlert.exam_id == str(exam_id),
        col(EduAnomalyAlert.anomaly_type).in_(list(_LEGACY_STUDENT_TYPES)),
    ]
    if class_name is not None and str(class_name).strip():
        filters.append(EduAnomalyAlert.class_name == str(class_name).strip())
    rows = list(session.exec(select(EduAnomalyAlert).where(*filters)).all())
    for row in rows:
        session.delete(row)
    return len(rows)


def detect_and_upsert_for_exam(
    session: Session,
    *,
    db_type: str,
    config: dict[str, Any],
    workspace_oid: int,
    datasource_id: int,
    school_id: str,
    exam_id: str,
    exam_name: str = "",
    source: str = SOURCE_SCORE_IMPORT,
    class_names: list[str] | None = None,
) -> dict[str, int]:
    """对单校单场拉取成绩、跑三态检测并 upsert。

    ``class_names`` 非空时只为这些班级生成报告（导入触发时用，避免扫出其它班）。
    """
    school_id = (school_id or "").strip()
    exam_id = str(exam_id or "").strip()
    if not school_id or not exam_id:
        return {"inserted": 0, "updated": 0, "skipped": 0, "detected": 0}

    score_rows = _fetch_score_rows(db_type, config, school_id=school_id, exam_id=exam_id)
    if not score_rows:
        return {"inserted": 0, "updated": 0, "skipped": 0, "detected": 0}

    class_filter = {str(c).strip() for c in (class_names or []) if str(c).strip()}
    if class_filter:
        score_rows = [
            r
            for r in score_rows
            if str(r.get("class") or r.get("class_name") or "").strip() in class_filter
        ]
        if not score_rows:
            return {"inserted": 0, "updated": 0, "skipped": 0, "detected": 0}

    if not exam_name:
        exam_name = _exam_name_lookup(db_type, config, exam_id)

    subjects = sorted(
        {str(r.get("subject_name") or "").strip() for r in score_rows if str(r.get("subject_name") or "").strip()}
    )
    prev_by_subject: dict[str, dict[str, float]] = {}
    for subj in subjects:
        prev_by_subject[subj] = _fetch_prev_scores(
            db_type, config, school_id=school_id, exam_id=exam_id, subject_name=subj
        )

    students = _rows_to_students(score_rows, prev_by_subject)
    class_by_student = {
        str(r.get("student_id") or ""): str(r.get("class") or r.get("class_name") or "")
        for r in score_rows
    }

    edu_cfg = copy(get_config())
    # 用本场卷面满分调整及格绝对线（有 exam_score 时）
    full_score = None
    for r in score_rows:
        if r.get("exam_score") is not None:
            try:
                full_score = float(r["exam_score"])
                break
            except (TypeError, ValueError):
                pass
    if full_score is not None and full_score > 0:
        edu_cfg.pass_threshold = float(full_score) * float(edu_cfg.pass_ratio)
        edu_cfg.excellent_threshold = float(full_score) * float(edu_cfg.excellent_ratio)

    at_risk = identify_at_risk_students(students, edu_cfg)
    by_class = _split_at_risk_by_class(at_risk, class_by_student)
    events: list[dict[str, Any]] = []
    for class_name, class_risk in by_class.items():
        ev = _build_report_event(
            class_risk,
            workspace_oid=workspace_oid,
            datasource_id=datasource_id,
            school_id=school_id,
            exam_id=exam_id,
            exam_name=exam_name,
            class_name=class_name,
            subject_name="",
            source=source,
        )
        if ev:
            events.append(ev)
            _purge_legacy_student_alerts(
                session,
                workspace_oid=workspace_oid,
                datasource_id=datasource_id,
                school_id=school_id,
                exam_id=exam_id,
                class_name=class_name,
            )
    stats = upsert_alert_events(session, events)
    stats["detected"] = len(events)
    return stats


def scan_alerts_after_import(
    session: Session,
    *,
    db_type: str,
    config: dict[str, Any],
    workspace_oid: int,
    datasource_id: int,
    resolved_rows: list[Any],
    exam_batch_id: int | None = None,
) -> dict[str, Any]:
    """导入成功后扫描异常提醒。

    - 仅 ``resolved_rows``：按 (school_id, exam_id) 去重，并按涉及班级过滤。
    - 同时传 ``exam_batch_id``：扫描该批次全部试卷 × resolved 中的学校，不按班级过滤。
    """
    from datasource.db.db import execute_sql

    pairs: dict[tuple[str, str], str] = {}
    classes_by_pair: dict[tuple[str, str], set[str]] = {}

    if exam_batch_id is not None:
        school_ids: set[str] = set()
        for r in resolved_rows or []:
            sid = str(
                getattr(r, "school_id", None)
                or (r.get("school_id") if isinstance(r, dict) else "")
                or getattr(r, "school_token", None)
                or (r.get("school_token") if isinstance(r, dict) else "")
                or ""
            ).strip()
            if sid:
                school_ids.add(sid)
        if not school_ids:
            return {"inserted": 0, "updated": 0, "skipped": 0, "detected": 0, "exams": 0}
        batch_id = int(exam_batch_id)
        ok, _msg, data = execute_sql(
            db_type,
            config,
            f"SELECT id, exam_name FROM tb_exam WHERE exam_batch_id = {batch_id}",
        )
        exam_rows: list[dict[str, Any]] = []
        if ok and isinstance(data, dict):
            cols = list(data.get("columns") or [])
            for row in data.get("rows") or []:
                if isinstance(row, dict):
                    exam_rows.append(row)
                elif isinstance(row, (list, tuple)):
                    exam_rows.append({cols[i]: row[i] for i in range(min(len(cols), len(row)))})
        for exam in exam_rows:
            eid = str(exam.get("id") or "").strip()
            ename = str(exam.get("exam_name") or "")
            if not eid:
                continue
            for sid in school_ids:
                pairs[(sid, eid)] = ename
        classes_by_pair = {pair: set() for pair in pairs}
    else:
        for r in resolved_rows or []:
            school_id = str(
                getattr(r, "school_id", None) or (r.get("school_id") if isinstance(r, dict) else "") or ""
            ).strip()
            exam_id = str(
                getattr(r, "exam_id", None) or (r.get("exam_id") if isinstance(r, dict) else "") or ""
            ).strip()
            exam_name = str(
                getattr(r, "exam_name", None) or (r.get("exam_name") if isinstance(r, dict) else "") or ""
            ).strip()
            class_name = str(
                getattr(r, "class_name", None) or (r.get("class_name") if isinstance(r, dict) else "") or ""
            ).strip()
            if school_id and exam_id:
                pairs[(school_id, exam_id)] = exam_name or pairs.get((school_id, exam_id), "")
                classes_by_pair.setdefault((school_id, exam_id), set())
                if class_name:
                    classes_by_pair[(school_id, exam_id)].add(class_name)

    totals = {"inserted": 0, "updated": 0, "skipped": 0, "detected": 0, "exams": 0}
    for (school_id, exam_id), exam_name in pairs.items():
        try:
            st = detect_and_upsert_for_exam(
                session,
                db_type=db_type,
                config=config,
                workspace_oid=workspace_oid,
                datasource_id=datasource_id,
                school_id=school_id,
                exam_id=exam_id,
                exam_name=exam_name,
                source=SOURCE_SCORE_IMPORT,
                class_names=sorted(classes_by_pair.get((school_id, exam_id)) or []),
            )
            for k in ("inserted", "updated", "skipped", "detected"):
                totals[k] += int(st.get(k) or 0)
            totals["exams"] += 1
        except Exception:  # noqa: BLE001 — 提醒失败不阻断导入成功
            logger.exception(
                "scan_alerts_after_import failed school=%s exam=%s", school_id, exam_id
            )
    return totals


def upsert_from_at_risk_payload(
    session: Session,
    at_risk: dict[str, list[dict[str, Any]]],
    *,
    workspace_oid: int,
    datasource_id: int,
    school_id: str,
    exam_id: str,
    exam_name: str = "",
    class_name: str = "",
    subject_name: str = "",
    source: str = SOURCE_TIER_ALERT,
) -> dict[str, int]:
    """分层预警报告产出后：整份报告写为一条提醒。"""
    class_by_student: dict[str, str] = {}
    for items in (at_risk or {}).values():
        for it in items or []:
            stu = str(it.get("student_id") or it.get("name") or "")
            cls = str(it.get("class") or it.get("class_name") or class_name or "")
            if stu:
                class_by_student[stu] = cls

    # 报告已指定班级则整包写入；否则按班拆分
    events: list[dict[str, Any]] = []
    if (class_name or "").strip():
        ev = _build_report_event(
            at_risk or {},
            workspace_oid=workspace_oid,
            datasource_id=datasource_id,
            school_id=school_id,
            exam_id=exam_id,
            exam_name=exam_name,
            class_name=str(class_name).strip(),
            subject_name=subject_name,
            source=source,
        )
        if ev:
            events.append(ev)
            _purge_legacy_student_alerts(
                session,
                workspace_oid=workspace_oid,
                datasource_id=datasource_id,
                school_id=school_id,
                exam_id=exam_id,
                class_name=str(class_name).strip(),
            )
    else:
        for cls, class_risk in _split_at_risk_by_class(at_risk or {}, class_by_student).items():
            ev = _build_report_event(
                class_risk,
                workspace_oid=workspace_oid,
                datasource_id=datasource_id,
                school_id=school_id,
                exam_id=exam_id,
                exam_name=exam_name,
                class_name=cls,
                subject_name=subject_name,
                source=source,
            )
            if ev:
                events.append(ev)
                _purge_legacy_student_alerts(
                    session,
                    workspace_oid=workspace_oid,
                    datasource_id=datasource_id,
                    school_id=school_id,
                    exam_id=exam_id,
                    class_name=cls,
                )

    stats = upsert_alert_events(session, events)
    stats["detected"] = len(events)
    return stats


__all__ = [
    "STATUS_CONFIRMED",
    "STATUS_PENDING",
    "SOURCE_SCORE_IMPORT",
    "SOURCE_TIER_ALERT",
    "ANOMALY_REPORT",
    "alert_to_dict",
    "can_access_anomaly_alerts",
    "confirm_alert",
    "detect_and_upsert_for_exam",
    "get_alert_for_scope",
    "list_alerts",
    "scan_alerts_after_import",
    "upsert_alert_events",
    "upsert_from_at_risk_payload",
]
