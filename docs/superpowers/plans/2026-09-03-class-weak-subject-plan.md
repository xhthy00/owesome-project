# 班级薄弱学科诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 口语问「某班薄弱学科」时，系统用同校同年级同科班际对比选出薄弱科，再按需下钻小题并给建议；不被班级总览的各科互比抢走。

**Architecture:** 仿知识点分层对比：`is_class_weak_subject_query` 硬路由 + 1 步 ToolExpert 计划 + 纯函数模块 `class_weak_subject.py` + 一个取数工具。不新增 `ReportType`，不改班级总览未命中时的行为。

**Tech Stack:** Python 3.11, pytest, 现有 education 取数（`_diagnosis_where_clause_pair` / `_fetch_subject_diagnosis_rows`）。

**Spec:** `docs/superpowers/specs/2026-09-03-class-weak-subject-design.md`

**Commit 约定：** 本仓库用户规则是未明确要求不要 commit。执行各 Task 时**跳过 Commit 步**，除非用户另行要求。

**验收问句（不得改写）：**

```
2026届高三1月期末考试，请分析一下学校B11仙城中学高三(1)班的薄弱学科，然后看薄弱学科的具体题目，再给出建议
```

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/agent/education/class_weak_subject.py` | 新建：班际对比、A 规则、小题差、HTML |
| `src/agent/education/query_parse.py` | 探测器；校名模式兼容 `B11仙城中学` |
| `src/agent/education/intent_router.py` | 硬路由、plan 拦截、coerce |
| `src/agent/expand/planner.py` | `build_class_weak_subject_plan_items` |
| `src/agent/education/tools.py` | 工具取数包装并注册 |
| `tests/agent/test_class_weak_subject.py` | 新建：探测器 + 纯函数 + 路由 |

**禁止修改：** `class_overview` 雷达 / `SUBJECT_BREAKDOWN` 各科互比逻辑；`stats.compute_imbalance_degree`；科目诊断默认三步（仅被本探测器截走）。

---

## Task 1: 探测器 + 校名抽取

**Files:**
- Modify: `src/agent/education/query_parse.py`
- Test: `tests/agent/test_class_weak_subject.py`

- [ ] **Step 1: 写失败测试**

Create `tests/agent/test_class_weak_subject.py`:

```python
from src.agent.education.query_parse import (
    extract_school_target,
    is_class_weak_subject_query,
)

GOLDEN = (
    "2026届高三1月期末考试，请分析一下学校B11仙城中学高三(1)班的薄弱学科，"
    "然后看薄弱学科的具体题目，再给出建议"
)


def test_golden_query_is_class_weak_subject():
    assert is_class_weak_subject_query(GOLDEN) is True


def test_golden_query_extracts_school_and_keeps_code_prefix():
    name = extract_school_target(GOLDEN)
    assert name is not None
    assert "仙城中学" in name
    assert "B11" in name


def test_class_overview_not_weak_subject():
    assert is_class_weak_subject_query(
        "B11仙城中学高三(1)班2026届高三1月期末班级总览"
    ) is False


def test_weak_knowledge_without_subject_word_not_hit():
    assert is_class_weak_subject_query(
        "仙城中学高三(1)班数学薄弱知识点"
    ) is False


def test_grade_comparison_not_hit():
    assert is_class_weak_subject_query(
        "扬州中学各班数学横向对比"
    ) is False


def test_no_class_name_not_hit():
    assert is_class_weak_subject_query(
        "请分析仙城中学的薄弱学科"
    ) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_class_weak_subject.py -v
```

Expected: FAIL（`is_class_weak_subject_query` 未定义）。

- [ ] **Step 3: 校名模式兼容 `B11仙城中学`**

In `src/agent/education/query_parse.py`, change the third `_SCHOOL_PATTERNS` entry from Chinese-only to optional 校码前缀：

```python
    re.compile(
        rf"(?<![\u4e00-\u9fff])((?:[A-Za-z]\d{{2}})?[\u4e00-\u9fff]{{2,8}}{_SCHOOL_SUFFIX}){_SCHOOL_TRAIL}"
    ),
