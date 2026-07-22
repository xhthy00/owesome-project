"""异常提醒：权限、报告级 upsert、确认状态。"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine, select

from datasource.service.edu_permission import EduScope
from src.agent.education.alert_service import (
    ANOMALY_REPORT,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    alert_to_dict,
    build_dedupe_key,
    can_access_anomaly_alerts,
    confirm_alert,
    list_alerts,
    upsert_alert_events,
    upsert_from_at_risk_payload,
)
from src.agent.education.config import ANOMALY_CRITICAL
from src.agent.education.models_alert import EduAnomalyAlert


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[EduAnomalyAlert.__table__])
    return Session(engine)


def test_can_access_roles():
    assert can_access_anomaly_alerts(EduScope(edu_role="school_admin", school_id="YZZX"))
    assert can_access_anomaly_alerts(EduScope(edu_role="teacher", school_id="YZZX", class_names=["高三(10)班"]))
    assert not can_access_anomaly_alerts(EduScope(edu_role="bureau_admin"))
    assert not can_access_anomaly_alerts(EduScope(edu_role="student", student_id="1"))
    assert not can_access_anomaly_alerts(EduScope())


def test_upsert_report_keeps_confirmed_status():
    session = _session()
    key = build_dedupe_key(
        workspace_oid=1,
        datasource_id=1,
        school_id="YZZX",
        exam_id="3",
        class_name="高三(10)班",
        subject_name="",
        source="score_import",
    )
    ev = {
        "workspace_oid": 1,
        "datasource_id": 1,
        "school_id": "YZZX",
        "class_name": "高三(10)班",
        "student_id": "",
        "exam_id": "3",
        "exam_name": "统考",
        "subject_name": "",
        "anomaly_type": ANOMALY_REPORT,
        "title": "高三(10)班 · 统考 · 异常报告",
        "reason": "临界生 1 人，大幅退步 0 人，偏科 0 人",
        "payload": {
            "counts": {"critical": 1, "regression": 0, "imbalanced": 0},
            "critical": [{"student_id": "S1", "score": 70, "reason": "临界生：70 分"}],
            "regression": [],
            "imbalanced": [],
        },
        "source": "score_import",
        "dedupe_key": key,
    }
    upsert_alert_events(session, [ev])
    row = session.exec(select(EduAnomalyAlert).where(EduAnomalyAlert.dedupe_key == key)).one()
    assert row.status == STATUS_PENDING
    assert row.anomaly_type == ANOMALY_REPORT

    scope = EduScope(edu_role="school_admin", school_id="YZZX")
    confirmed = confirm_alert(session, int(row.id), scope, workspace_oid=1, user_id=9, note="已谈心")
    assert confirmed is not None
    assert confirmed.status == STATUS_CONFIRMED
    assert confirmed.confirm_note == "已谈心"

    ev2 = {
        **ev,
        "reason": "临界生 1 人，大幅退步 0 人，偏科 0 人",
        "payload": {
            "counts": {"critical": 1, "regression": 0, "imbalanced": 0},
            "critical": [{"student_id": "S1", "score": 71, "reason": "临界生：71 分"}],
            "regression": [],
            "imbalanced": [],
        },
    }
    upsert_alert_events(session, [ev2])
    row2 = session.exec(select(EduAnomalyAlert).where(EduAnomalyAlert.dedupe_key == key)).one()
    assert row2.status == STATUS_CONFIRMED
    assert row2.payload_json["critical"][0]["score"] == 71


def test_upsert_from_at_risk_is_one_report():
    session = _session()
    at_risk = {
        "critical": [
            {"student_id": "A", "name": "A", "subject": "数学", "score": 70, "class": "高三(10)班"},
            {"student_id": "B", "name": "B", "subject": "数学", "score": 72, "class": "高三(10)班"},
        ],
        "regression": [
            {"student_id": "C", "name": "C", "subject": "数学", "score": 60, "prev_score": 90, "class": "高三(10)班"}
        ],
        "imbalanced": [],
    }
    stats = upsert_from_at_risk_payload(
        session,
        at_risk,
        workspace_oid=1,
        datasource_id=1,
        school_id="YZZX",
        exam_id="3",
        exam_name="统考",
        class_name="高三(10)班",
        source="tier_alert_report",
    )
    assert stats["detected"] == 1
    assert stats["inserted"] == 1
    rows, total = list_alerts(
        session, EduScope(edu_role="school_admin", school_id="YZZX"), workspace_oid=1
    )
    assert total == 1
    d = alert_to_dict(rows[0])
    assert d["anomaly_type"] == ANOMALY_REPORT
    assert d["counts"]["critical"] == 2
    assert d["counts"]["regression"] == 1
    assert len(d["payload"]["critical"]) == 2


def test_teacher_list_filters_by_class():
    session = _session()
    now = datetime.utcnow()
    for cls in ["高三(10)班", "高三(11)班"]:
        key = build_dedupe_key(
            workspace_oid=1,
            datasource_id=1,
            school_id="YZZX",
            exam_id="3",
            class_name=cls,
            source="score_import",
        )
        session.add(
            EduAnomalyAlert(
                workspace_oid=1,
                datasource_id=1,
                school_id="YZZX",
                class_name=cls,
                student_id="",
                exam_id="3",
                exam_name="统考",
                subject_name="",
                anomaly_type=ANOMALY_REPORT,
                title=f"{cls} · 统考 · 异常报告",
                reason="临界生 1 人，大幅退步 0 人，偏科 0 人",
                payload_json={
                    "counts": {"critical": 1, "regression": 0, "imbalanced": 0},
                    "critical": [{"student_id": "X"}],
                    "regression": [],
                    "imbalanced": [],
                },
                source="score_import",
                status=STATUS_PENDING,
                dedupe_key=key,
                confirm_note="",
                create_time=now,
                update_time=now,
            )
        )
    session.commit()

    teacher = EduScope(edu_role="teacher", school_id="YZZX", class_names=["高三(10)班"])
    rows, total = list_alerts(session, teacher, workspace_oid=1)
    assert total == 1
    assert rows[0].class_name == "高三(10)班"
    d = alert_to_dict(rows[0])
    assert d["anomaly_type_label"] == "异常报告"


def test_legacy_student_rows_consolidated_on_list():
    session = _session()
    now = datetime.utcnow()
    for stu, typ in [("A", ANOMALY_CRITICAL), ("B", ANOMALY_CRITICAL)]:
        session.add(
            EduAnomalyAlert(
                workspace_oid=1,
                datasource_id=1,
                school_id="YZZX",
                class_name="高三(10)班",
                student_id=stu,
                exam_id="3",
                exam_name="统考",
                subject_name="数学",
                anomaly_type=typ,
                title=f"临界 · {stu}",
                reason="x",
                payload_json={"student_id": stu, "score": 70},
                source="score_import",
                status=STATUS_PENDING,
                dedupe_key=f"legacy|{stu}",
                confirm_note="",
                create_time=now,
                update_time=now,
            )
        )
    session.commit()

    rows, total = list_alerts(
        session, EduScope(edu_role="school_admin", school_id="YZZX"), workspace_oid=1
    )
    assert total == 1
    assert rows[0].anomaly_type == ANOMALY_REPORT
    assert alert_to_dict(rows[0])["counts"]["critical"] == 2
