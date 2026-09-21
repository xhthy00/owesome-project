from src.agent.education.class_track import (
    TRACK_HIS,
    TRACK_PHY,
    both_tracks_present,
    class_track_map,
    filter_rows_by_track,
    track_of_xkkm,
)
from src.agent.education.config import EducationConfig
from src.agent.education.direction_choice import (
    compute_pending_choices,
    is_fact_metric_unresolved,
    is_report_type_unresolved,
)
from src.agent.education.intent_router import (
    ReportRoute,
    fallback_classify_report_intent,
    plan_items_for_route,
)
from src.agent.education.query_parse import is_school_class_comparison_query
from src.agent.education.report_types import ReportType
from src.agent.education.school_intervention import (
    build_class_compare_table_html,
    build_school_intervention_insights,
)


def test_track_of_xkkm() -> None:
    assert track_of_xkkm("物化生") == TRACK_PHY
    assert track_of_xkkm("史政地") == TRACK_HIS
    assert track_of_xkkm("历政地") == TRACK_HIS
    assert track_of_xkkm("物理类") == TRACK_PHY
    assert track_of_xkkm("") == ""


def test_class_track_majority() -> None:
    rows = [
        {"class_name": "1班", "xkkm": "物化生", "score": 80},
        {"class_name": "1班", "xkkm": "物化生", "score": 82},
        {"class_name": "10班", "xkkm": "史政地", "score": 78},
        {"class_name": "10班", "xkkm": "历政地", "score": 76},
    ]
    cmap = class_track_map(rows)
    assert cmap["1班"] == TRACK_PHY
    assert cmap["10班"] == TRACK_HIS
    assert both_tracks_present(cmap)
    assert len(filter_rows_by_track(rows, cmap, TRACK_PHY)) == 2
    assert not both_tracks_present({"1班": TRACK_PHY, "2班": TRACK_PHY})


def test_class_compare_splits_when_both_tracks() -> None:
    cfg = EducationConfig()
    rows = []
    for score in (90, 88, 86, 70):
        rows.append({"class_name": "1班", "xkkm": "物化生", "score": score, "exam_score": 150})
    for score in (80, 78, 60, 58):
        rows.append({"class_name": "10班", "xkkm": "史政地", "score": score, "exam_score": 150})
    for score in (92, 91, 90, 89):
        rows.append({"class_name": "2班", "xkkm": "物化生", "score": score, "exam_score": 150})
    for score in (77, 76, 75, 74):
        rows.append({"class_name": "11班", "xkkm": "史政地", "score": score, "exam_score": 150})
    insights = build_school_intervention_insights(score_rows=rows, config=cfg)
    html = insights.get("class_compare_html") or ""
    assert "物理方向" in html
    assert "历史方向" in html
    assert "该方向基准" in html
    assert "校级基准" not in html
    assert insights["class_tracks"]["1班"] == TRACK_PHY
    assert insights["class_tracks"]["10班"] == TRACK_HIS


def test_class_compare_keeps_single_table_without_track() -> None:
    cfg = EducationConfig()
    rows = [
        {"class_name": "1班", "score": 80, "exam_score": 150},
        {"class_name": "1班", "score": 82, "exam_score": 150},
        {"class_name": "2班", "score": 70, "exam_score": 150},
        {"class_name": "2班", "score": 72, "exam_score": 150},
        {"class_name": "2班", "score": 74, "exam_score": 150},
    ]
    insights = build_school_intervention_insights(score_rows=rows, config=cfg)
    assert insights.get("class_compare_html") == ""
    html = build_class_compare_table_html(
        insights["class_compare"],
        insights.get("weak_classes"),
        school_stats=insights.get("stats"),
    )
    assert "校级基准" in html
    assert "物理方向" not in html


def test_single_track_does_not_split() -> None:
    cfg = EducationConfig()
    rows = [
        {"class_name": "1班", "xkkm": "物化生", "score": 80, "exam_score": 150},
        {"class_name": "1班", "xkkm": "物化生", "score": 82, "exam_score": 150},
        {"class_name": "2班", "xkkm": "物化生", "score": 70, "exam_score": 150},
        {"class_name": "2班", "xkkm": "物化生", "score": 72, "exam_score": 150},
        {"class_name": "2班", "xkkm": "物化生", "score": 74, "exam_score": 150},
    ]
    insights = build_school_intervention_insights(score_rows=rows, config=cfg)
    assert insights.get("class_compare_html") == ""
    assert "物理方向" not in (
        build_class_compare_table_html(
            insights["class_compare"],
            insights.get("weak_classes"),
            school_stats=insights.get("stats"),
        )
    )


def test_school_class_compare_does_not_ask_fact_metric() -> None:
    q = "对比本校各班成绩。补充：考试=2026届高三1月期末"
    route = ReportRoute(needs_report=False, report_type=None, confidence=0.2, reason="fact")
    assert not is_fact_metric_unresolved(q, route)
    assert not is_fact_metric_unresolved("对比本校各班成绩", route)
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items_for_route(route, q))
    assert "待确认口径" not in blob
    assert "用 SQL 直接回答" in blob
    assert not is_school_class_comparison_query("对比本校各班成绩")


def _assert_own_school_class_compare_plan(q: str) -> None:
    assert is_school_class_comparison_query(q)
    route = fallback_classify_report_intent(q)
    assert route.needs_report is True
    assert route.report_type == ReportType.GRADE_COMPARISON
    assert not is_report_type_unresolved(q, route)
    assert compute_pending_choices(q, route) == []
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items_for_route(route, q))
    assert "待确认口径" not in blob
    assert "question" not in blob
    assert "build_subject_diagnosis_sections_tool" in blob
    assert "禁止传 class_name" in blob


def test_own_school_horizontal_compare_skips_report_type_question() -> None:
    _assert_own_school_class_compare_plan(
        "本校高三各班2026届高三1月期末数学横向对比"
    )
    _assert_own_school_class_compare_plan(
        "我校高三各班2026届高三1月期末数学横向对比"
    )
    _assert_own_school_class_compare_plan(
        "扬大附中高三各班2026届高三1月期末数学横向对比"
    )
    assert not is_school_class_comparison_query(
        "高三各班2026届高三1月期末数学横向对比"
    )
    # 本校单班学情 / 无横向口径：不能抬成各班对比
    assert not is_school_class_comparison_query(
        "生成2026届高三1月期末本校高三(3)班综合学情分析"
    )
    assert not is_school_class_comparison_query("2026届高三1月各校考试分析")
    assert not is_school_class_comparison_query("2026届高三1月各区县总分10分段统计")
    _assert_own_school_class_compare_plan("本校各班数学横向分析")
    _assert_own_school_class_compare_plan("我校高三班级横向对比")
