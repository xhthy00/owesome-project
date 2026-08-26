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
    # 压缩：只保留关键规则行 + 示例题干
    rules: list[str] = []
    if "AVG(reach_rate)" in term or "达线" in term:
        rules.append(
            "达线：区县/全市查 tb_score_indicator，SUM(reached_count)/SUM(candidates)；"
            "禁止 AVG(reach_rate)；禁止 district='月…区'；先 peek_edu_filter_values。"
        )
    if "zf3m" in term or "zf6m" in term:
        rules.append("三门/六门均分用 tb_score_overview.zf3m/zf6m，禁止对 tb_score 三科 AVG 当三门均分。")
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
