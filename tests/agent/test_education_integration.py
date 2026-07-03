"""DataAnalyst 教育学情报告端到端集成测试。

用 Fake LLM 跑一次完整的 ReAct 链路：
    select_report_template_tool
      -> compute_score_stats_tool
      -> build_chart_option_tool
      -> render_html_report(template=education/class_overview.html, data={...})
      -> terminate
验证最终回复包含报告生成说明，且渲染出的 HTML 含 KPI 与 ECharts JSON。
"""

from __future__ import annotations

import asyncio
import json

from src.agent.core.agent import AgentMessage
from src.agent.expand.data_analyst import build_data_analyst
from src.agent.expand.user_proxy import UserProxyAgent


class _ScriptedLlm:
    def __init__(self, replies: list[str]) -> None:
        self._q = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self._q.pop(0)


def _run(coro):
    return asyncio.run(coro)


def test_data_analyst_education_report_flow():
    # 第 1 步：选定 class_overview 模板
    # 第 2 步：统计分数
    # 第 3 步：生成分数段柱图 option
    # 第 4 步：用模板渲染 HTML 报告
    # 第 5 步：terminate
    stats_payload = {
        "count": 4, "avg": 72.5, "median": 75.0, "stdev": 17.5,
        "min": 50, "max": 95, "pass_rate": 75.0, "excellent_rate": 25.0,
        "fail_rate": 25.0, "full_score": 100,
        "segments": [
            {"label": "0-60", "count": 1, "ratio": 25.0},
            {"label": "60-70", "count": 0, "ratio": 0.0},
            {"label": "70-80", "count": 1, "ratio": 25.0},
            {"label": "80-90", "count": 1, "ratio": 25.0},
            {"label": "90-100", "count": 1, "ratio": 25.0},
        ],
    }
    chart_option = json.dumps({
        "xAxis": {"type": "category", "data": [s["label"] for s in stats_payload["segments"]]},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "data": [s["count"] for s in stats_payload["segments"]]}],
    }, ensure_ascii=False)

    report_data = {
        "REPORT_TITLE": "初三1班期中成绩分析报告",
        "REPORT_SUBTITLE": "班级学情总览",
        "REPORT_TIME": "2026-07-01",
        "CLASS_NAME": "初三1班", "EXAM_NAME": "期中考试",
        "TOTAL_COUNT": str(stats_payload["count"]),
        "AVG_SCORE": str(stats_payload["avg"]),
        "PASS_RATE": str(stats_payload["pass_rate"]),
        "EXCELLENT_RATE": str(stats_payload["excellent_rate"]),
        "STDEV": str(stats_payload["stdev"]),
        "SCORE_DIST_CHART": chart_option,
        "SUBJECT_RADAR_CHART": "{}",
        "SUBJECT_BREAKDOWN": "<p>分科明细占位</p>",
        "RANK_INFO": "<p>年级第 3 / 8</p>",
        "SUMMARY": "<p>整体稳健，数学薄弱。</p>",
        "RECOMMENDATIONS": "<ul><li>加强数学基础题</li></ul>",
    }

    llm = _ScriptedLlm(
        [
            '{"thoughts":"选模板","tool":"select_report_template_tool","args":{"report_type":"class_overview"}}',
            '{"thoughts":"算统计","tool":"compute_score_stats_tool","args":{"scores":[95,80,65,50]}}',
            '{"thoughts":"画分数段","tool":"build_chart_option_tool","args":{"chart_type":"score_distribution","data":{"segments":[{"label":"0-60","count":1},{"label":"60-70","count":0},{"label":"70-80","count":1},{"label":"80-90","count":1},{"label":"90-100","count":1}],"pass_rate":75.0},"title":"分数段"}}',
            '{"thoughts":"出报告","tool":"render_html_report","args":{"template_name":"education/class_overview.html","data":' + json.dumps(report_data, ensure_ascii=False) + ',"title":"班级总览"}}',
            '{"thoughts":"完成","tool":"terminate","args":{"final_answer":"已生成初三1班期中成绩分析报告（含分数段柱图与分科表现）。"}}',
        ]
    )

    agent = build_data_analyst(llm_client=llm, datasource_id=1, user_id=7)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="生成初三1班期中成绩分析报告", role="user"
            ),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.terminate is True
    assert "初三1班" in reply.content
    assert reply.rounds == 5

    # terminate 轮（最后一轮 LLM 调用）应能看到 render_html_report 的 observation
    last_tool_round = llm.calls[-1]
    assert any("已生成" in m["content"] and "mode=template" in m["content"]
               for m in last_tool_round), \
        "render_html_report observation 未回灌到 terminate 轮"

    # 第一步的 observation 应含模板名
    round2 = llm.calls[1]
    assert any("education/class_overview.html" in m["content"] for m in round2)
