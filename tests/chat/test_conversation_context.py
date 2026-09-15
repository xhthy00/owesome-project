import asyncio
from types import SimpleNamespace

from src.chat.service import conversation_context as context


def test_context_filters_failed_and_pending_turns() -> None:
    records = [
        SimpleNamespace(
            id=1,
            question="高三(1)班期中数学成绩",
            resolved_question=None,
            context_summary="已生成数学成绩概览",
            summary="<html>long report</html>",
            sql_answer=None,
            reasoning=None,
            exec_result=None,
            is_success=True,
        ),
        SimpleNamespace(
            id=2,
            question="失败问题",
            resolved_question=None,
            context_summary="不应出现",
            summary=None,
            sql_answer=None,
            reasoning=None,
            exec_result=None,
            is_success=False,
        ),
    ]

    result = context.context_from_records(records)

    assert result.latest_record_id == 1
    assert "数学成绩概览" in result.brief
    assert "不应出现" not in result.brief
    assert "<html>" not in result.brief


def test_merge_inherited_slots_honors_reset() -> None:
    inherited = {"exam_name": "期中考试", "class_name": "高三(1)班"}

    result = context.merge_inherited_slots({}, inherited, "换个考试，再看数学")

    assert "exam_name" not in result
    assert result["class_name"] == "高三(1)班"


def test_standalone_question_does_not_load_history(monkeypatch) -> None:
    def fail_if_loaded(*args, **kwargs):
        raise AssertionError("完整新问题不应加载历史")

    monkeypatch.setattr(context, "load_turn_context", fail_if_loaded)
    result = asyncio.run(
        context.resolve_turn_context(
            question="查询扬州中学高三(2)班期末数学平均分",
            conversation_id=10,
            user_id=1,
        )
    )

    assert result.turn_type == context.TURN_STANDALONE
    assert result.resolved_question == "查询扬州中学高三(2)班期末数学平均分"
    assert not result.context_capsule


def test_follow_up_is_rewritten_to_one_current_task(monkeypatch) -> None:
    monkeypatch.setattr(
        context,
        "load_turn_context",
        lambda *args, **kwargs: context.TurnContext(
            inherited={"exam_name": "期中考试", "class_name": "高三(1)班"},
            brief="用户：查询高三(1)班期中语文均分\n助手：均分为 110 分",
            latest_record_id=7,
        ),
    )

    result = asyncio.run(
        context.resolve_turn_context(
            question="那数学呢",
            conversation_id=10,
            user_id=1,
        )
    )

    assert result.turn_type == context.TURN_FOLLOW_UP
    assert result.parent_record_id == 7
    assert result.resolved_question.startswith("那数学呢")
    assert "考试=期中考试" in result.resolved_question
    assert "班级=高三(1)班" in result.resolved_question
    assert not result.context_capsule


def test_short_question_without_history_is_standalone(monkeypatch) -> None:
    monkeypatch.setattr(context, "load_turn_context", lambda *args, **kwargs: context.TurnContext())

    result = asyncio.run(
        context.resolve_turn_context(
            question="有多少用户",
            conversation_id=None,
            user_id=1,
        )
    )

    assert result.turn_type == context.TURN_STANDALONE


def test_retry_never_loads_session_history(monkeypatch) -> None:
    def fail_if_loaded(*args, **kwargs):
        raise AssertionError("retry 不得加载会话级历史")

    monkeypatch.setattr(context, "load_turn_context", fail_if_loaded)
    result = asyncio.run(
        context.resolve_turn_context(
            question="期中考试",
            conversation_id=10,
            user_id=1,
            is_retry_chat=True,
            parent_record_id=9,
        )
    )

    assert result.turn_type == context.TURN_RETRY
    assert result.parent_record_id == 9
    assert not result.context_capsule


def test_exam_anaphora_variants_are_follow_ups() -> None:
    for reference in (
        "这次考试",
        "本场考试",
        "刚才的考试",
        "前面提到的那场考试",
        "上述考试",
        "同一场考试",
    ):
        q = f"{reference}扬州中学清北达线人数是多少"
        assert context.is_follow_up_question(q), reference


