"""DataAnalystAgent 真实 LLM 冒烟 CLI。

用途：不起 FastAPI / 不用前端，直接用真实 LLM + 真实数据库，观察 ReAct 回路
的完整轨迹。特别适合在接入生产前快速回答以下问题：

    - LLM 能否稳定产出我们约定的 JSON 协议？
    - 每一轮思考 → 工具调用 → 观察的耗时分布如何？
    - SQL 失败时 Agent 是否真能在下一轮自愈？

用法::

    # 最简
    python -m src.agent.smoke --datasource-id 1 "本月订单最多的前三名用户是谁"

    # 指定用户、调上限、改 LLM 模型（走现有 .env 配置）
    python -m src.agent.smoke -d 1 -u 7 --max-rounds 12 "..."

    # 打印完整 observation 而非截断
    python -m src.agent.smoke -d 1 --full "..."

退出码：
    0 = agent 正常终止（调用了 terminate）
    1 = agent 未正常终止 / 抛异常
    2 = 参数错误
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

__all__ = ["main"]

_COLORS = {
    "agent_thought": "\033[36m",
    "tool_call": "\033[33m",
    "tool_result": "\033[32m",
    "sql": "\033[35m",
    "result": "\033[35m",
    "final_answer": "\033[1;32m",
    "error": "\033[1;31m",
}
_RESET = "\033[0m"


def _fmt(event: str, text: str, elapsed_ms: int) -> str:
    color = _COLORS.get(event, "")
    return f"{color}[{event:<14s}]{_RESET} (+{elapsed_ms:>5d}ms) {text}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [+{len(text) - limit} chars]"


def _render_payload(event: str, data: dict[str, Any], full: bool) -> str:
    limit = 2000 if full else 240

    if event == "agent_thought":
        text = str(data.get("text") or "")
        return f"round={data.get('round')} text={_truncate(text, limit)}"

    if event == "tool_call":
        tool = data.get("tool")
        args = data.get("args") or {}
        thought = str(data.get("thought") or "")
        return (
            f"round={data.get('round')} tool={tool} args={args} "
            f"thought={_truncate(thought, 160)}"
        )

    if event == "tool_result":
        tool = data.get("tool")
        success = data.get("success")
        content = str(data.get("content") or "")
        return (
            f"round={data.get('round')} tool={tool} success={success} "
            f"elapsed={data.get('elapsed_ms')}ms content={_truncate(content, limit)}"
        )

    if event == "sql":
        return f"sql={data.get('sql')}"

    if event == "result":
        cols = data.get("columns") or []
        rows = data.get("rows") or []
        return f"columns={cols} row_count={data.get('row_count', len(rows))}"

    if event == "final_answer":
        return _truncate(str(data.get("text") or ""), limit)

    if event == "error":
        return f"error={data.get('error')}"

    return str(data)


async def _run(
    question: str,
    datasource_id: int,
    user_id: int,
    max_rounds: int | None,
    full: bool,
) -> int:
    from langgraph.checkpoint.memory import InMemorySaver

    from src.chat.schemas import ChatRequest
    from src.chat.service.deepagents_runtime import run_chat_stream

    req = ChatRequest(question=question, datasource_id=datasource_id)

    wall_t0 = time.time()
    event_t0 = {"t": wall_t0}
    terminated_ok = {"ok": False}

    async def emit(event: str, data: dict[str, Any]) -> None:
        now = time.time()
        elapsed_ms = int((now - event_t0["t"]) * 1000)
        event_t0["t"] = now
        print(_fmt(event, _render_payload(event, data, full), elapsed_ms), flush=True)
        if event == "error" or (event == "step" and data.get("name") == "deepagents" and data.get("status") == "error"):
            terminated_ok["failed"] = True
        if event == "summary" and data.get("content"):
            terminated_ok["ok"] = True

    print(f"→ question   : {question}")
    print(f"→ datasource : {datasource_id}  user: {user_id}")
    print(f"→ max_rounds : {max_rounds or 'default'}")
    print("-" * 80)

    try:
        await run_chat_stream(
            request=req,
            current_user_id=user_id,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            max_model_calls=max_rounds,
        )
    except KeyboardInterrupt:
        print("\n[interrupted]", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"\n[fatal] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    total = time.time() - wall_t0
    print("-" * 80)
    print(f"total elapsed: {total:.2f}s  terminated_ok={terminated_ok['ok']}")
    return 0 if terminated_ok["ok"] and not terminated_ok.get("failed") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.agent.smoke",
        description="Smoke-test DeepAgents against a real LLM / datasource.",
    )
    parser.add_argument("question", nargs="+", help="Natural-language question (quoted)")
    parser.add_argument("-d", "--datasource-id", type=int, required=True)
    parser.add_argument("-u", "--user-id", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=None, help="Limit model and tool rounds")
    parser.add_argument("--full", action="store_true", help="Do not truncate content/thought")
    args = parser.parse_args(argv)
    if args.max_rounds is not None and args.max_rounds < 1:
        parser.error("--max-rounds 必须大于 0")

    question = " ".join(args.question).strip()
    if not question:
        parser.error("question cannot be empty")

    return asyncio.run(
        _run(
            question=question,
            datasource_id=args.datasource_id,
            user_id=args.user_id,
            max_rounds=args.max_rounds,
            full=args.full,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
