import type { ExecutionStep } from "@/hooks/useChat";

export type HumanizePhase = "running" | "done" | "error";

type ToolCopy = Record<HumanizePhase, string>;

const TOOL_COPY: Record<string, ToolCopy> = {
  list_tables: {
    running: "助手正在查看可用数据表…",
    done: "已确认数据表清单",
    error: "未能获取数据表清单"
  },
  find_related_tables: {
    running: "助手正在匹配相关数据表…",
    done: "已定位相关数据表",
    error: "未能匹配到相关数据表"
  },
  describe_table: {
    running: "助手正在梳理表结构与字段…",
    done: "已掌握表结构信息",
    error: "未能读取表结构"
  },
  sample_rows: {
    running: "助手正在抽样核验数据…",
    done: "已完成样例数据核验",
    error: "未能获取样例数据"
  },
  execute_sql: {
    running: "助手正在查询成绩数据…",
    done: "已完成数据查询",
    error: "查询未成功，正在尝试调整"
  },
  find_related_datasources: {
    running: "助手正在确认数据源…",
    done: "已匹配到合适的数据源",
    error: "未能确认数据源"
  },
  recent_questions: {
    running: "助手正在回顾近期提问…",
    done: "已参考近期提问记录",
    error: "未能读取历史提问"
  },
  render_html_report: {
    running: "助手正在生成分析报告…",
    done: "报告已生成",
    error: "报告生成未完成"
  },
  compute_score_stats_tool: {
    running: "助手正在计算成绩统计指标…",
    done: "成绩统计指标已就绪",
    error: "成绩统计未能完成"
  },
  fetch_subject_diagnosis_data_tool: {
    running: "助手正在拉取科目诊断数据…",
    done: "科目诊断数据已就绪",
    error: "科目诊断数据未能完成"
  },
  build_subject_diagnosis_sections_tool: {
    running: "助手正在整理科目诊断内容…",
    done: "科目诊断内容已整理",
    error: "科目诊断内容未能完成"
  },
  build_subject_diagnosis_report_tool: {
    running: "助手正在生成科目诊断报告…",
    done: "科目诊断报告已生成",
    error: "科目诊断报告未能完成"
  },
  build_class_overview_report_data_tool: {
    running: "助手正在汇总班级总览数据…",
    done: "班级总览数据已就绪",
    error: "班级总览数据未能完成"
  },
  build_comprehensive_report_data_tool: {
    running: "助手正在汇总综合分析数据…",
    done: "综合分析数据已就绪",
    error: "综合分析数据未能完成"
  },
  build_diagnostic_report_data_tool: {
    running: "助手正在整理诊断报告数据…",
    done: "诊断报告数据已就绪",
    error: "诊断报告数据未能完成"
  },
  build_tier_alert_report_data_tool: {
    running: "助手正在生成分层预警数据…",
    done: "分层预警数据已就绪",
    error: "分层预警数据未能完成"
  },
  build_group_feature_report_data_tool: {
    running: "助手正在分析群体特征…",
    done: "群体特征分析已就绪",
    error: "群体特征分析未能完成"
  },
  build_trend_tracking_report_data_tool: {
    running: "助手正在追踪成绩趋势…",
    done: "成绩趋势数据已就绪",
    error: "成绩趋势分析未能完成"
  },
  build_student_exam_report_data_tool: {
    running: "助手正在整理学生学情数据…",
    done: "学生学情数据已就绪",
    error: "学生学情数据未能完成"
  },
  build_student_subject_diagnosis_tool: {
    running: "助手正在诊断学生学科表现…",
    done: "学生学科诊断已完成",
    error: "学生学科诊断未能完成"
  },
  build_citywide_exam_analysis_report_tool: {
    running: "助手正在汇总全市考试分析…",
    done: "全市考试分析已就绪",
    error: "全市考试分析未能完成"
  },
  build_knowledge_tier_sections_tool: {
    running: "助手正在分析知识点分层…",
    done: "知识点分层已就绪",
    error: "知识点分层未能完成"
  },
  build_chart_option_tool: {
    running: "助手正在配置图表展示…",
    done: "图表配置已就绪",
    error: "图表配置未能完成"
  },
  select_report_template_tool: {
    running: "助手正在选择报告模板…",
    done: "报告模板已选定",
    error: "报告模板选择未完成"
  },
  compute_rankings_tool: {
    running: "助手正在计算排名…",
    done: "排名结果已就绪",
    error: "排名计算未能完成"
  },
  identify_at_risk_students_tool: {
    running: "助手正在识别需关注学生…",
    done: "需关注学生已识别",
    error: "需关注学生识别未完成"
  },
  aggregate_dimension_tool: {
    running: "助手正在做维度汇总…",
    done: "维度汇总已完成",
    error: "维度汇总未能完成"
  },
  cross_analyze_tool: {
    running: "助手正在做交叉分析…",
    done: "交叉分析已完成",
    error: "交叉分析未能完成"
  },
  resolve_score_schema: {
    running: "助手正在确认成绩表结构…",
    done: "成绩表结构已确认",
    error: "成绩表结构确认未完成"
  }
};

