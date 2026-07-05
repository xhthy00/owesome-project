"""教育场景 prompt_context 单元测试。"""

from __future__ import annotations

from src.agent.education.prompt_context import (
    build_education_prompt_extras,
    is_education_question,
)
from src.templates.sql_gen_prompt import education_sql_training_block


def test_is_education_question_positive():
    assert is_education_question("南京市第一中学高一(1)班数学平均分")
    assert is_education_question("生成班级学情报告")


def test_is_education_question_negative():
    assert not is_education_question("查询用户总数")
    assert not is_education_question("")


def test_education_sql_training_contains_tb_score():
    block = education_sql_training_block()
    assert "tb_score" in block
    assert "exam_score * 0.6" in block


def test_build_education_prompt_extras():
    term, training = build_education_prompt_extras()
    assert "<terminologies>" in term
    assert "tb_exam.exam_score" in term
    assert "tb_score_detail" in training
