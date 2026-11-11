"""Read-only report for education query failures.

Usage:
    uv run python scripts/semantic_failure_report.py --days 7 --json

The script only reads tool_call_log. It is intentionally dependency-free so it
can run against production without loading the application.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

from src.common.core.config import get_settings


def build_report(days: int) -> dict:
    since = datetime.now() - timedelta(days=days)
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT tool_name, success, error_code, failure_domain, rule, error_detail
                FROM tool_call_log
                WHERE created_at >= :since
                  AND tool_name IN ('query_semantic', 'lookup_question_context')
                """
            ),
            {"since": since},
        ).mappings().all()

    queries = [row for row in rows if row["tool_name"] == "query_semantic"]
    lookups = [row for row in rows if row["tool_name"] == "lookup_question_context"]
    failures = [row for row in queries if not row["success"]]
    return {
        "window_days": days,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "query_semantic": {
            "total": len(queries),
            "succeeded": len(queries) - len(failures),
            "failed": len(failures),
            "success_rate": round((len(queries) - len(failures)) / len(queries), 4)
            if queries
            else None,
        },
        "lookup_question_context": {
            "total": len(lookups),
            "failed": sum(1 for row in lookups if not row["success"]),
        },
        "failure_domains": dict(Counter(row["failure_domain"] or "unknown" for row in failures)),
        "top_rules": dict(Counter(row["rule"] or "unknown" for row in failures).most_common(10)),
        "top_codes": dict(Counter(row["error_code"] or "unknown" for row in failures).most_common(10)),
        "repair_budget_exhausted": sum(
            1 for row in failures if '"repair_budget_exhausted": true' in (row["error_detail"] or "")
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="教育问数失败分类报告（只读）")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    report = build_report(args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(f"窗口：最近 {report['window_days']} 天")
    query = report["query_semantic"]
    print(f"query_semantic：{query['succeeded']}/{query['total']} 成功（{query['success_rate']}）")
    print(f"lookup_question_context 失败：{report['lookup_question_context']['failed']}")
    print(f"失败域：{report['failure_domains']}")
    print(f"Top 规则：{report['top_rules']}")
    print(f"Top 错误码：{report['top_codes']}")
    print(f"修复预算耗尽：{report['repair_budget_exhausted']}")


if __name__ == "__main__":
    main()
