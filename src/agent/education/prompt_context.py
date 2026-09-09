"""教育场景 Prompt 增强——场景识别与 few-shot/术语注入。"""

from __future__ import annotations

from src.templates.sql_gen_prompt import (
    education_sql_training_block_for_intent,
    education_terminologies_block_for_intent,
    resolve_edu_sql_intent,
)

# 与 planner / orchestrator 关键词对齐
_EDUCATION_KEYWORDS = (
    "学情",
    "成绩",
    "考试",
    "班级",
    "年级",
    "科目",
    "学科",
    "均衡",
    "标准差",
    "数学",
    "语文",
    "英语",
    "物理",
    "化学",
    "生物",
    "政治",
    "历史",
    "地理",
    "学生",
    "学号",
    "小题",
    "逐题",
    "及格率",
    "优秀率",
    "分数段",
    "排名",
    "报告",
    "诊断",
    "知识点",
    "学校",
    "中学",
    "达线",
    "预测线",
    "特控",
    "本科线",
    "十分段",
    "10分段",
    "五分段",
    "5分段",
)


def is_education_question(question: str) -> bool:
    """判断问题是否属于教育学情/成绩分析场景。"""
    q = (question or "").strip()
    if not q:
        return False
    return any(kw in q for kw in _EDUCATION_KEYWORDS)


def build_education_prompt_extras(question: str = "") -> tuple[str, str]:
    """按问句意图返回精简 (terminologies, data_training)，供 SQL 生成注入。

    无 question 时回落 default 意图小包（兼容旧调用）。
    """
    intent = resolve_edu_sql_intent(question or "")
    return (
        education_terminologies_block_for_intent(intent),
        education_sql_training_block_for_intent(intent),
    )


