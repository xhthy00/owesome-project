"""LinearDAG：一条线性执行链。

说明：生产 **Team** 编排已迁至 LangGraph（见 ``src/chat/service/team_graph/``）；
本模块仅保留线性 MapOperator 冒烟与历史兼容，不作为线上入口。

Phase A 的 DAG 只做最简的串行调度：
- 节点之间是 ``A.output -> B.input`` 的纯函数流；
- 单节点异常直接冒泡，整个 DAG 失败（上层负责重试/降级）；
- 不做持久化、不做可视化编译——那些留给 Phase C。
"""

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

from src.agent.awel.operator import MapOperator

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LinearDAG(Generic[T]):
    """按注册顺序执行一串 MapOperator。"""

    def __init__(self, operators: list[MapOperator[Any, Any]], name: str = "LinearDAG") -> None:
        if not operators:
            raise ValueError("LinearDAG requires at least one operator")
        self.operators = list(operators)
        self.name = name

    async def execute(self, input_value: T) -> Any:
        current: Any = input_value
        for op in self.operators:
            logger.debug("[%s] -> %s", self.name, op.name)
            current = await op.map(current)
        return current

    def __repr__(self) -> str:
        chain = " >> ".join(op.name for op in self.operators)
        return f"LinearDAG({chain})"
