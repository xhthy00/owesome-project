"""贡献分口径薄弱学科：闸门、校类选线、差值 Top3、文理分轨。"""

from src.agent.education.clarification import FactQueryKind, fact_query_kind
from src.agent.education.contribution_weak import (
    GAP_MIN,
    apply_contribution_weak_verdict,
    evaluate_contribution_weak,
    format_contribution_weak_verdict,
    lines_for_school_type,
    pick_weak_subjects,
)
from src.agent.education.intent_router import fallback_classify_report_intent
from src.agent.education.query_parse import (
    is_class_weak_subject_query,
    is_contribution_report_query,
    is_contribution_weak_query,
    is_subject_strength_query,
)
from src.agent.expand.planner import build_fact_query_plan_items


def test_gate_contribution_weak_not_rank_or_report() -> None:
    q = "安宜中学 2026届高三3月 贡献分薄弱学科"
    assert is_contribution_weak_query(q)
    assert not is_subject_strength_query(q)
    assert not is_contribution_report_query(q)
    assert not is_class_weak_subject_query(q)
    assert fact_query_kind(q) == FactQueryKind.CONTRIBUTION_WEAK
    route = fallback_classify_report_intent(q)
    assert route.needs_report is False
    assert route.report_type is None


def test_plain_weak_subject_still_rank_band() -> None:
    q = "安宜中学3月薄弱学科"
    assert not is_contribution_weak_query(q)
    assert is_subject_strength_query(q)


def test_contribution_report_without_weak() -> None:
    q = "请生成2026届高三3月各科贡献分分析"
    assert not is_contribution_weak_query(q)
    assert is_contribution_report_query(q)


def test_lines_by_school_type() -> None:
    assert lines_for_school_type("市引领校") == ("211", "tz")
    assert lines_for_school_type("支撑") == ("tz", "bk")
    assert lines_for_school_type("发展校") == ("bk",)
    assert lines_for_school_type("其他校") == ()


def test_pick_weak_subjects_gap_and_top3() -> None:
    contrib = {
        "yw": 114,
        "sx": 107,
        "yy": 115,
        "wl": 67,
        "hxzh": 82,
        "swzh": 82,
        "zzzh": 83,
        "dlzh": 83,
        "ls": None,
    }
    avgs = {
        "yw": 110.0,
        "sx": 106.0,
        "yy": 113.6,
        "wl": 66.0,
        "hxzh": 82.0,
        "swzh": 80.4,
        "zzzh": 81.0,
        "dlzh": 70.0,
        "ls": None,
    }
    weak = pick_weak_subjects(contrib, avgs)
    assert [w["subject"] for w in weak] == ["地理", "语文", "政治"]
    assert weak[0]["gap"] == 13.0
    assert all(w["gap"] >= GAP_MIN for w in weak)
    boundary = pick_weak_subjects({"yw": 100}, {"yw": 98.5})
    assert boundary[0]["gap"] == 1.5
    below = pick_weak_subjects({"yw": 100}, {"yw": 98.51})
    assert below == []


def test_evaluate_splits_track_and_line() -> None:
    contrib = [
        {"track": "物理类", "line_code": "211", "yw": 114, "sx": 100, "yy": 90},
        {"track": "物理类", "line_code": "tz", "yw": 108, "sx": 85, "yy": 96},
        {"track": "物理类", "line_code": "bk", "yw": 104, "sx": 70, "yy": 83},
        {"track": "历史类", "line_code": "211", "yw": 117, "sx": 89, "yy": 117},
        {"track": "历史类", "line_code": "tz", "yw": 112, "sx": 70, "yy": 101},
    ]
    avgs = [
        {"track": "物理类", "xxlb": "引领校", "n": 200, "yw": 110, "sx": 99, "yy": 90},
        {"track": "历史类", "xxlb": "引领校", "n": 40, "yw": 116, "sx": 80, "yy": 100},
    ]
    out = evaluate_contribution_weak(contrib, avgs)
    assert out["school_type"] == "引领"
    codes = [(g["track"], g["line_code"]) for g in out["groups"]]
    assert codes == [
        ("物理类", "211"),
        ("物理类", "tz"),
        ("历史类", "211"),
        ("历史类", "tz"),
    ]
    assert all(g["line_code"] != "bk" for g in out["groups"])
    phy_211 = out["groups"][0]["weak"]
    assert phy_211[0]["subject"] == "语文"
    hist_211 = [g for g in out["groups"] if g["track"] == "历史类" and g["line_code"] == "211"][0]
    assert [w["subject"] for w in hist_211["weak"]] == ["英语", "数学"]


