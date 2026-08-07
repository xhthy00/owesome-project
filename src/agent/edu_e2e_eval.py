"""教育学情端到端问答评测 CLI。

批量跑 team 模式（真实 LLM + SQL），按类别自动判分并输出统计。

用法::

    uv run python -m src.agent.edu_e2e_eval -d 1 -u 4 \\
      --bank tests/agent/fixtures/edu_e2e_questions.json \\
      --out artifacts/edu_e2e_results.json

    uv run python -m src.agent.edu_e2e_eval -d 1 -u 4 --category student_profile
    uv run python -m src.agent.edu_e2e_eval -d 1 -u 4 --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

__all__ = ["main", "score_case", "summarize_results"]

_DEFAULT_BANK = Path("tests/agent/fixtures/edu_e2e_questions.json")
_DIGIT_RE = re.compile(r"\d")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_bank(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        qs = raw.get("questions") or []
        if not isinstance(qs, list):
            raise ValueError("bank.questions must be a list")
        return list(qs)
    raise ValueError("bank must be list or {questions: [...]}")


def _route_snapshot(question: str) -> dict[str, Any]:
    from src.agent.education.intent_router import classify_report_intent_sync

    route = classify_report_intent_sync(question)
    return {
        "needs_report": bool(route.needs_report),
        "report_type": route.report_type.value if route.report_type else None,
        "confidence": route.confidence,
        "reason": route.reason,
        "source": route.source,
    }


def _collect_tools(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for ev in events:
        if ev.get("event") != "tool_result":
            continue
        data = ev.get("data") or {}
        tool = str(data.get("tool") or data.get("name") or "").strip()
        if tool:
            names.append(tool)
    return names


def _collect_reports(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("event") != "report":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append(dict(data))
    return out


def _summary_text(events: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for ev in events:
        name = ev.get("event")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if name == "summary":
            chunks.append(str(data.get("content") or data.get("text") or ""))
        elif name == "final_answer":
            chunks.append(str(data.get("text") or data.get("content") or ""))
        elif name == "agent_speak" and data.get("status") == "end":
            chunks.append(str(data.get("summary_preview") or ""))
    return "\n".join(c for c in chunks if c).strip()


def _errors(events: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    for ev in events:
        if ev.get("event") == "error":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            errs.append(str(data.get("error") or data))
    return errs


def score_case(
    case: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    route: dict[str, Any],
    error: str | None = None,
    timed_out: bool = False,
) -> dict[str, Any]:
    """对单题结果判分。"""
    from src.agent.education.report_quality import report_html_quality_issues
    from src.agent.education.report_types import REPORT_TYPE_LABELS, ReportType

    fail: list[str] = []
    expect_needs = bool(case.get("expect_needs_report"))
    expect_type = case.get("expect_report_type")
    if expect_type is not None:
        expect_type = str(expect_type).strip() or None

    route_needs = bool(route.get("needs_report"))
    route_type = route.get("report_type")
    route_ok = route_needs == expect_needs and (
        (expect_type is None and route_type is None)
        or (expect_type is not None and route_type == expect_type)
    )

    if timed_out:
        fail.append("timeout")
    if error:
        fail.append("exception")

    errs = _errors(events)
    if errs:
        fail.append("error_event")

    tools = _collect_tools(events)
    reports = _collect_reports(events)
    summary = _summary_text(events)

    report_types_got: list[str] = []
    html_blobs: list[str] = []
    for rp in reports:
        rt = str(rp.get("report_type") or "").strip()
        label = str(rp.get("report_type_label") or "").strip()
        if rt:
            report_types_got.append(rt)
        elif label:
            for enum_rt, lab in REPORT_TYPE_LABELS.items():
                if label == lab:
                    report_types_got.append(enum_rt.value)
                    break
            else:
                report_types_got.append(label)
        html_blobs.append(str(rp.get("html") or rp.get("content") or ""))

    primary_report_type = report_types_got[0] if report_types_got else None

    # 工具约束
    forbid_tools = [str(t) for t in (case.get("forbid_tools") or [])]
    for t in forbid_tools:
        # 成功调用才算违规：content 含「禁止」时多为守卫拒绝，放行
        for ev in events:
            if ev.get("event") != "tool_result":
                continue
            data = ev.get("data") or {}
            if str(data.get("tool") or "") != t:
                continue
            content = str(data.get("content") or "")
            success = data.get("success")
            if success is False:
                continue
            if "needs_report_false" in content or "本题不需要生成" in content:
                continue
            if "禁止" in content and t in content and "失败" not in content[:40]:
                # 文案里写禁止调用 ≠ 真的调成功
                if "已渲染" not in content and "已组装" not in content:
                    continue
            fail.append(f"forbid_tool:{t}")
            break

    expect_tools_any = [str(t) for t in (case.get("expect_tools_any") or [])]
    if expect_tools_any and not any(t in tools for t in expect_tools_any):
        fail.append("missing_expected_tool")

    if expect_needs:
        if not reports:
            fail.append("missing_report")
        else:
            if expect_type and primary_report_type != expect_type:
                # 标签匹配兜底
                want_label = ""
                try:
                    want_label = REPORT_TYPE_LABELS[ReportType(expect_type)]
                except ValueError:
                    want_label = ""
                labels = [str(r.get("report_type_label") or "") for r in reports]
                if want_label and want_label not in labels:
                    fail.append(
                        f"wrong_report_type:got={primary_report_type or labels[0]!r}"
                    )
            for html in html_blobs:
                for issue in report_html_quality_issues(html):
                    tag = f"empty_report:{issue}"
                    if tag not in fail:
                        fail.append(tag)
        forbid_types = [str(t) for t in (case.get("forbid_report_types") or [])]
        for ft in forbid_types:
            if ft in report_types_got:
                fail.append(f"forbid_report_type:{ft}")
    else:
        # 事实题：不应产出正式 9 类报告
        formal = {rt.value for rt in ReportType}
        formal_hit = [t for t in report_types_got if t in formal]
        if formal_hit:
            fail.append(f"unexpected_report:{formal_hit[0]}")
        if not summary and not timed_out and not error:
            fail.append("no_answer")
        elif summary and not _DIGIT_RE.search(summary):
            # 事实题通常应含数字；弱信号
            fail.append("answer_no_digits")

    e2e_ok = not fail
    return {
        "route_ok": route_ok,
        "e2e_ok": e2e_ok,
        "pass": e2e_ok,
        "fail_reasons": fail,
        "route": route,
        "report_type_got": primary_report_type,
        "report_types": report_types_got,
        "tools": tools,
        "summary_preview": summary[:400],
        "error": error,
        "errors": errs[:5],
        "timed_out": timed_out,
        "report_count": len(reports),
        "html_lens": [len(h) for h in html_blobs],
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_cat: dict[str, dict[str, Any]] = {}
    reason_counter: Counter[str] = Counter()
    for r in results:
        cat = str(r.get("category") or "unknown")
        slot = by_cat.setdefault(
            cat,
            {
                "total": 0,
                "pass": 0,
                "route_ok": 0,
                "e2e_ok": 0,
                "fail_reasons": Counter(),
            },
        )
        slot["total"] += 1
        if r.get("pass"):
            slot["pass"] += 1
        if r.get("route_ok"):
            slot["route_ok"] += 1
        if r.get("e2e_ok"):
            slot["e2e_ok"] += 1
        for fr in r.get("fail_reasons") or []:
            # 归一化 wrong_report_type / forbid_tool 前缀
            key = str(fr).split(":", 1)[0]
            slot["fail_reasons"][key] += 1
            reason_counter[key] += 1

    categories = {}
    for cat, slot in sorted(by_cat.items()):
        total = slot["total"] or 1
        categories[cat] = {
            "total": slot["total"],
            "pass": slot["pass"],
            "pass_rate": round(slot["pass"] / total, 3),
            "route_ok": slot["route_ok"],
            "route_ok_rate": round(slot["route_ok"] / total, 3),
            "e2e_ok": slot["e2e_ok"],
            "e2e_ok_rate": round(slot["e2e_ok"] / total, 3),
            "fail_reasons": dict(slot["fail_reasons"]),
        }

    n = len(results) or 1
    passed = sum(1 for r in results if r.get("pass"))
    return {
        "total": len(results),
        "pass": passed,
        "pass_rate": round(passed / n, 3),
        "route_ok": sum(1 for r in results if r.get("route_ok")),
        "route_ok_rate": round(sum(1 for r in results if r.get("route_ok")) / n, 3),
        "e2e_ok": sum(1 for r in results if r.get("e2e_ok")),
        "e2e_ok_rate": round(sum(1 for r in results if r.get("e2e_ok")) / n, 3),
        "fail_reason_totals": dict(reason_counter),
        "categories": categories,
    }


async def _run_one(
    case: dict[str, Any],
    *,
    datasource_id: int,
    user_id: int,
    workspace_oid: int,
    timeout_sec: float,
) -> dict[str, Any]:
    from src.chat.schemas import ChatRequest
    from src.chat.service.agent_runner import run_team_stream

    question = str(case.get("question") or "").strip()
    route = _route_snapshot(question)
    events: list[dict[str, Any]] = []
    t0 = time.time()
    error: str | None = None
    timed_out = False

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append({"event": event, "data": data if isinstance(data, dict) else {}})

    req = ChatRequest(
        question=question,
        datasource_id=datasource_id,
        agent_mode="team",
        enable_tool_agent=True,
    )
    try:
        await asyncio.wait_for(
            run_team_stream(
                request=req,
                current_user_id=user_id,
                emit=emit,
                persist=False,
                enable_tool_agent=True,
                workspace_oid=workspace_oid,
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        timed_out = True
        error = f"timeout after {timeout_sec}s"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    elapsed = round(time.time() - t0, 2)
    scored = score_case(
        case,
        events=events,
        route=route,
        error=error,
        timed_out=timed_out,
    )
    return {
        "id": case.get("id"),
        "category": case.get("category"),
        "question": question,
        "elapsed_s": elapsed,
        "event_count": len(events),
        **scored,
    }


def _print_summary(summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    print("=" * 72)
    print(
        f"TOTAL  pass={summary['pass']}/{summary['total']} "
        f"({summary['pass_rate']:.0%})  "
        f"route_ok={summary['route_ok_rate']:.0%}  "
        f"e2e_ok={summary['e2e_ok_rate']:.0%}"
    )
    print("-" * 72)
    print(f"{'category':<22} {'pass':>8} {'route':>8} {'e2e':>8}  top_fails")
    for cat, slot in (summary.get("categories") or {}).items():
        fails = slot.get("fail_reasons") or {}
        top = ",".join(f"{k}:{v}" for k, v in list(fails.items())[:3]) or "-"
        print(
            f"{cat:<22} {slot['pass']:>3}/{slot['total']:<3} "
            f"{slot['route_ok']:>3}/{slot['total']:<3} "
            f"{slot['e2e_ok']:>3}/{slot['total']:<3}  {top}"
        )
    print("-" * 72)
    for r in results:
        status = "PASS" if r.get("pass") else "FAIL"
        reasons = ",".join(r.get("fail_reasons") or []) or "-"
        print(
            f"[{status}] {r.get('id')}  {r.get('elapsed_s')}s  "
            f"route_ok={r.get('route_ok')} type={r.get('report_type_got')}  "
            f"{reasons}"
        )
    print("=" * 72)


async def _amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.agent.edu_e2e_eval",
        description="教育学情端到端问答评测（team 模式）",
    )
    parser.add_argument(
        "-d",
        "--datasource-id",
        type=int,
        default=1,
        help="数据源 ID（默认 1=exam；前端 .env 写 12 时以本机库为准）",
    )
    parser.add_argument("-u", "--user-id", type=int, default=4)
    parser.add_argument("--workspace-oid", type=int, default=1)
    parser.add_argument(
        "--bank",
        type=Path,
        default=None,
        help="题库 JSON 路径",
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/edu_e2e_results.json"))
    parser.add_argument("--category", type=str, default="", help="只跑某一 category")
    parser.add_argument("--limit", type=int, default=0, help="最多跑 N 题（0=全部）")
    parser.add_argument("--timeout-sec", type=float, default=600.0)
    parser.add_argument(
        "--ids",
        type=str,
        default="",
        help="逗号分隔题 id，只跑这些题",
    )
    args = parser.parse_args(argv)

    bank_path = args.bank or (_repo_root() / _DEFAULT_BANK)
    if not bank_path.is_file():
        print(f"bank not found: {bank_path}", file=sys.stderr)
        return 2

    cases = _load_bank(bank_path)
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if args.ids.strip():
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c.get("id") in want]
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("no questions to run", file=sys.stderr)
        return 2

    print(
        f"→ bank={bank_path}  cases={len(cases)}  "
        f"ds={args.datasource_id}  user={args.user_id}  "
        f"timeout={args.timeout_sec}s",
        flush=True,
    )
    print("-" * 72, flush=True)

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        qid = case.get("id")
        print(
            f"\n[{i}/{len(cases)}] {qid}  {case.get('category')}  {case.get('question')}",
            flush=True,
        )
        row = await _run_one(
            case,
            datasource_id=args.datasource_id,
            user_id=args.user_id,
            workspace_oid=args.workspace_oid,
            timeout_sec=args.timeout_sec,
        )
        results.append(row)
        mark = "PASS" if row.get("pass") else "FAIL"
        print(
            f"  → {mark}  {row.get('elapsed_s')}s  "
            f"route_ok={row.get('route_ok')}  type={row.get('report_type_got')}  "
            f"fails={row.get('fail_reasons')}",
            flush=True,
        )

    summary = summarize_results(results)
    payload = {
        "meta": {
            "datasource_id": args.datasource_id,
            "user_id": args.user_id,
            "workspace_oid": args.workspace_oid,
            "bank": str(bank_path),
            "timeout_sec": args.timeout_sec,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "summary": summary,
        "results": results,
    }
    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    _print_summary(summary, results)
    print(f"wrote {out_path}")
    return 0 if summary.get("pass") == summary.get("total") else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