```

只改这一条，不要动另外两条。

- [ ] **Step 4: 实现探测器**

放在 `is_knowledge_cohort_gap_query` 附近。班级名用与 `is_school_class_comparison_query` 相同的正则，避免 `query_parse` import `orchestrator` 循环依赖。

```python
_CLASS_WEAK_SUBJECT_HINTS = (
    "薄弱学科",
    "薄弱科目",
    "学科薄弱",
    "科目薄弱",
)
_NAMED_CLASS_RE = re.compile(
    r"高[一二三]\(\d+\)班|"
    r"(?:初三|初二|初一|高三|高二|高一|九年级|八年级|七年级)[\d班]*\d?班"
)


def is_class_weak_subject_query(question: str) -> bool:
    """指定班级的薄弱学科（同校同科班际对比），不是各科互比、不是薄弱知识点。"""
    q = (question or "").strip()
    if not q:
        return False
    if not any(h in q for h in _CLASS_WEAK_SUBJECT_HINTS):
        return False
    if not _NAMED_CLASS_RE.search(q):
        return False
    if is_citywide_analysis_query(q) or is_individual_student_analysis_query(q):
        return False
    if is_subject_research_report_query(q):
        return False
    if is_school_class_comparison_query(q):
        return False
    return True
```

把 `is_class_weak_subject_query` 加入文件末尾 `__all__`（紧挨 `is_knowledge_cohort_gap_query`）。

注意：`is_school_class_comparison_query` 在有具名班级且无「各班/横向/对比/排名」时已经返回 False，验收句不会被它挡住。

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run pytest tests/agent/test_class_weak_subject.py -v
```

Expected: PASS。若 `B11` 仍抽不到，先检查第三条 pattern 是否保存成功，再查 `extract_school_targets` 是否被 filler 截断。

- [ ] **Step 6: Commit（本仓库默认跳过）**

```bash
git add src/agent/education/query_parse.py tests/agent/test_class_weak_subject.py
git commit -m "feat(edu): detect class weak-subject queries"
```

---

## Task 2: 班际对比 + A 规则

**Files:**
- Create: `src/agent/education/class_weak_subject.py`
- Test: `tests/agent/test_class_weak_subject.py`

- [ ] **Step 1: 写失败测试（追加到同一测试文件）**