/** 未登记工具名时的语义兜底（按关键字） */
function inferToolCopy(key: string): ToolCopy | null {
  if (/class_overview/.test(key)) return TOOL_COPY.build_class_overview_report_data_tool;
  if (/subject_diagnosis_report/.test(key)) return TOOL_COPY.build_subject_diagnosis_report_tool;
  if (/subject_diagnosis_sections/.test(key)) return TOOL_COPY.build_subject_diagnosis_sections_tool;
  if (/subject_diagnosis|fetch_subject/.test(key)) return TOOL_COPY.fetch_subject_diagnosis_data_tool;
  if (/diagnostic_report|citywide/.test(key)) return TOOL_COPY.build_diagnostic_report_data_tool;
  if (/tier_alert|knowledge_tier/.test(key)) return TOOL_COPY.build_tier_alert_report_data_tool;
  if (/group_feature/.test(key)) return TOOL_COPY.build_group_feature_report_data_tool;
  if (/trend_tracking|trend/.test(key)) return TOOL_COPY.build_trend_tracking_report_data_tool;
  if (/student_exam|student_profile|student_subject/.test(key)) {
    return TOOL_COPY.build_student_exam_report_data_tool;
  }
  if (/comprehensive/.test(key)) return TOOL_COPY.build_comprehensive_report_data_tool;
  if (/score_stats|compute_score/.test(key)) return TOOL_COPY.compute_score_stats_tool;
  if (/ranking/.test(key)) return TOOL_COPY.compute_rankings_tool;
  if (/at_risk|risk_student/.test(key)) return TOOL_COPY.identify_at_risk_students_tool;
  if (/aggregate|dimension/.test(key)) return TOOL_COPY.aggregate_dimension_tool;
  if (/cross_analy/.test(key)) return TOOL_COPY.cross_analyze_tool;
  if (/chart_option|build_chart/.test(key)) return TOOL_COPY.build_chart_option_tool;
  if (/report_template|select_report/.test(key)) return TOOL_COPY.select_report_template_tool;
  if (/schema|resolve_score/.test(key)) return TOOL_COPY.resolve_score_schema;
  if (/report|render_html/.test(key)) return TOOL_COPY.render_html_report;
  return null;
}

/** 右侧阶段条 / 步骤文案用的 Agent 身份名 */
export const AGENT_IDENTITY: Record<string, string> = {
  Planner: "问题规划师",
  DataAnalyst: "数据分析专家",
  Charter: "可视化专家",
  Summarizer: "学情总结专家",
  ToolExpert: "工具专家"
};

const AGENT_NAME_RE = "Planner|DataAnalyst|Charter|Summarizer|ToolExpert";

export function agentIdentity(name?: string | null): string {
  if (!name) return "";
  const key = String(name).trim();
  const hit = Object.keys(AGENT_IDENTITY).find((k) => k.toLowerCase() === key.toLowerCase());
  return hit ? AGENT_IDENTITY[hit] : key;
}

/** 从步骤标题解析 Agent 英文名，如 `DataAnalyst: start` → DataAnalyst */
export function resolveStepAgent(title?: string | null): string | null {
  if (!title) return null;
  const m = String(title).match(new RegExp(`^(${AGENT_NAME_RE})\\s*:`, "i"));
  if (!m) return null;
  const hit = Object.keys(AGENT_IDENTITY).find((k) => k.toLowerCase() === m[1].toLowerCase());
  return hit ?? m[1];
}

const AGENT_COPY: Record<string, ToolCopy> = {
  Planner: {
    running: "问题规划师正在梳理分析思路…",
    done: "问题规划师已确定分析路径",
    error: "问题规划师未能完成规划"
  },
  DataAnalyst: {
    running: "数据分析专家正在深入分析数据…",
    done: "数据分析专家已完成本步分析",
    error: "数据分析专家遇到问题"
  },
  Charter: {
    running: "可视化专家正在准备图表展示…",
    done: "可视化专家已就绪图表方案",
    error: "可视化专家未能完成制图"
  },
  Summarizer: {
    running: "学情总结专家正在整理分析结论…",
    done: "学情总结专家已完成结论整理",
    error: "学情总结专家未能完成总结"
  },
  ToolExpert: {
    running: "工具专家正在调用分析工具…",
    done: "工具专家已完成本步工具调用",
    error: "工具专家调用未顺利完成"
  }
};

const FALLBACK: ToolCopy = {
  running: "助手正在处理中…",
  done: "本步骤已完成",
  error: "本步骤未顺利完成，正在继续尝试"
};

const toPhase = (status?: ExecutionStep["status"]): HumanizePhase => {
  if (status === "error") return "error";
  if (status === "running") return "running";
  return "done";
};

