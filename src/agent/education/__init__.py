"""教育学情分析领域包。

提供面向「学生成绩分析 + 报告生成」的领域抽象：

- ``report_types``：报告类型 / 受众 / 报告规格枚举；
- ``schema_mapping``：兼容宽表（一行多科）与标准分表（student/exam/score）的
  字段映射，屏蔽各校表结构差异；
- ``config``：及格线 / 优秀线 / 分数段 / 退步阈值等可配置项；
- ``data_adapter``：把 SQL 原始行归一化为 ``NormalizedScoreRow``；
- ``stats``：纯函数统计（均分 / 中位数 / 标准差 / 分数段 / 及格率）；
- ``charts``：ECharts option 生成；
- ``templates``：报告类型 → 模板名映射；
- ``tools``：暴露给 Agent ReAct 循环的 ``@tool()`` 工具。

设计哲学延续 ``calculate``：**数值由工具算，文字由 LLM 写，模板管排版**。
"""

from src.agent.education.comprehensive import build_comprehensive_data
from src.agent.education.orchestrator import ReportIntentResolver, ReportOrchestrator, ReportResult
from src.agent.education.report_types import Audience, ReportSpec, ReportType

__all__ = [
    "Audience",
    "ReportIntentResolver",
    "ReportOrchestrator",
    "ReportResult",
    "ReportSpec",
    "ReportType",
    "build_comprehensive_data",
]