```python
from src.agent.education.class_weak_subject import (
    DRILL_TOP_N,
    compare_class_subjects_vs_peers,
    identify_weak_subjects,
    pick_drill_subjects,
)


def _row(cls, subject, score, sid):
    return {
        "class": cls,
        "class_name": cls,
        "subject": subject,
        "score": score,
        "student_id": sid,
    }


def _ten_class_math_rows(class_avgs: dict[int, float]):
    """class_avgs: 班号 → 均分；每班 3 人同分，避免并列名次搅乱断言。"""
    rows = []
    for c, avg in class_avgs.items():
        for i in range(3):
            rows.append(_row(f"高三({c})班", "数学", avg, f"c{c}_{i}"))
    return rows


def test_rank_10_of_10_is_weak():
    avgs = {1: 91.0}
    avgs.update({c: 100.0 + c for c in range(2, 11)})  # 102..110，全部高于 1 班
    comps = compare_class_subjects_vs_peers(
        _ten_class_math_rows(avgs), class_name="高三(1)班"
    )
    math = next(c for c in comps if c["subject"] == "数学")
    assert math["rank"] == 10
    assert math["total_classes"] == 10
    weak = identify_weak_subjects(comps)
    assert [w["subject"] for w in weak] == ["数学"]


def test_mid_rank_with_gap_6_is_weak():
    # 让目标班均分 = 104，9 个对照班 = 110 → 分差 6，名次约第 10？
    # 要第 5 名：4 个班高于目标、5 个低于。
    rows = []
    for i in range(3):
        rows.append(_row("高三(1)班", "数学", 104.0, f"t{i}"))
    avgs = [120, 118, 116, 114, 100, 99, 98, 97, 96]  # 4 高 5 低 → 第 5
    for idx, avg in enumerate(avgs, start=2):
        for i in range(3):
            rows.append(_row(f"高三({idx})班", "数学", avg, f"c{idx}_{i}"))
    comps = compare_class_subjects_vs_peers(rows, class_name="高三(1)班")
    math = next(c for c in comps if c["subject"] == "数学")
    assert math["rank"] == 5
    assert math["avg_gap"] >= 5
    weak = identify_weak_subjects(comps)
    assert "数学" in [w["subject"] for w in weak]


def test_rank_4_gap_2_not_weak():
    rows = []
    for i in range(3):
        rows.append(_row("高三(1)班", "数学", 108.0, f"t{i}"))
    avgs = [112, 111, 110, 107, 106, 105, 104, 103, 102]  # 3 高 → 第 4，对照班均约 106.7，分差 < 5
    for idx, avg in enumerate(avgs, start=2):
        for i in range(3):
            rows.append(_row(f"高三({idx})班", "数学", avg, f"c{idx}_{i}"))
    comps = compare_class_subjects_vs_peers(rows, class_name="高三(1)班")
    math = next(c for c in comps if c["subject"] == "数学")
    assert math["rank"] == 4
    assert math["avg_gap"] < 5
    weak = identify_weak_subjects(comps)
    assert weak == []


def test_physics_4_classes_last3_not_applied():
    rows = []
    for i in range(3):
        rows.append(_row("高三(1)班", "物理", 90.0, f"t{i}"))
    for c, avg in ((2, 100.0), (3, 95.0), (4, 92.0)):
        for i in range(3):
            rows.append(_row(f"高三({c})班", "物理", avg, f"c{c}_{i}"))
    # 另 6 个班没有物理，不应进分母
    for c in range(5, 11):
        for i in range(3):
            rows.append(_row(f"高三({c})班", "语文", 110.0, f"y{c}_{i}"))
    comps = compare_class_subjects_vs_peers(rows, class_name="高三(1)班")
    phy = next(c for c in comps if c["subject"] == "物理")
    assert phy["total_classes"] == 4
    # n=4 不用后 3 名；后 30% → 仅 rank>3 即第 4 名才因名次命中。第 4 且分差看数据。
    assert phy["rank"] == 4


def test_no_weak_when_all_above_peers():
    rows = []
    for i in range(3):
        rows.append(_row("高三(1)班", "语文", 120.0, f"t{i}"))
        rows.append(_row("高三(1)班", "数学", 119.0, f"tm{i}"))
    for c in range(2, 6):
        for i in range(3):
            rows.append(_row(f"高三({c})班", "语文", 100.0, f"y{c}_{i}"))
            rows.append(_row(f"高三({c})班", "数学", 100.0, f"m{c}_{i}"))
    comps = compare_class_subjects_vs_peers(rows, class_name="高三(1)班")
    assert identify_weak_subjects(comps) == []


def test_drill_caps_at_two():
    weak = [
        {"subject": "物理", "avg_gap": 12.0},
        {"subject": "化学", "avg_gap": 9.0},
        {"subject": "生物", "avg_gap": 6.0},
    ]
    assert pick_drill_subjects(weak) == ["物理", "化学"]
    assert DRILL_TOP_N == 2
```