def build_education_sql_hint_text(question: str) -> str:
    """给 Team/DataAnalyst 的短提示（非整包 XML）。"""
    intent = resolve_edu_sql_intent(question or "")
    term, training = build_education_prompt_extras(question)
    q = question or ""
    # 压缩：只保留关键规则行 + 示例题干
    rules: list[str] = []
    from src.agent.education.query_parse import (
        extract_school_type_target,
        is_citywide_nth_class_lookup_query,
        is_class_city_rank_query,
        is_class_subject_city_rank_query,
        is_school_class_comparison_query,
        is_school_vs_school_type_avg_query,
        is_subject_strength_query,
    )

    st = extract_school_type_target(q)
    type_pool = (
        f"问句点名{st}校时对照池必须 xxlb LIKE '%{st}%'，禁止全市普通高中混排。"
        if st
        else ""
    )
    if is_class_city_rank_query(q):
        rules.append(
            "班级全市六门排名必须一条 SQL 用 UNION ALL 一次返回两个口径："
            "按选考方向（人数多的一类，该类满3人进池）+ 不区分选考；"
            "禁止只报文理混排，禁止拆两次 execute_sql。"
            "每个口径返回第1名+目标班前后各3名（标记本班），禁止只查出目标班一行。"
            "排名池排除其他校（xxlb NOT LIKE '%其他%'）和整班不足10人的小班。"
            f"{type_pool}"
            "物理类 xkkm LIKE '物%'，历史类 LIKE '史%' OR LIKE '历%'；"
            "GROUP BY xx,bj 后 RANK() OVER (ORDER BY 均分 DESC)，"
            "用学校 xx 与班级 bj 定位窗口。"
        )
    if is_citywide_nth_class_lookup_query(q):
        rules.append(
            "全市第N是哪个班：没有目标班；洗净其他校（xxlb NOT LIKE '%其他%'）和整班不足10人；"
            "该科 FILTER col>0 且 HAVING 有效人数>=3；RANK 与 COUNT(*) OVER() 在进池后同一 SELECT；"
            "外层 WHERE 全市排名=N，禁止第1名+前后3名窗口，禁止外层滤已知班；"
            "化学/生物/政治/地理用 hxzh/swzh/zzzh/dlzh，禁止把行政班总数当再选科分母。"
        )
    if is_class_subject_city_rank_query(q):
        rules.append(
            "班级单科全市排名：洗净其他校（xxlb NOT LIKE '%其他%'）和整班不足10人的小班；"
            f"{type_pool}"
            "该科 FILTER col>0 且 HAVING 有效人数>=3；RANK() OVER (ORDER BY 均分 DESC NULLS LAST)；"
            "查询结果返回第1名+目标班前后各3名并标记本班，禁止只查出目标班一行；"
            "用 xx LIKE '%校名%' 与 bj 定位窗口；禁止 UNION ALL 文理双行；"
            "名次/总数≤25% 称前列，禁止称中上段；结论禁止字段名与 A01 校码。"
        )

    if is_subject_strength_query(q):
        rules.append(
            "优势/薄弱学科：按该校（有班则该班）各科均分的全市排名相对位置判断，"
            "名次/参赛数≤25%为前列（优势），≥50%为靠后（薄弱），中间为中游；"
            "禁止把本校各科里名次较差的直接叫薄弱；禁止用本校各科均分互比；"
            "GROUP BY xx（班级再加 bj）后 RANK() OVER (ORDER BY 均分 DESC NULLS LAST) "
            "与 COUNT(*) OVER()，各科须先 WHERE 均分 IS NOT NULL（或 >0）；"
            "禁止 PARTITION BY bj，禁止 COUNT(DISTINCT bj)，禁止空均分校进参赛池，"
            "xsxz='在籍生'，AVG FILTER col>0；化学/生物/政治/地理用 hxzh/swzh/zzzh/dlzh；"
            "最终结果一科一行（学科/均分/全市排名/参赛数），禁止语文均分+语文排名并排宽表。"
        )
    if is_school_class_comparison_query(q):
        rules.append(
            "班级横向对比：班级列是 bj 不是 xx；SELECT bj AS class_name，GROUP BY xx, bj；"
            "禁止 SELECT xx AS class_name，禁止只 GROUP BY xx（会把全校塌成一行）。"
            "小题为空时仍用学生行按班聚合，禁止手写学校级 AVG。"
        )
    if is_school_vs_school_type_avg_query(q):
        rules.append(
            "学校 vs 引领/支撑/发展校均分或单科：查 tb_score_overview；"
            "校类用 xxlb LIKE '%引领%' 且 xsxz=在籍生；语文=yw 且 yw > 0（缺考 0 分不计入）；"
            "禁止 JOIN tb_school 算学生均分；禁止 GROUP BY xx 再平均。"
        )
    if not is_school_vs_school_type_avg_query(q) and (
        "AVG(reach_rate)" in term or "达线" in term
    ):
        rules.append(
            "达线：区县/全市查 tb_score_indicator，SUM(reached_count)/SUM(candidates)；"
            "点名学校用 school_name LIKE '%校名%'，禁止 school_id='GZ_…'；"
            "引领/支撑/发展校 JOIN tb_school，sch.type LIKE '%引领%'（与 overview.xxlb 同源）；"
            "禁止套用全市达线报告；禁止 AVG(reach_rate)；先 peek_edu_filter_values。"
        )
    if "zf3m" in term or "zf6m" in term:
        rules.append("三门/六门均分用 tb_score_overview.zf3m/zf6m，禁止对 tb_score 三科 AVG 当三门均分。")
    rules.append(
        "凡单科分数统计（均分/标准差/均衡性/中位/最值/分数段）必须排除未选考/缺考 0 分："
        "AVG/STDDEV_SAMP(col) FILTER (WHERE col > 0)；禁止 AVG(ls/zz/dl) 或 STDDEV(ls) 除以全体人数。"
    )
    rules.append(
        "查 tb_score_overview 默认 AND xsxz='在籍生'：市报生/往届不进均分、不进全市班级或学校排名。"
    )
    rules.append(
        "选科方向只用 xkkm：物理类 LIKE '物%'，历史类 LIKE '史%' OR LIKE '历%'；"
        "禁止 xkqk IN ('134','135','136') 分轨（xkqk 常为空，IN 会少人）；"
        "跨考试 JOIN 用 anon_stu_id，有 zf6m 即算参考，不要要求两侧 xkqk 都有值。"
    )
    if any(h in q for h in ("线上", "特招", "特控", "达线")):
        rules.append(
            "「线上N分」= zf6m ≥ 该方向分数线 + N（特控496则线上15分是 ≥511）；"
            "禁止写成 [线, 线+N] 闭区间（那是压线/临界，不是线上N分）。"
        )
    if "hxzh" in term or "十分段" in term or "分以上" in term:
        rules.append(
            "绝对分数段查 tb_score_overview；化学用 hxzh；十分段 ((zf6m-1)/10)*10+1。"
        )
    if "peek" not in " ".join(rules).lower():
        rules.append("写 district/exam_name 过滤前先 peek_edu_filter_values；空结果禁止断言缺数。")
    import re

    qs = re.findall(r"<question>(.*?)</question>", training, re.DOTALL)
    ex = "；".join(q.strip() for q in qs[:3] if q.strip())
    return (
        f"【教育 SQL 提示 intent={intent}】"
        + " ".join(rules)
        + (f" 参考问法：{ex}" if ex else "")
    )


__all__ = [
    "build_education_prompt_extras",
    "build_education_sql_hint_text",
    "is_education_question",
]