def test_explicit_exam_name_is_not_mistaken_for_anaphora() -> None:
    q = "这次改查2026届高三3月扬州中学清北达线人数"
    assert not context.is_follow_up_question(q)


def test_this_exam_follow_up_inherits_previous_exam(monkeypatch) -> None:
    records = [
        SimpleNamespace(
            id=1586,
            question="2026届高三1月全市达线情况分析",
            resolved_question="2026届高三1月全市达线情况分析。补充：考试=2026届高三1月期末",
            context_summary="全市达线分析",
            summary="全市达线分析",
            sql_answer=None,
            reasoning=None,
            exec_result=None,
            is_success=True,
        )
    ]
    turn_ctx = context.context_from_records(records)
    assert turn_ctx.inherited.get("exam_name") == "2026届高三1月期末"

    monkeypatch.setattr(context, "load_turn_context", lambda *args, **kwargs: turn_ctx)
    result = asyncio.run(
        context.resolve_turn_context(
            question="这次考试扬州中学清北达线人数是多少",
            conversation_id=721,
            user_id=1,
        )
    )

    assert result.turn_type == context.TURN_FOLLOW_UP
    assert "考试=2026届高三1月期末" in result.resolved_question


def test_upstream_supplemented_follow_up_is_not_rewritten_again(monkeypatch) -> None:
    turn_ctx = context.TurnContext(
        inherited={"exam_name": "2026届高三1月期末"},
        brief="用户：2026届高三1月全市达线情况分析",
        latest_record_id=1594,
    )
    monkeypatch.setattr(context, "load_turn_context", lambda *args, **kwargs: turn_ctx)

    class FailIfCalled:
        async def chat(self, _messages):
            raise AssertionError("已补齐槽位的问题不应再次调用 LLM 改写")

    current = (
        "这次考试扬州中学的清北达线人数是多少。"
        "补充：考试=2026届高三1月期末"
    )
    result = asyncio.run(
        context.resolve_turn_context(
            question=current,
            original_question="这次考试扬州中学的清北达线人数是多少",
            conversation_id=722,
            user_id=1,
            llm_client=FailIfCalled(),
        )
    )

    assert result.turn_type == context.TURN_FOLLOW_UP
    assert result.resolved_question == current


def test_follow_up_rewrite_strips_minimax_think_block(monkeypatch) -> None:
    turn_ctx = context.TurnContext(
        brief="用户：扬州中学达线人数\n助手：已查询",
        latest_record_id=1594,
    )
    monkeypatch.setattr(context, "load_turn_context", lambda *args, **kwargs: turn_ctx)

    class MiniMaxLikeLlm:
        async def chat(self, _messages):
            return (
                "<think>这里是改写过程，不应进入最终问题。</think>\n"
                "查询扬州中学清北达线人数"
            )

    result = asyncio.run(
        context.resolve_turn_context(
            question="那清北线呢",
            conversation_id=722,
            user_id=1,
            llm_client=MiniMaxLikeLlm(),
        )
    )

    assert result.resolved_question == "查询扬州中学清北达线人数"
    assert "<think>" not in result.resolved_question


def test_complete_roster_request_is_follow_up(monkeypatch) -> None:
    monkeypatch.setattr(
        context,
        "load_turn_context",
        lambda *args, **kwargs: context.TurnContext(
            inherited={"exam_name": "2026届高三5月模拟"},
            brief="用户：全市理科前150名但是有单科在150名之外的学生数据\n助手：入围155人",
            latest_record_id=2171,
        ),
    )
    result = asyncio.run(
        context.resolve_turn_context(
            question="需 155 人完整名单",
            conversation_id=955,
            user_id=1,
        )
    )
    assert result.turn_type == context.TURN_FOLLOW_UP
    assert "完整名单" in result.resolved_question
    assert "考试=2026届高三5月模拟" in result.resolved_question
