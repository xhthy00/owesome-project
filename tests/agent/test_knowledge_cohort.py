"""班内后十 vs 中位组：知识点差距纯函数与路由。"""

from __future__ import annotations

from src.agent.education.intent_router import (
    classify_report_intent_sync,
    plan_items_for_route,
)
from src.agent.education.knowledge_cohort import (
    build_knowledge_cohort_report_data,
    compare_knowledge_by_cohort,
    render_knowledge_cohort_html,
    split_score_cohorts,
)
from src.agent.education.query_parse import is_knowledge_cohort_gap_query

DEMO_Q = "高二(6)班数学期末最后十名与中位数在知识点掌握方面的差距"


def _scores_20() -> list[dict]:
    # 分数 100, 95, ..., 5 —— 共 20 人，降序排名后 bottom10 = s11..s20
    return [
        {"student_id": f"s{i:02d}", "score": float(105 - 5 * i)}
        for i in range(1, 21)
    ]


def test_split_score_cohorts_bottom_and_median_band():
    rows = _scores_20()
    got = split_score_cohorts(rows, bottom_n=10, median_band=2)
    assert got["n"] == 20
    assert got["median_rank"] == 10
    assert got["bottom_ids"] == [f"s{i:02d}" for i in range(11, 21)]
    # 中位名次 10 ±2 → 名次 8..12 → s08..s12
    assert got["median_ids"] == [f"s{i:02d}" for i in range(8, 13)]
    assert got["median_rank_lo"] == 8
    assert got["median_rank_hi"] == 12


def test_compare_knowledge_by_cohort_gap_sort():
    bottom = [f"s{i:02d}" for i in range(11, 21)]
    median = [f"s{i:02d}" for i in range(8, 13)]
    # 每名学生每个知识点仅一行，避免后十∩中位组重复累加
    all_ids = list(dict.fromkeys([*median, *bottom]))
    details = []
    for sid in all_ids:
        in_median = sid in median
        # 函数：中位组满分、后十（且非中位）零分；重叠学生按中位口径满分
        fn_score = 10 if in_median else 0
        details.append(
            {
                "student_id": sid,
                "knowledge_name": "函数",
                "score": fn_score,
                "full_score": 10,
            }
        )
        details.append(
            {
                "student_id": sid,
                "knowledge_name": "几何",
                "score": 8,
                "full_score": 10,
            }
        )

    rows = compare_knowledge_by_cohort(details, bottom, median)
    assert rows[0]["knowledge_name"] == "函数"
    # 后十含 s11/s12（与中位重叠，满分）+ s13..s20（0）→ (20+0)/100 = 20%
    assert rows[0]["bottom_rate"] == 20.0
    assert rows[0]["median_rate"] == 100.0
    assert rows[0]["gap"] == 80.0
    assert abs(rows[1]["gap"] or 0) < abs(rows[0]["gap"])
    # 无重叠时 gap 语义更干净：仅用不相交集合再验一次
    rows2 = compare_knowledge_by_cohort(
        details,
        [f"s{i:02d}" for i in range(15, 21)],
        [f"s{i:02d}" for i in range(8, 11)],
    )
    assert rows2[0]["knowledge_name"] == "函数"
    assert rows2[0]["bottom_rate"] == 0.0
    assert rows2[0]["median_rate"] == 100.0
    assert rows2[0]["gap"] == 100.0


def test_build_report_data_and_html():
    cohorts = split_score_cohorts(_scores_20(), bottom_n=10, median_band=2)
    compare_rows = [
        {
            "knowledge_name": "函数",
            "bottom_rate": 40.0,
            "median_rate": 75.0,
            "gap": 35.0,
        },
        {
            "knowledge_name": "几何",
            "bottom_rate": 60.0,
            "median_rate": 70.0,
            "gap": 10.0,
        },
    ]
    data = build_knowledge_cohort_report_data(
        class_name="高二(6)班",
        subject_name="数学",
        exam_name="期末",
        cohorts=cohorts,
        compare_rows=compare_rows,
    )
    assert data["empty"] is False
    assert "函数" in data["CONCLUSION"]
    assert "35.00pp" in data["CONCLUSION"] or "35" in data["CONCLUSION"]
    assert "edu-table" in data["TABLE_HTML"]
    assert "kc-rate" in data["TABLE_HTML"]
    assert "kc-gap" in data["TABLE_HTML"]
    assert data["CHART_OPTION"]
    html = render_knowledge_cohort_html(data)
    assert "知识点分层对比" in html or "高二(6)班" in html
    assert "kc_gap_chart" in html
    assert "data-edu-echart" in html
    assert "kc-report" in html
    assert "kc-kpi" in html
    assert "<style>" in html
    assert "差距最大" in html
    assert "echarts" in html
    assert "echarts.init" in html
    assert "<!DOCTYPE html>" in html


def test_empty_state_when_no_knowledge():
    data = build_knowledge_cohort_report_data(
        class_name="高二(6)班",
        subject_name="数学",
        exam_name="期末",
        cohorts={"n": 20, "bottom_ids": ["a"], "median_ids": ["b"]},
        compare_rows=[],
    )
    assert data["empty"] is True
    html = render_knowledge_cohort_html(data)
    assert "无知识点" in html or "无法计算" in html


def test_is_knowledge_cohort_gap_query():
    assert is_knowledge_cohort_gap_query(DEMO_Q) is True
    assert is_knowledge_cohort_gap_query("高二(6)班数学期末均分是多少") is False
    assert is_knowledge_cohort_gap_query("最后十名成绩") is False
    assert is_knowledge_cohort_gap_query("中位数与知识点") is False


def test_route_forces_compare_knowledge_cohort_tool():
    route = classify_report_intent_sync(DEMO_Q)
    assert route.needs_report is True
    plans = plan_items_for_route(route, DEMO_Q)
    blob = " ".join(p["sub_task"] for p in plans)
    assert "compare_knowledge_cohort_tool" in blob
    assert "禁止" in blob and "build_subject_diagnosis_sections_tool" in blob
    assert "class_name=高二(6)班" in blob
    assert "subject_name=数学" in blob


def test_exam_extract_not_class_subject_garbage():
    """「高二(6)班数学期末考试…」不得抽成「班数学期末考试」。"""
    from src.agent.education.orchestrator import _extract_exam
    from src.agent.education.query_parse import (
        _clean_exam_name_candidate,
        extract_exam_name_hint,
    )
    from src.agent.education.tools import exam_name_like_candidates
    from src.agent.expand.planner import _plan_exam_name, build_knowledge_cohort_plan_items

    q = "高二(6)班数学期末考试最后十名与中位数在知识点掌握方面的差距"
    assert _clean_exam_name_candidate("班数学期末考试") == "期末考试"
    assert _extract_exam(q) in {"期末", "期末考试"}
    assert extract_exam_name_hint(q) in {"期末", "期末考试"}
    assert _plan_exam_name(q) in {"期末", "期末考试"}
    blob = " ".join(p["sub_task"] for p in build_knowledge_cohort_plan_items(q))
    assert "班数学期末考试" not in blob
    assert "exam_name=期末" in blob or "exam_name=期末考试" in blob
    cands = exam_name_like_candidates("班数学期末考试")
    assert "期末" in cands
    assert "期末考试" in cands
