"""分数线带（线上/线下 N 分）口径提示。只生成文案，不改 SQL / 路由。"""

from __future__ import annotations

# 与旧 prompt_context 触发对齐，仅多认「线下」，避免均分/排名题被塞线带规则。
_LINE_OFFSET_TRIGGERS = ("线上", "线下", "特招", "特控", "达线")

# 不含 '<'，可直接塞进 sql_gen_prompt 的 XML description。
LINE_OFFSET_HINT = (
    "分数线带按修饰语定区间，达线本身仍是 zf6m≥该线，与「线上N分」不是同一口径。"
    "光秃「线上N分」/达到线上N分/线上N分及以上/线上N分以上 = zf6m≥线+N"
    "（特控496则线上15分是 ≥511）；"
    "超过线上N分 = zf6m大于线+N；"
    "线上N分以内/及以内/包括N分 = 线≤zf6m≤线+N（[496,511]）；"
    "线上不足N分/不到N分/不含N分 = 线≤zf6m且小于线+N；"
    "「线下」无N = zf6m小于该线；"
    "线下N分以内 = 线-N≤zf6m≤线；"
    "距线/线上下N分以内 = |zf6m-线|≤N。"
    "「特招线线上N分以内」= 特招线之上 0～N 分，不要拆成「特招线」+「线上N分(≥线+N)」。"
    "有以内/不足/包括时必须用区间，禁止再改成 ≥线+N。"
    "临界生是及格线±margin，不要把线带区间叫成压线/临界。"
    "结论须写明实际区间。"
)


def needs_line_offset_hint(question: str) -> bool:
    q = str(question or "")
    return any(h in q for h in _LINE_OFFSET_TRIGGERS)


def line_offset_hint(question: str | None = None) -> str:
    """无问句时返回口径表；有问句则仅在触及线带/特招/达线时返回。"""
    if question is None:
        return LINE_OFFSET_HINT
    return LINE_OFFSET_HINT if needs_line_offset_hint(question) else ""


__all__ = [
    "LINE_OFFSET_HINT",
    "line_offset_hint",
    "needs_line_offset_hint",
]
