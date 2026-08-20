"""教育场景 Prompt 增强——场景识别与 few-shot/术语注入。"""

from __future__ import annotations

from src.templates.sql_gen_prompt import (
    education_sql_training_block,
    education_terminologies_block,
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


def build_education_prompt_extras() -> tuple[str, str]:
    """返回 (terminologies, data_training) 供 SQL 生成 prompt 注入。"""
    return education_terminologies_block(), education_sql_training_block()


__all__ = [
    "build_education_prompt_extras",
    "is_education_question",
]