def test_evaluate_physics_only_when_track_filtered() -> None:
    contrib = [
        {"track": "物理类", "line_code": "bk", "yw": 104, "sx": 90},
        {"track": "历史类", "line_code": "bk", "yw": 106, "sx": 54},
    ]
    avgs = [
        {"track": "物理类", "xxlb": "发展校", "yw": 100, "sx": 80},
        {"track": "历史类", "xxlb": "发展校", "yw": 100, "sx": 50},
    ]
    out = evaluate_contribution_weak(contrib, avgs, tracks=("物理类",))
    assert [g["track"] for g in out["groups"]] == ["物理类"]
    assert out["groups"][0]["line_code"] == "bk"


def test_format_verdict_per_line() -> None:
    text = format_contribution_weak_verdict(
        {
            "school_type": "引领",
            "groups": [
                {
                    "track": "物理类",
                    "line_code": "211",
                    "line_name": "211线",
                    "weak": [{"subject": "语文", "gap": 4.0}],
                    "missing_contrib": False,
                },
                {
                    "track": "物理类",
                    "line_code": "tz",
                    "line_name": "特控线",
                    "weak": [],
                    "missing_contrib": False,
                },
            ],
        },
        exam_name="2026届高三3月",
        school_name="安宜中学",
    )
    assert "**2026届高三3月 · 安宜中学（引领校）**" in text
    assert "| 选科 | 预测线 | 科目 | 差值 |" in text
    assert "| 物理类 | 211线 | 语文 | 4 |" in text
    assert "| 物理类 | 特控线 | 无 | — |" in text
    assert "本科线" not in text


def test_pick_school_filter_skips_cipher_id() -> None:
    from src.agent.education.contribution_weak import pick_school_filter_token

    assert (
        pick_school_filter_token(
            named="",
            arg="GZ_E62620B2",
            bound_name="",
            looked_up=["B04安宜中学"],
        )
        == "B04安宜中学"
    )
    assert (
        pick_school_filter_token(named="扬州中学", arg="GZ_X", bound_name="")
        == "扬州中学"
    )
    assert pick_school_filter_token(
        named="", arg="GZ_E62620B2", bound_name="", looked_up=[]
    ) == ""


def test_pick_class_prefers_supplement() -> None:
    from src.agent.education.contribution_weak import pick_class_filter_token
    from src.agent.education.query_parse import extract_class_target

    q = "2026届高三3月 高三(11)班贡献分薄弱学科。补充：班级=高三(10)班"
    assert extract_class_target(q) == "高三(11)班"
    assert pick_class_filter_token(q, "高三(11)班") == "高三(10)班"
    items = build_fact_query_plan_items(q)
    assert "class_name='高三(10)班'" in items[0]["sub_task"]
    assert "class_name='高三(11)班'" not in items[0]["sub_task"]


def test_planner_calls_contribution_weak_tool() -> None:
    items = build_fact_query_plan_items("安宜中学3月贡献分薄弱学科")
    assert items
    assert "query_contribution_weak_tool" in items[0]["sub_task"]
    assert "execute_sql" in items[0]["sub_task"]
    no_name = build_fact_query_plan_items("2026届高三3月 贡献分薄弱学科")
    assert "query_contribution_weak_tool" in no_name[0]["sub_task"]
    assert "GZ_" not in no_name[0]["sub_task"]


def test_apply_verdict_pins_tool_output() -> None:
    verdict = "安宜中学（引领校）按贡献分口径的薄弱学科\n物理类：\n- 211线：语文（差4）。"
    out = apply_contribution_weak_verdict(
        "模型胡写对照池后50%是数学。",
        "安宜中学贡献分薄弱学科",
        [{"tool": "query_contribution_weak_tool", "data": {"verdict": verdict}}],
    )
    assert out == verdict


def test_apply_verdict_drops_llm_failure_preamble() -> None:
    verdict = "B04安宜中学（支撑校）按贡献分口径的薄弱学科\n物理类：\n- 特控线：化学（差6.18）。"
    out = apply_contribution_weak_verdict(
        "未能完成本次查询，未取得「2026届高三3月」的贡献分薄弱学科结果。",
        "2026届高三3月 贡献分薄弱学科",
        [{"tool": "query_contribution_weak_tool", "data": {"verdict": verdict}}],
    )
    assert out == verdict
    assert "未能完成" not in out


def test_apply_verdict_skips_first_missing_school() -> None:
    verdict = "B04安宜中学（支撑校）按贡献分口径的薄弱学科\n物理类：\n- 特控线：化学（差6.18）。"
    out = apply_contribution_weak_verdict(
        "该查询本轮未执行成功，结论未产出，不是「库中无数据」。",
        "2026届高三3月 贡献分薄弱学科",
        [
            {
                "tool": "query_contribution_weak_tool",
                "data": {"error": "missing_school"},
                "content": "贡献分薄弱学科需要具体学校。局端请点名学校；校长/教师用绑定本校。",
            },
            {
                "tool": "query_contribution_weak_tool",
                "data": {"verdict": verdict},
            },
        ],
    )
    assert out == verdict
    assert "未执行成功" not in out
    assert "需要具体学校" not in out