const normalizeToolKey = (raw: string): string =>
  raw
    .trim()
    .replace(/^调用工具:\s*/i, "")
    .replace(/^工具结果:\s*/i, "")
    .replace(/^调\s*/i, "")
    .replace(/\([\s\S]*$/, "")
    .replace(/\s+/g, "_")
    .toLowerCase();

/** 从步骤标题/计划文案中抽出英文工具名（含 `调用工具:` 前缀与 `xxx_tool(...)`） */
function extractToolName(text: string): string | null {
  const prefixed = text.match(/^(?:调用工具|工具结果):\s*(.+)$/i);
  if (prefixed) {
    const key = normalizeToolKey(prefixed[1]);
    return key || prefixed[1].trim();
  }
  const m =
    text.match(/\b([a-z][a-z0-9_]*_tool)\s*\(/i) ||
    text.match(/\b([a-z][a-z0-9_]*_tool)\b/i) ||
    text.match(
      /\b(execute_sql|list_tables|sample_rows|describe_table|render_html_report|resolve_score_schema)\b/i
    );
  return m?.[1] || null;
}

export function humanizeTool(
  tool: string,
  phase: HumanizePhase,
  ctx?: { rowCount?: number; title?: string }
): string {
  const key = normalizeToolKey(tool);
  const copy = TOOL_COPY[key] ?? inferToolCopy(key) ?? FALLBACK;
  if (key === "execute_sql" && phase === "done" && typeof ctx?.rowCount === "number") {
    return `已查询到 ${ctx.rowCount} 行数据`;
  }
  if ((key === "render_html_report" || /生成报告|报告/.test(tool)) && phase === "done" && ctx?.title) {
    return `报告《${ctx.title}》已生成`;
  }
  if ((key === "render_html_report" || /生成报告|报告/.test(tool)) && phase === "running" && ctx?.title) {
    return `助手正在生成报告《${ctx.title}》…`;
  }
  if (!TOOL_COPY[key] && !inferToolCopy(key) && /chart|图表/.test(tool.toLowerCase())) {
    return phase === "running"
      ? "助手正在选择合适的图表…"
      : phase === "error"
        ? "图表类型未能确定"
        : "图表类型已确定";
  }
  if (!TOOL_COPY[key] && !inferToolCopy(key) && /sql|生成\s*sql/i.test(tool)) {
    return phase === "running"
      ? "助手正在生成查询语句…"
      : phase === "error"
        ? "查询语句生成未完成"
        : "查询语句已准备就绪";
  }
  return copy[phase];
}

/** 步骤详情里去掉英文工具名 / Agent 英文名，保持与左侧人话一致 */
export function humanizeStepDetail(detail?: string): string {
  let text = (detail || "").trim();
  if (!text) return "";
  text = text
    .replace(/执行角色:\s*ToolExpert/gi, `执行角色: ${AGENT_IDENTITY.ToolExpert}`)
    .replace(/执行角色:\s*DataAnalyst/gi, `执行角色: ${AGENT_IDENTITY.DataAnalyst}`)
    .replace(/执行角色:\s*Planner/gi, `执行角色: ${AGENT_IDENTITY.Planner}`)
    .replace(/执行角色:\s*Charter/gi, `执行角色: ${AGENT_IDENTITY.Charter}`)
    .replace(/执行角色:\s*Summarizer/gi, `执行角色: ${AGENT_IDENTITY.Summarizer}`)
    .replace(/\bToolExpert\b/g, AGENT_IDENTITY.ToolExpert)
    .replace(/\bDataAnalyst\b/g, AGENT_IDENTITY.DataAnalyst)
    .replace(/\bPlanner\b/g, AGENT_IDENTITY.Planner)
    .replace(/\bCharter\b/g, AGENT_IDENTITY.Charter)
    .replace(/\bSummarizer\b/g, AGENT_IDENTITY.Summarizer);
  // `xxx_tool(...)` / 裸工具名 → 中文动作短语（去掉「助手正在」「…」）
  text = text.replace(/\b([a-z][a-z0-9_]*_tool)\s*\([^)]*\)?/gi, (_, name: string) => {
    return humanizeTool(name, "done").replace(/^已/, "").replace(/…$/, "") || "数据处理";
  });
  text = text.replace(/\b([a-z][a-z0-9_]*_tool)\b/gi, (_, name: string) => {
    return humanizeTool(name, "done").replace(/^已/, "").replace(/…$/, "") || "数据处理";
  });
  text = text.replace(
    /\b(execute_sql|list_tables|sample_rows|describe_table|render_html_report|resolve_score_schema)\b/gi,
    (name) => humanizeTool(name, "done").replace(/^已/, "") || "数据处理"
  );
  // 纯 JSON / 长参数不适合客户阅读
  if (/^\s*[\{\[]/.test(text) && text.length > 80) {
    return "已提交分析参数";
  }
  return text.replace(/[ \t]{2,}/g, " ").trim();
}

export function humanizeStepTitle(
  title: string,
  detail?: string,
  status?: ExecutionStep["status"]
): string {
  const phase = toPhase(status);
  const raw = (title || "").trim();
  if (!raw) return FALLBACK[phase];

  if (/工具调用失败|请求失败/.test(raw)) {
    return "处理未顺利完成，助手正在继续尝试";
  }
  if (/^准备执行计划|初始化规划/.test(raw)) {
    return phase === "error"
      ? `${AGENT_IDENTITY.Planner}未能完成规划`
      : phase === "done"
        ? `${AGENT_IDENTITY.Planner}已确定分析路径`
        : `${AGENT_IDENTITY.Planner}正在分析您的提问…`;
  }
  if (/^计划\s*\d+/i.test(raw) || raw.includes("子任务")) {
    const brief = raw.replace(/^计划\s*\d+:\s*/, "").trim();
    const toolInBrief = extractToolName(brief);
    const role =
      detail?.match(/执行角色:\s*(\w+)/i)?.[1] ||
      brief.match(/执行角色:\s*(\w+)/i)?.[1] ||
      "";
    const actor = agentIdentity(role) || AGENT_IDENTITY.DataAnalyst;
    if (toolInBrief) {
      const action = humanizeTool(toolInBrief, phase);
      if (phase === "running") return `${actor}：${action}`;
      if (phase === "error") return `${actor}：${action}`;
      return `${actor}：${action}`;
    }
    const scrubbed = humanizeStepDetail(brief);
    if (phase === "error") return scrubbed ? `${actor}未完成：${scrubbed}` : `${actor}执行未完成`;
    if (phase === "running") {
      return scrubbed ? `${actor}正在处理：${scrubbed}` : `${actor}正在抓紧分析…`;
    }
    return scrubbed ? `${actor}已完成：${scrubbed}` : `${actor}已完成本步`;
  }
  if (/^Agent\s*思考|^思考/i.test(raw)) {
    return phase === "running" ? "助手正在梳理分析思路…" : "分析思路已整理完成";
  }
  if (/图表推荐|chart/i.test(raw)) {
    return humanizeTool("chart", phase);
  }
  if (/生成\s*SQL|生成 SQL/i.test(raw)) {
    return humanizeTool("sql", phase);
  }
  if (/生成报告|报告/.test(raw)) {
    const reportTitle = (detail || "").replace(/\s*\(.*\)\s*$/, "").trim();
    return humanizeTool("render_html_report", phase, { title: reportTitle || undefined });
  }
  if (/^调用工具:|^工具结果:/i.test(raw)) {
    const tool = raw.replace(/^调用工具:\s*/i, "").replace(/^工具结果:\s*/i, "");
    const rowMatch = detail?.match(/返回\s*(\d+)\s*行|共\s*(\d+)\s*行|row_count["\s:=]+(\d+)/i);
    const rowCount = rowMatch
      ? Number(rowMatch[1] || rowMatch[2] || rowMatch[3])
      : undefined;
    return humanizeTool(tool, phase, {
      rowCount: Number.isFinite(rowCount) ? rowCount : undefined
    });
  }

  const agentMatch = raw.match(new RegExp(`^(${AGENT_NAME_RE})\\s*:\\s*(\\w+)`, "i"));
  if (agentMatch) {
    const agent =
      Object.keys(AGENT_IDENTITY).find((k) => k.toLowerCase() === agentMatch[1].toLowerCase()) ||
      agentMatch[1];
    const event = agentMatch[2].toLowerCase();
    // end 一律按完成文案；勿因步骤残留 status=running 仍显示「出马中」
    const agentPhase: HumanizePhase =
      event === "error" || phase === "error"
        ? "error"
        : event === "end" || event === "done"
          ? "done"
          : event === "start" && phase === "running"
            ? "running"
            : event === "start"
              ? "done"
              : phase;
    return (AGENT_COPY[agent] ?? FALLBACK)[agentPhase];
  }

  if (/^执行\s*SQL/i.test(raw)) {
    const rowMatch = detail?.match(/(\d+)\s*行/);
    const n = rowMatch ? Number(rowMatch[1]) : undefined;
    return humanizeTool("execute_sql", phase, { rowCount: n });
  }

  // 兜底：文案里夹带英文工具名时整段人话化
  const toolAnywhere = extractToolName(raw);
  if (toolAnywhere) {
    return humanizeTool(toolAnywhere, phase);
  }
  const cleaned = humanizeStepDetail(
    raw.replace(/^调用工具:\s*/i, "").replace(/^工具结果:\s*/i, "")
  );
  if (/^[a-z_][a-z0-9_]+$/i.test(cleaned)) {
    return humanizeTool(cleaned, phase);
  }
  return cleaned || FALLBACK[phase];
}

export type StoryLine = {
  id: string;
  text: string;
  status: ExecutionStep["status"];
  stepId: string;
};

/**
 * 动态故事章节：按真实执行顺序把同类动作聚成「几幕」，
 * 幕数/标题随问题变化，不写死固定五站。
 */
export type StoryPhase = {
  id: string;
  title: string;
  tip: string;
  status: "idle" | "running" | "done" | "error";
  count: number;
  stepId?: string;
};

type StoryKind = "plan" | "explore" | "query" | "report" | "wrap" | "other";

const TITLE_VARIANTS: Record<StoryKind, string[]> = {
  plan: ["已梳理本次分析思路", "已明确需要核查的问题", "已制定数据分析路径"],
  explore: ["已了解相关数据表结构", "已核验关键字段含义", "已完成数据基础探查"],
  query: ["已完成成绩数据查询", "已汇总所需统计结果", "已获取分析所需数据"],
  report: ["已生成分析报告", "已完成图表与报告整理", "已输出可视化分析结果"],
  wrap: ["已整理出分析结论", "已形成面向业务的总结"],
  other: ["已完成本阶段处理"]
};

const RUNNING_TITLE: Record<StoryKind, string> = {
  plan: "助手正在抓紧分析您的提问…",
  explore: "助手正在梳理数据表与字段…",
  query: "助手正在查询与核对成绩数据…",
  report: "助手正在生成分析报告…",
  wrap: "助手正在整理分析结论…",
  other: "助手正在处理中…"
};

function classifyKind(step: ExecutionStep): StoryKind | null {
  const title = step.title || "";
  if (/^Agent\s*思考/i.test(title)) return null;
  // agent start 不单独占一幕，等 end / 实质动作
  if (new RegExp(`^(${AGENT_NAME_RE})\\s*:\\s*start$`, "i").test(title)) return null;
  if (/^Planner\s*:/i.test(title) || /^准备执行计划/.test(title) || /^计划\s*\d+/i.test(title)) {
    return "plan";
  }
  if (/^Summarizer\s*:/i.test(title)) return "wrap";
  if (/^Charter\s*:/i.test(title) || /图表推荐|生成报告/.test(title)) return "report";
  if (/^DataAnalyst\s*:/i.test(title)) return "query";

  const tool = extractToolName(title)?.toLowerCase() || "";
  if (
    /list_tables|describe_table|sample_rows|find_related|resolve_.*schema|recent_questions/.test(tool)
  ) {
    return "explore";
  }
  if (
    /render_html|report|chart|build_.*report|build_.*diagnosis|build_.*trend|build_.*tier|build_.*group|build_.*comprehensive/.test(
      tool
    ) ||
    /报告/.test(title)
  ) {
    return "report";
  }
  if (/execute_sql|生成\s*SQL|执行\s*SQL/i.test(tool) || /生成\s*SQL|执行\s*SQL/.test(title)) {
    return "query";
  }
  if (tool) return "other";
  if (step.section === "plan") return "plan";
  if (step.section === "result" || step.section === "step") return "other";
  return null;
}

function clusterStatus(items: ExecutionStep[]): StoryPhase["status"] {
  if (!items.length) return "idle";
  if (items.some((s) => s.status === "error")) return "error";
  if (items.some((s) => s.status === "running")) return "running";
  return "done";
}

function pickVariant(kind: StoryKind, salt: number): string {
  const list = TITLE_VARIANTS[kind];
  return list[Math.abs(salt) % list.length];
}

function chapterTip(kind: StoryKind, items: ExecutionStep[], status: StoryPhase["status"]): string {
  if (status === "running") return "进行中，请稍候";
  if (status === "error") return "本阶段遇到问题，助手已继续尝试";

  if (kind === "plan") {
    const n = items.filter((s) => /^计划\s*\d+/i.test(s.title)).length;
    return n > 1 ? `已拆分为 ${n} 个子任务` : "分析路径已明确";
  }
  if (kind === "explore") {
    return items.length > 1 ? `已完成 ${items.length} 次结构/样例核验` : "数据基础信息已确认";
  }
  if (kind === "query") {
    const rows = items.map((s) => s.rowCount).filter((n): n is number => typeof n === "number");
    const maxRows = rows.length ? Math.max(...rows) : 0;
    const hits = Math.max(
      items.filter((s) => /execute_sql|执行\s*SQL/i.test(s.title) || s.rowCount != null).length,
      1
    );
    if (maxRows > 0) return `共查询 ${hits} 次，单次最多 ${maxRows} 行`;
    return `共完成 ${hits} 次数据查询`;
  }
  if (kind === "report") {
    const n = items.filter((s) => /报告|render|report|chart/i.test(s.title)).length;
    return n > 1 ? `已生成 ${n} 份图表/报告` : "报告已准备就绪";
  }
  if (kind === "wrap") return "结论可供直接参阅";
  return items.length > 1 ? `本阶段共处理 ${items.length} 项` : "本阶段已完成";
}

function mergeClusters(
  clusters: Array<{ kind: StoryKind; items: ExecutionStep[] }>
): Array<{ kind: StoryKind; items: ExecutionStep[] }> {
  // 幕太多时，合并相邻同 kind；仍多则把最短的 other/相邻幕并进邻居
  let list = clusters;
  const mergeAdjacent = () => {
    const next: typeof list = [];
    list.forEach((c) => {
      const prev = next[next.length - 1];
      if (prev && prev.kind === c.kind) {
        prev.items.push(...c.items);
      } else {
        next.push({ kind: c.kind, items: [...c.items] });
      }
    });
    list = next;
  };
  mergeAdjacent();
  while (list.length > 6) {
    // 找最长相邻可合并对，或合并最短幕到前一幕
    let minIdx = 1;
    for (let i = 1; i < list.length; i++) {
      if (list[i].items.length < list[minIdx].items.length) minIdx = i;
    }
    const into = minIdx === 0 ? 1 : minIdx - 1;
    list[into].items.push(...list[minIdx].items);
    list.splice(minIdx, 1);
    mergeAdjacent();
  }
  return list;
}

export function buildStoryPhases(
  steps: ExecutionStep[],
  opts?: { loading?: boolean; activity?: string }
): StoryPhase[] {
  const events: Array<{ kind: StoryKind; step: ExecutionStep }> = [];
  steps.forEach((step) => {
    const kind = classifyKind(step);
    if (!kind) return;
    events.push({ kind, step });
  });

  const rawClusters: Array<{ kind: StoryKind; items: ExecutionStep[] }> = [];
  events.forEach(({ kind, step }) => {
    const last = rawClusters[rawClusters.length - 1];
    if (last && last.kind === kind) {
      last.items.push(step);
    } else {
      rawClusters.push({ kind, items: [step] });
    }
  });

  const clusters = mergeClusters(rawClusters);
  const phases: StoryPhase[] = clusters.map((cluster, idx) => {
    const status = clusterStatus(cluster.items);
    const salt = cluster.items.length * 7 + idx * 3 + cluster.kind.length;
    const title =
      status === "running"
        ? RUNNING_TITLE[cluster.kind]
        : status === "error"
          ? "本阶段处理未完全成功"
          : pickVariant(cluster.kind, salt);
    const last =
      [...cluster.items].reverse().find((s) => s.status === "done" || s.status === "running") ||
      cluster.items[cluster.items.length - 1];
    return {
      id: `ch-${idx}-${cluster.kind}`,
      title,
      tip: chapterTip(cluster.kind, cluster.items, status),
      status,
      count: cluster.items.length,
      stepId: last?.id
    };
  });

  // 阶段间隙（如上一段已结束、下一段事件未到）时补一条进行中，避免界面像停滞
  const hasRunningPhase = phases.some((p) => p.status === "running");
  if (opts?.loading && !hasRunningPhase) {
    phases.push({
      id: "ch-pending",
      title: (opts.activity || "").trim() || "助手正在抓紧分析您的提问…",
      tip: "下一阶段准备中，请稍候",
      status: "running",
      count: 0
    });
  }

  return phases;
}

const rankStatus = (s: ExecutionStep["status"]): number =>
  s === "error" ? 3 : s === "done" ? 2 : s === "running" ? 1 : 0;

export function storyLines(steps: ExecutionStep[]): StoryLine[] {
  const lines: StoryLine[] = [];
  const toolIndex = new Map<string, number>();
  const agentIndex = new Map<string, number>();

  steps.forEach((step) => {
    // 思考合并为一行，避免刷屏；最终状态随 finalize 收口
    if (/^Agent\s*思考/i.test(step.title)) {
      const key = `think-${step.subTaskIndex ?? "x"}`;
      const existing = agentIndex.get(key);
      const line: StoryLine = {
        id: step.id,
        text: humanizeStepTitle(step.title, step.detail, step.status),
        status: step.status,
        stepId: step.id
      };
      if (existing != null) {
        if (rankStatus(step.status) >= rankStatus(lines[existing].status)) {
          lines[existing] = line;
        }
      } else {
        agentIndex.set(key, lines.length);
        lines.push(line);
      }
      return;
    }

    const agentMatch = step.title.match(new RegExp(`^(${AGENT_NAME_RE})\\s*:\\s*(\\w+)`, "i"));
    if (agentMatch) {
      const agent = agentMatch[1];
      const event = agentMatch[2].toLowerCase();
      const key = `agent-${agent}`;
      const existing = agentIndex.get(key);
      const status: ExecutionStep["status"] =
        event === "error" || step.status === "error"
          ? "error"
          : event === "end" || event === "done"
            ? "done"
            : step.status;
      const line: StoryLine = {
        id: step.id,
        text: humanizeStepTitle(step.title, step.detail, status),
        status,
        stepId: step.id
      };
      if (existing != null) {
        // end/error 覆盖 start，避免进行中状态残留
        if (rankStatus(status) >= rankStatus(lines[existing].status)) {
          lines[existing] = line;
        }
      } else {
        agentIndex.set(key, lines.length);
        lines.push(line);
      }
      return;
    }

    const tool = extractToolName(step.title);
    if (tool) {
      const key = `${step.subTaskIndex ?? "x"}-${step.round ?? "r"}-${tool}`;
      const existing = toolIndex.get(key);
      const line: StoryLine = {
        id: step.id,
        text: humanizeStepTitle(step.title, step.detail, step.status),
        status: step.status,
        stepId: step.id
      };
      if (existing != null) {
        lines[existing] = line;
      } else {
        toolIndex.set(key, lines.length);
        lines.push(line);
      }
      return;
    }
    lines.push({
      id: step.id,
      text: humanizeStepTitle(step.title, step.detail, step.status),
      status: step.status,
      stepId: step.id
    });
  });

  return lines;
}

export function summarizeRun(
  steps: ExecutionStep[],
  opts?: { loading?: boolean; activity?: string }
): {
  planCount: number;
  toolCount: number;
  reportCount: number;
  label: string;
  hasError: boolean;
  doneCount: number;
  totalCount: number;
} {
  const plans = steps.filter((s) => s.section === "plan" && !s.id.startsWith("plan-bootstrap"));
  const tools = steps.filter((s) => /^调用工具:|^工具结果:/i.test(s.title));
  const uniqueTools = new Set(
    tools.map((s) => `${s.subTaskIndex ?? "x"}-${s.round ?? "r"}-${extractToolName(s.title) || s.title}`)
  );
  const reports = steps.filter((s) => /生成报告|报告/.test(s.title) && !/^调用工具:|^工具结果:/i.test(s.title));
  const reportTools = tools.filter((s) => /render_html|report|报告/i.test(s.title));
  const reportCount = Math.max(reports.length, reportTools.filter((s) => s.title.startsWith("工具结果:")).length);
  const hasError = steps.some((s) => s.status === "error");
  const hasRunning = steps.some((s) => {
    if (s.status !== "running") return false;
    const m = s.title.match(new RegExp(`^(${AGENT_NAME_RE})\\s*:\\s*start$`, "i"));
    if (m) {
      return !steps.some((x) =>
        new RegExp(`^${m[1]}\\s*:\\s*(end|done)$`, "i").test(x.title)
      );
    }
    return true;
  });
  const planCount = plans.length;
  const toolCount = uniqueTools.size;
  const donePlans = plans.filter((s) => s.status === "done").length;
  const totalCount = planCount || storyLines(steps).length;
  const doneCount = planCount ? donePlans : storyLines(steps).filter((s) => s.status === "done").length;

  const phases = buildStoryPhases(steps, opts);
  const runningPhase = phases.find((p) => p.status === "running");

  let label: string;
  if (hasError && !opts?.loading) {
    label = "本轮分析遇到问题，详情见右侧工作台";
  } else if (opts?.loading || hasRunning || runningPhase) {
    // 请求未结束时绝不展示「已完成」，避免阶段间隙像卡住
    label =
      (opts?.activity || "").trim() ||
      runningPhase?.title ||
      "助手正在抓紧分析您的提问…";
  } else if (steps.length === 0) {
    label = "等待开始分析";
  } else {
    const settled = phases.filter((p) => p.id !== "ch-pending");
    const parts = [`已完成 ${settled.length} 个阶段`];
    if (toolCount) parts.push(`查询 ${toolCount} 次`);
    if (reportCount) parts.push(`报告 ${reportCount} 份`);
    label = parts.join(" · ");
  }

  return { planCount, toolCount, reportCount, label, hasError, doneCount, totalCount };
}

/** 拆出 &lt;think&gt; 块与对外正文（支持未闭合标签） */
export function splitThinkContent(raw: string): { thinkBlocks: string[]; plain: string } {
  const text = raw || "";
  const thinkBlocks: string[] = [];
  const thinkRegex = /<think>([\s\S]*?)(?:<\/think>|$)/gi;
  let m: RegExpExecArray | null;
  while ((m = thinkRegex.exec(text)) !== null) {
    const content = m[1]?.trim();
    if (content) thinkBlocks.push(content);
  }
  const plain = text
    .replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, "")
    .replace(/<\/?think>/gi, "")
    .trim();
  return { thinkBlocks, plain };
}

function clampThink(joined: string, max = 1200): string {
  const t = joined.trim();
  if (t.length > max) return `${t.slice(0, max)}…`;
  return t;
}

/**
 * 把 Agent 思考原文收成给人看的句子：去掉 tool/args JSON、tool_call 标签等。
 * 例：`{"thoughts":"先看表结构","tool":"resolve_score_schema","args":{...}}` → `先看表结构`
 */
export function humanizeThinkBlock(raw: string): string {
  let text = (raw || "").trim();
  if (!text) return "";

  // 去掉工具调用 XML/标签块
  text = text
    .replace(/<tool_call>[\s\S]*?(?:<\/tool_call>|$)/gi, "")
    .replace(/<\/?tool_call>/gi, "")
    .replace(/<tool_response>[\s\S]*?(?:<\/tool_response>|$)/gi, "")
    .trim();

  // 整段是 JSON（或夹在文本里的 JSON 对象）
  const tryParseThoughtJson = (candidate: string): string | null => {
    const s = candidate.trim();
    if (!s.startsWith("{") && !s.startsWith("[")) return null;
    try {
      const obj = JSON.parse(s) as unknown;
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
      const rec = obj as Record<string, unknown>;
      const thoughts =
        typeof rec.thoughts === "string"
          ? rec.thoughts.trim()
          : typeof rec.thought === "string"
            ? rec.thought.trim()
            : typeof rec.reasoning === "string"
              ? rec.reasoning.trim()
              : typeof rec.content === "string"
                ? rec.content.trim()
                : "";
      // 有 thoughts 就只留它；纯 tool/args 结构不当思考展示
      if (thoughts) return thoughts;
      if ("tool" in rec || "args" in rec || "name" in rec) return "";
      return null;
    } catch {
      return null;
    }
  };

  const asWhole = tryParseThoughtJson(text);
  if (asWhole !== null) return asWhole;

  // 正文中嵌入的 JSON 对象：抽 thoughts，去掉整段 JSON
  text = text.replace(/\{[\s\S]*?\}/g, (block) => {
    const parsed = tryParseThoughtJson(block);
    if (parsed === null) return block;
    return parsed;
  });

  // 残留的 tool/args 痕迹再清一遍
  text = text
    .replace(/"tool"\s*:\s*"[^"]*"/gi, "")
    .replace(/"args"\s*:\s*\{[\s\S]*?\}/g, "")
    .replace(/[{}\[\]]/g, " ")
    .replace(/\\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();

  // 仍像裸 JSON / 工具名堆砌则丢弃
  if (!text) return "";
  if (/^[\s{}"[\],:0-9.]+$/.test(text)) return "";
  if (/^(resolve_|execute_|build_|render_|list_|describe_|sample_)/i.test(text) && text.length < 80) {
    return "";
  }
  return text;
}

function normalizeThinkBlocks(blocks: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  blocks.forEach((b) => {
    const t = humanizeThinkBlock(b);
    if (!t || seen.has(t)) return;
    seen.add(t);
    out.push(t);
  });
  return out;
}

function isMdTableLine(line: string): boolean {
  const t = line.trim();
  if (!t.startsWith("|")) return false;
  return /^\|.+\|\s*$/.test(t) || /^\|[-:| \t]+\|\s*$/.test(t);
}

/**
 * 修复助手 Markdown，使 GFM 表格可被 remark-gfm 解析。
 * 常见问题：模型把多行表格粘成 `| a | | b |`（行间换行丢失）。
 * 表格行之间不能插空行，否则 GFM 会断开表格。
 */
export function normalizeAssistantMarkdown(md: string): string {
  let text = (md || "").replace(/\r\n/g, "\n");
  if (text.includes("\\n")) {
    text = text.replace(/\\n/g, "\n");
  }
  // 粘连行：`| 单元格 | | 下一行 |` → 断行；正常 `| 内容 | 内容 |` 不会命中
  text = text.replace(/\|\s+\|/g, "|\n|");

  const lines = text.split("\n");
  const out: string[] = [];
  for (const line of lines) {
    const prev = out.length ? out[out.length - 1] : undefined;
    if (isMdTableLine(line) && prev != null && prev.trim() !== "" && !isMdTableLine(prev)) {
      out.push("");
    }
    out.push(line);
  }
  return out.join("\n");
}

/** 判断一段文本是否像 SQL（用于剥离无语言标记的代码块） */
function looksLikeSql(body: string): boolean {
  const t = body.trim();
  if (!t) return false;
  if (/^(WITH|SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|EXPLAIN)\b/i.test(t)) return true;
  return /\bSELECT\b[\s\S]*\bFROM\b/i.test(t) && /\b(WHERE|GROUP\s+BY|ORDER\s+BY|JOIN|LIMIT)\b/i.test(t);
}

/**
 * 面向用户的结论里去掉专业技术 SQL：
 * - ```sql ... ``` / 无语言标记但内容像 SQL 的代码块
 * - 「SQL：」「最终 SQL」等标题及其后的裸 SELECT 段落
 */
export function stripTechnicalSql(text: string): string {
  let out = (text || "").replace(/\r\n/g, "\n");

  // fenced code blocks
  out = out.replace(/```([^\n`]*)\n([\s\S]*?)```/g, (_m, lang: string, body: string) => {
    const langKey = String(lang || "").trim().toLowerCase();
    if (langKey === "sql" || langKey.startsWith("sql") || (!langKey && looksLikeSql(body))) {
      return "";
    }
    return _m;
  });

  // 「SQL：」标题行（可带加粗）+ 后续连续 SQL 行
  out = out.replace(
    /(^|\n)#{0,3}\s*\*{0,2}\s*(最终\s*)?SQL\s*[：:]\s*\*{0,2}\s*\n+(?:```[\s\S]*?```|(?:[ \t]*(?:WITH|SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|GROUP|ORDER|HAVING|LIMIT|AND|OR|ON|AS|CASE|WHEN|THEN|END|COUNT|ROUND|COALESCE)[^\n]*\n?)+)/gi,
    "$1"
  );

  // 文末裸 SQL 段落（前面已是中文结论）
  out = out.replace(
    /\n{1,2}(?:WITH\b[\s\S]*?\bSELECT\b[\s\S]*|\bSELECT\b[\s\S]*?\bFROM\b[\s\S]*)$/i,
    (m) => (looksLikeSql(m) ? "" : m)
  );

  // 行内「SQL：SELECT ...」单行残留
  out = out.replace(/(^|\n)\s*\*{0,2}SQL\s*[：:]\s*\*{0,2}\s*(?:WITH|SELECT)\b[^\n]*/gi, "$1");

  return out
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}

/** 左侧气泡只展示给人看的结论，过滤 SQL / think / 执行完成等过程垃圾 */
export function pickCustomerAnswer(summary?: string, assistantContent?: string): string {
  const fromSummary = (summary || "").trim();
  if (fromSummary) {
    return normalizeAssistantMarkdown(stripTechnicalSql(splitThinkContent(fromSummary).plain));
  }
  const raw = (assistantContent || "").trim();
  if (!raw) return "";
  const { plain } = splitThinkContent(raw);
  if (!plain) return "";
  const cleaned = stripTechnicalSql(plain);
  if (!cleaned) return "";
  if (/^SQL[（(]/i.test(cleaned)) return "";
  if (/^执行完成[，,]/.test(cleaned)) return "";
  if (/^思考[：:]/.test(cleaned)) return "";
  if (/^\s*SELECT\b/i.test(cleaned)) return "";
  if (/返回\s*\d+\s*行结果/.test(cleaned) && /SELECT\b/i.test(cleaned)) return "";
  return normalizeAssistantMarkdown(cleaned);
}

export function extractThinkFromText(raw?: string): string {
  const { thinkBlocks } = splitThinkContent(raw || "");
  // 无 &lt;think&gt; 时，整段也可能是 thoughts JSON
  const blocks = thinkBlocks.length ? thinkBlocks : raw?.trim() ? [raw.trim()] : [];
  // 仅当原文像思考 JSON / 含 think 标签时才用人话抽取，避免把正式结论误收进折叠区
  const source = raw || "";
  const looksLikeThink =
    /<think>/i.test(source) ||
    /"thoughts"\s*:/i.test(source) ||
    /"tool"\s*:\s*"/i.test(source);
  if (!looksLikeThink && thinkBlocks.length === 0) return "";
  return clampThink(normalizeThinkBlocks(blocks).join("\n\n"));
}

export function extractThinkFromSteps(steps: ExecutionStep[]): string {
  const blocks: string[] = [];
  steps.forEach((s) => {
    if (/^Agent\s*思考/i.test(s.title) && s.detail?.trim()) {
      const nested = splitThinkContent(s.detail);
      const candidates = nested.thinkBlocks.length
        ? nested.thinkBlocks
        : [nested.plain || s.detail.trim()];
      candidates.forEach((c) => blocks.push(c));
      return;
    }
    const { thinkBlocks } = splitThinkContent(s.detail || "");
    thinkBlocks.forEach((t) => blocks.push(t));
  });
  return clampThink(normalizeThinkBlocks(blocks).join("\n\n"));
}

/** 合并答案里的 think 与步骤里的思考，供「分析思路」折叠区 */
export function mergeThinkText(...parts: Array<string | undefined>): string {
  const seen = new Set<string>();
  const out: string[] = [];
  parts.forEach((p) => {
    // 各 part 可能仍带 JSON，再收口一次
    const t = humanizeThinkBlock(p || "");
    if (!t || seen.has(t)) return;
    seen.add(t);
    out.push(t);
  });
  return clampThink(out.join("\n\n"));
}