`test_rank_9_of_10` 里目标均分 100、其余 110，目标是最后一名即 rank=10。断言用 `rank == 10`。

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_class_weak_subject.py::test_rank_10_of_10_is_weak -v
```

Expected: FAIL import error。

- [ ] **Step 3: 实现纯函数**

Create `src/agent/education/class_weak_subject.py`：

```python
"""指定班级薄弱学科：同校同年级同科班际对比（纯函数，无 I/O）。"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from src.agent.education.dimension_parse import parse_grade_from_class
from src.agent.education.stats import compute_score_stats
from src.agent.education.config import EducationConfig

WEAK_RANK_RATIO = 0.30
WEAK_AVG_GAP = 5.0
LAST_N_MIN_CLASSES = 10
LAST_N = 3
DRILL_TOP_N = 2
MIN_CLASS_N = 3
ITEM_LAG_PP = -8.0
COMMON_HARD_RATE = 60.0
MAX_LAGGING_ITEMS = 15
MAX_COMMON_ITEMS = 8

__all__ = [
    "DRILL_TOP_N",
    "compare_class_items_vs_peers",
    "compare_class_subjects_vs_peers",
    "identify_weak_subjects",
    "pick_drill_subjects",
    "build_class_weak_subject_report_data",
    "build_recommendations_html",
    "render_class_weak_subject_html",
]


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _class_of(row: dict[str, Any]) -> str:
    return str(row.get("class_name") or row.get("class") or "").strip()


def _subject_of(row: dict[str, Any]) -> str:
    return str(row.get("subject") or row.get("subject_name") or "").strip()


def _dense_rank_by_avg(class_avgs: dict[str, float]) -> dict[str, int]:
    ordered = sorted(class_avgs.items(), key=lambda kv: (-kv[1], kv[0]))
    ranks: dict[str, int] = {}
    rank = 0
    prev: float | None = None
    for name, avg in ordered:
        if prev is None or avg < prev:
            rank += 1
        ranks[name] = rank
        prev = avg
    return ranks


def compare_class_subjects_vs_peers(
    score_rows: list[dict[str, Any]],
    *,
    class_name: str,
    min_class_n: int = MIN_CLASS_N,
) -> list[dict[str, Any]]:
    target = str(class_name or "").strip()
    grade = parse_grade_from_class(target) or ""
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in score_rows:
        cls = _class_of(r)
        sub = _subject_of(r)
        sc = _num(r.get("score"))
        if not cls or not sub or sc is None:
            continue
        if grade and parse_grade_from_class(cls) != grade:
            continue
        by[sub][cls].append(sc)

    cfg = EducationConfig()
    out: list[dict[str, Any]] = []
    for sub, classes in by.items():
        usable = {c: scores for c, scores in classes.items() if len(scores) >= min_class_n}
        if target not in usable:
            continue
        avgs = {c: sum(s) / len(s) for c, s in usable.items()}
        peer_avg = sum(avgs.values()) / len(avgs)
        ranks = _dense_rank_by_avg(avgs)
        tgt_scores = usable[target]
        tgt_stats = compute_score_stats(tgt_scores, cfg)
        peer_pass = []
        peer_exc = []
        for c, scores in usable.items():
            st = compute_score_stats(scores, cfg)
            if st.get("pass_rate") is not None:
                peer_pass.append(float(st["pass_rate"]))
            if st.get("excellent_rate") is not None:
                peer_exc.append(float(st["excellent_rate"]))
        class_avg = avgs[target]
        pass_rate = float(tgt_stats.get("pass_rate") or 0)
        exc_rate = float(tgt_stats.get("excellent_rate") or 0)
        p_pass = sum(peer_pass) / len(peer_pass) if peer_pass else None
        p_exc = sum(peer_exc) / len(peer_exc) if peer_exc else None
        out.append({
            "subject": sub,
            "class_avg": round(class_avg, 2),
            "peer_avg": round(peer_avg, 2),
            "avg_gap": round(peer_avg - class_avg, 2),
            "rank": ranks[target],
            "total_classes": len(usable),
            "pass_rate": round(pass_rate, 2),
            "peer_pass_rate": round(p_pass, 2) if p_pass is not None else None,
            "pass_gap": round((p_pass - pass_rate), 2) if p_pass is not None else None,
            "excellent_rate": round(exc_rate, 2),
            "peer_excellent_rate": round(p_exc, 2) if p_exc is not None else None,
            "n": len(tgt_scores),
        })
    out.sort(key=lambda x: str(x["subject"]))
    return out


def _is_weak(row: dict[str, Any]) -> tuple[bool, list[str]]:
    n = int(row.get("total_classes") or 0)
    rank = int(row.get("rank") or 0)
    gap = float(row.get("avg_gap") or 0)
    reasons: list[str] = []
    if n <= 0 or rank <= 0:
        return False, []
    cutoff = math.ceil(n * (1 - WEAK_RANK_RATIO))
    if rank > cutoff:
        reasons.append(f"名次第 {rank}/{n}（后 {int(WEAK_RANK_RATIO * 100)}%）")
    if gap >= WEAK_AVG_GAP:
        reasons.append(f"均分低于对照 {gap:.1f} 分")
    if n >= LAST_N_MIN_CLASSES and rank > n - LAST_N:
        tag = f"名次第 {rank}/{n}（后 {LAST_N} 名）"
        if tag not in reasons and not any("后" in r and "名" in r for r in reasons):
            reasons.append(tag)
    return bool(reasons), reasons


def identify_weak_subjects(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weak: list[dict[str, Any]] = []
    for row in comparisons:
        hit, reasons = _is_weak(row)
        item = dict(row)
        item["is_weak"] = hit
        item["reasons"] = reasons
        if hit:
            weak.append(item)
    weak.sort(key=lambda x: float(x.get("avg_gap") or 0), reverse=True)
    return weak


def pick_drill_subjects(
    weak: list[dict[str, Any]],
    top_n: int = DRILL_TOP_N,
) -> list[str]:
    return [str(w.get("subject") or "") for w in weak[:top_n] if w.get("subject")]
```

`_is_weak` 里「后 3 名」与「后 30%」对 n=10 会重叠，重复 reason 可去重；有一条即可。

- [ ] **Step 4: 跑 Task 2 测试**

```bash
uv run pytest tests/agent/test_class_weak_subject.py -v
```

Expected: Task 1+2 全 PASS。若 `test_mid_rank_with_gap_6` 的 `avg_gap` 因「班均的均」不到 5，把目标均分再降到 100，保持 rank=5。

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add src/agent/education/class_weak_subject.py tests/agent/test_class_weak_subject.py
git commit -m "feat(edu): compare class subjects against same-school peers"
```

---

## Task 3: 小题差 + 建议 + HTML

**Files:**
- Modify: `src/agent/education/class_weak_subject.py`
- Test: `tests/agent/test_class_weak_subject.py`

- [ ] **Step 1: 写失败测试**

```python
from src.agent.education.class_weak_subject import (
    compare_class_items_vs_peers,
    build_class_weak_subject_report_data,
    render_class_weak_subject_html,
)


def test_item_lagging_vs_common_hard():
    rows = [
        {"class_name": "高三(1)班", "question_no": "1", "score_rate": 50.0, "knowledge_name": "函数"},
        {"class_name": "高三(2)班", "question_no": "1", "score_rate": 80.0, "knowledge_name": "函数"},
        {"class_name": "高三(3)班", "question_no": "1", "score_rate": 78.0, "knowledge_name": "函数"},
        {"class_name": "高三(1)班", "question_no": "2", "score_rate": 40.0, "knowledge_name": "立体几何"},
        {"class_name": "高三(2)班", "question_no": "2", "score_rate": 42.0, "knowledge_name": "立体几何"},
        {"class_name": "高三(3)班", "question_no": "2", "score_rate": 41.0, "knowledge_name": "立体几何"},
    ]
    got = compare_class_items_vs_peers(rows, class_name="高三(1)班")
    lag_nos = [x["question_no"] for x in got["lagging"]]
    hard_nos = [x["question_no"] for x in got["common_hard"]]
    assert "1" in lag_nos
    assert "2" not in lag_nos
    assert "2" in hard_nos


def test_empty_weak_report_has_no_item_section():
    comps = compare_class_subjects_vs_peers(
        [
            _row("高三(1)班", "语文", 120.0, "a"),
            _row("高三(1)班", "语文", 121.0, "b"),
            _row("高三(1)班", "语文", 119.0, "c"),
            _row("高三(2)班", "语文", 100.0, "d"),
            _row("高三(2)班", "语文", 100.0, "e"),
            _row("高三(2)班", "语文", 100.0, "f"),
        ],
        class_name="高三(1)班",
    )
    report = build_class_weak_subject_report_data(
        school_name="仙城中学",
        class_name="高三(1)班",
        exam_name="1月期末",
        comparisons=comps,
        weak_subjects=[],
        drill_subjects=[],
        item_by_subject={},
    )
    html = render_class_weak_subject_html(report)
    assert "无明显薄弱" in html
    assert "特差" not in html
    assert "各科互比" in html or "班际" in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_class_weak_subject.py::test_item_lagging_vs_common_hard -v
```

Expected: FAIL。

- [ ] **Step 3: 补齐小题对比、建议、HTML**

Append to `class_weak_subject.py`（保留 Task 2 已有函数）：

`compare_class_items_vs_peers`：按 `question_no` 分组；本班得分率 vs 其他班平均；`class_rate - peer_rate <= ITEM_LAG_PP` → lagging（按差值升序，最多 15）；两边都 `< COMMON_HARD_RATE` 且不在 lagging → common_hard（最多 8）。过滤空班级。年级过滤：若能从 `class_name` 解析年级，丢掉不同年级的 `item_class_rows`。

`build_recommendations_html(weak, item_by_subject)`：

- 无 weak：`<ul><li>该班各科相对本校同年级均无明显薄弱，不必单开加课时。</li></ul>`
- 有 lagging：点名该科题号，建议本班讲评/加练。
- 仅 common_hard：写明全年级都难，跟年级进度讲评。
- 禁止出现「加强基础」四字。

`build_class_weak_subject_report_data(...)` 返回 dict，至少含：

- `title`：`{school} {class} · {exam} · 薄弱学科`
- `comparisons`（每行可带 `is_weak`、`drill` bool：drill 名单内为 True，其余命中科文案用「薄弱（本次未下钻题目）」）
- `weak_subjects` / `drill_subjects` / `item_by_subject` / `SUMMARY` / `RECOMMENDATIONS` / `empty_weak`

`render_class_weak_subject_html`：内联短 CSS（`edu-card` / table），结构按 spec §5。口径句必须出现：「同校同年级同一学科、不同班级对比，不是本班各科互比」。无薄弱时不要小题表。不要雷达图。

把 HTML 里的用户文本用 `html.escape`。

- [ ] **Step 4: 跑测试**

```bash
uv run pytest tests/agent/test_class_weak_subject.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add src/agent/education/class_weak_subject.py tests/agent/test_class_weak_subject.py
git commit -m "feat(edu): render class weak-subject evidence and item gaps"
```

---

## Task 4: 硬路由 + 1 步计划

**Files:**
- Modify: `src/agent/education/intent_router.py`
- Modify: `src/agent/expand/planner.py`
- Test: `tests/agent/test_class_weak_subject.py`

- [ ] **Step 1: 写失败测试**

```python
from src.agent.education.intent_router import (
    classify_report_intent_sync,
    coerce_plan_to_route,
    plan_items_for_route,
)
from src.agent.education.report_types import ReportType


def test_golden_query_hard_routes_to_weak_subject_tool():
    route = classify_report_intent_sync(GOLDEN)
    assert route.needs_report is True
    assert route.source == "hard"
    assert route.report_type == ReportType.SUBJECT_DIAGNOSIS
    items = plan_items_for_route(route, GOLDEN)
    blob = " ".join(it["sub_task"] for it in items)
    assert "build_class_weak_subject_report_data_tool" in blob
    assert "build_class_overview_report_data_tool" not in blob
    assert "build_subject_diagnosis_sections_tool" not in blob
    coerced = coerce_plan_to_route(GOLDEN, [{"sub_task": GOLDEN, "sub_task_agent": "DataAnalyst"}], route)
    assert "build_class_weak_subject_report_data_tool" in coerced[0]["sub_task"]


def test_class_overview_query_not_stolen():
    q = "B11仙城中学高三(1)班2026届高三1月期末班级总览"
    route = classify_report_intent_sync(q)
    blob = " ".join(it["sub_task"] for it in plan_items_for_route(route, q))
    assert "build_class_weak_subject_report_data_tool" not in blob
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_class_weak_subject.py::test_golden_query_hard_routes_to_weak_subject_tool -v
```

Expected: FAIL（计划里没有新工具）。

- [ ] **Step 3: planner 增加 1 步计划**

In `src/agent/expand/planner.py`, next to `build_knowledge_cohort_plan_items`:

```python
def build_class_weak_subject_plan_items(question: str) -> list[dict[str, str]]:
    """指定班级薄弱学科：同校同科班际对比，一键工具。"""
    from src.agent.education.orchestrator import _extract_class_name
    from src.agent.education.query_parse import extract_school_target

    school = extract_school_target(question) or ""
    class_name = _extract_class_name(question) or ""
    exam = _plan_exam_name(question)
    scope_args: list[str] = []
    if school:
        scope_args.append(f"school_name={school}")
    if class_name:
        scope_args.append(f"class_name={class_name}")
    if exam:
        scope_args.append(f"exam_name={exam}")
    tool_args = (", ".join(scope_args) + ", ") if scope_args else ""
    class_l = class_name or "该班"
    exam_l = _plan_label(exam, missing="问题中的考试")
    school_l = school or "该校"
    return [
        {
            "sub_task": (
                f"调 build_class_weak_subject_report_data_tool({tool_args}render=true) "
                f"分析【{school_l}】【{class_l}】【{exam_l}】薄弱学科"
                "（同校同年级同一学科不同班级对比，禁止本班各科互比）；"
                "有薄弱则下钻最弱 1～2 科小题；完成后 terminate。"
                "**禁止** build_class_overview_report_data_tool / "
                "build_subject_diagnosis_sections_tool / execute_sql 自行比各科均分"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]
```

不要传 `subject_name`。

- [ ] **Step 4: intent_router 拦截（所有挂钩都仿 knowledge_cohort）**

1. `fallback_classify_report_intent`：在 `is_knowledge_cohort_gap_query` 块**之后**插入：

```python
    if is_class_weak_subject_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_DIAGNOSIS,
            confidence=0.95,
            reason="硬约束班级薄弱学科（同校同科班际对比）",
            source="hard",
        )
```

2. `classify_report_intent`（异步 LLM 那条）同样在 knowledge_cohort 硬约束后加相同返回，避免 LLM 改判。
3. `should_use_deterministic_report_plan` 的 `any(...)` 增加 `is_class_weak_subject_query(q)`。
4. `plan_items_for_route` 与 `plan_items_for_report_type`：**最先**判断 `is_class_weak_subject_query`，返回 `build_class_weak_subject_plan_items(q)`，必须写在 `build_school_subject_report_plan_items` 之前。
5. `plan_matches_report_type`：若 blob 含 `build_class_weak_subject_report_data_tool`，return True（放在 knowledge_cohort 工具判断旁边）。
6. `coerce_plan_to_route`：与 knowledge_cohort 同样，命中探测器但计划不含本工具则 rebuild。
7. `plan_is_fact_query` 的工具黑名单加上 `build_class_weak_subject_report_data_tool`。

所有新增 import 用现有 `from src.agent.education.query_parse import (...)` 列表追加，不要另开一套。

- [ ] **Step 5: 跑测试**

```bash
uv run pytest tests/agent/test_class_weak_subject.py -v
```

Expected: PASS。

- [ ] **Step 6: Commit（默认跳过）**

```bash
git add src/agent/education/intent_router.py src/agent/expand/planner.py tests/agent/test_class_weak_subject.py
git commit -m "feat(edu): route class weak-subject queries to dedicated plan"
```

---

## Task 5: 取数工具

**Files:**
- Modify: `src/agent/education/tools.py`
- Test: `tests/agent/test_class_weak_subject.py`（工具缺参单测；不打真实库）

- [ ] **Step 1: 写失败测试**

```python
from src.agent.education.tools import build_class_weak_subject_report_data_tool


def test_tool_requires_class_and_exam():
    result = build_class_weak_subject_report_data_tool(
        school_name="仙城中学",
        class_name="",
        exam_name="1月期末",
        datasource_id=1,
        tool_runtime_ctx={"datasource_id": 1},
    )
    assert "class_name" in (result.content or "")
```

若 `@tool()` 包装导致直接调用不便，改为 import 底层函数（把校验抽成 `_class_weak_subject_missing_slots`）测纯函数。不要为了测工具去 mock 整库。

- [ ] **Step 2: 实现工具**

在 `tools.py` 中 `compare_knowledge_cohort_tool` 附近新增 `build_class_weak_subject_report_data_tool`，签名对齐 knowledge_cohort（`school_name, class_name, exam_name, render, datasource_id, workspace_oid, user_id, tool_runtime_ctx`）。**不要** `subject_name`。

行为：

1. `_guard_report_when_fact_query`。
2. 规范化班级（`normalize_fullwidth_parentheses`）、考试（`_clean_exam_name_candidate`）。
3. 缺 `class_name` 或 `exam_name` 或 `school_name` → `ToolResult` 可读错误，不渲染。缺学校时文案写明「需要学校，禁止改查全市」。
4. `_load_datasource`；`_diagnosis_where_clause_pair(school_name=..., class_name="", subject_name="", exam_name=...)` + `_diagnosis_sql_bundle` 只跑 **score_sql**（全校全科，不按班过滤）。`_run_edu_sql` + `_rows_to_dicts`。多 `exam_id` 时用 `pick_primary_exam_id` 收敛。考试名空结果时，可按学校放宽考试名，**禁止清空 school_name**。
5. `compare_class_subjects_vs_peers` → `identify_weak_subjects` → `pick_drill_subjects`。
6. **仅当** `drill_subjects` 非空：对每科 `_fetch_subject_diagnosis_rows(..., class_name="", subject_name=该科)`，取 `item_class_rows` 交给 `compare_class_items_vs_peers`。weak 为空时**禁止** fetch 小题。
7. `build_class_weak_subject_report_data` + `render_class_weak_subject_html`；`render=true` 时 `_sanitize_report_html`，payload 的 `report_type` / `report_type_label` 用 `"class_weak_subject"` / `"薄弱学科"`（不要走科目诊断模板）。`is_final=True`，content 末尾「任务已完成，无需再调用其他工具。」

计算逻辑全部在 `class_weak_subject.py`，`tools.py` 只取数和组装。

8. 把工具加入 `EDUCATION_TOOLS` 和 `__all__`（紧挨 `compare_knowledge_cohort_tool`）。

- [ ] **Step 3: 跑全部相关测试**

```bash
uv run pytest tests/agent/test_class_weak_subject.py -v
uv run ruff check src/agent/education/class_weak_subject.py src/agent/education/query_parse.py src/agent/education/intent_router.py src/agent/expand/planner.py src/agent/education/tools.py tests/agent/test_class_weak_subject.py
```

Expected: pytest PASS；ruff 无新增 E/F/I。

- [ ] **Step 4: Commit（默认跳过）**

```bash
git add src/agent/education/tools.py tests/agent/test_class_weak_subject.py
git commit -m "feat(edu): add class weak-subject report tool"
```

---

## Spec coverage

| Spec | Task |
|------|------|
| §2.1 探测器 / 验收句 / 反例 | Task 1 |
| `B11仙城中学` 抽取 | Task 1 |
| §4.1–4.2 对照班、选考过滤、A 规则、后 3 名门槛、下钻封顶 | Task 2 |
| §4.3–4.4 小题特差/共性、建议、无命中不下钻 | Task 3 |
| §5 HTML 结构、禁止各科雷达 | Task 3 |
| §2.2 / §3.1 硬路由与 1 步计划 | Task 4 |
| §3.2 工具取数 | Task 5 |
| 不新增 ReportType、不改班级总览填充 | 全任务未改那些文件 |

手工验收（实现后、有前端时）：把验收原句丢进聊天，确认工具名出现在链路里，结论学科来自班际名次/分差。
