import { useCallback, useRef, useState } from "react";
import {
  createConversation,
  getConversationDetail,
  syncWorkspaceOid,
  sendMessageStream,
  updateReportReview,
  replaceRecordReports,
  type ClarifyPayload,
  type TaskPlanStage
} from "@/api/adapter/chatAdapter";
import { notifyConversationChanged, snippetConversationTitle } from "@/utils/conversationTitle";
import { genUUID } from "@/utils/uuid";
import { replaceRecommendationsHtml } from "@/utils/reportRecommendations";
import { humanizeStepTitle, humanizeTool } from "@/utils/toolLabels";
import {
  computeProgressPct,
  EMPTY_RUN_METRICS,
  metricsFromPersisted,
  type ProgressInput,
  type RunMetrics
} from "@/utils/runMetrics";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  runId?: string;
  createdAt?: number;
};

export type ExecutionStep = {
  id: string;
  title: string;
  detail?: string;
  status: "running" | "done" | "error";
  runId?: string;
  section?: "plan" | "step" | "result";
  subTaskIndex?: number;
  round?: number;
  progressPct?: number;
  rowCount?: number;
  elapsedMs?: number;
  toolName?: string;
  stageContent?: string;
  kind?: "thought";
};

export type ReportPayload = {
  title: string;
  html: string;
  mode?: string;
  subTaskIndex?: number;
  reportType?: string;
  reportTypeLabel?: string;
  /** 关联多轮对话中的某次运行，用于点击历史步骤时回显对应报告 */
  runId?: string;
  /** 落库后的 conversation record id，用于编辑/审核持久化 */
  recordId?: number;
  /** 该 record.reports 数组中的下标 */
  reportIndex?: number;
  /** pending=未审核；approved=已审核（不可再编辑） */
  reviewStatus?: "pending" | "approved";
};

export type QueryResult = {
  key: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  runId?: string;
};

export type ChartConfig = {
  x?: string;
  y?: string[];
  title?: string;
};

export type ChartRecommendation = {
  chartType?: string;
  chartConfig?: ChartConfig;
};

type SendOptions = {
  datasourceId?: number;
  reportAudience?: string;
};

const asText = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const asChartConfig = (raw: unknown): ChartConfig | undefined => {
  if (!raw || typeof raw !== "object") return undefined;
  const obj = raw as Record<string, unknown>;
  const x = typeof obj.x === "string" ? obj.x : "";
  const y = Array.isArray(obj.y) ? obj.y.map((item) => asText(item)).filter(Boolean) : [];
  const title = typeof obj.title === "string" ? obj.title : "";
  if (!x && !y.length && !title) return undefined;
  return { x, y, title };
};

const REPORT_TYPE_MARKERS = new Set([
  "class_overview",
  "grade_comparison",
  "subject_diagnosis",
  "student_profile",
  "trend_tracking",
  "tier_alert",
  "group_feature",
  "comprehensive",
  "diagnostic_report",
  "line_reach",
  "subject_avg",
  "assign_grade",
  "rank_bucket",
  "contribution",
  "combo_reach",
  "elite_roster",
  "score_band",
  "班级总览报告",
  "班级横向对比报告",
  "科目诊断报告",
  "学生学情报告",
  "成绩趋势报告",
  "分层预警报告",
  "群体特征报告",
  "综合分析报告",
  "结构化诊断报告",
  "全市达线分析",
  "均分情况分析",
  "选考等级分析",
  "高分位次分析",
  "贡献分分析",
  "选科组合达线",
  "高分名单分析",
  "分段统计"
]);

/** 报告列表/预览标题：报告名称【报告类型】 */
export const formatReportDisplayTitle = (title: string, typeLabel?: string): string => {
  let base = (title || "").trim();
  while (true) {
    const m = base.match(/^【([^】]+)】\s*/);
    if (!m || !REPORT_TYPE_MARKERS.has(m[1].trim())) break;
    base = base.slice(m[0].length).trim();
  }
  const tail = base.match(/[【（(]([^】）)]+)[】）)]\s*$/);
  if (tail && REPORT_TYPE_MARKERS.has(tail[1].trim())) {
    base = base.slice(0, tail.index).trim();
  }
  const label = (typeLabel || "").trim();
  if (!label) return base || "Report";
  if (!base) return label;
  if (base.includes(label)) return base;
  return `${base}【${label}】`;
};

export const extractReportFromToolData = (
  data: Record<string, unknown> | undefined,
  subTaskIndex?: number
): ReportPayload | null => {
  if (!data) return null;
  const html = asText(data.html).trim();
  const typeMeta = {
    reportType: data.report_type ? asText(data.report_type) : undefined,
    reportTypeLabel: data.report_type_label ? asText(data.report_type_label) : undefined
  };
  if (data.output_type === "html" && html) {
    return {
      title: formatReportDisplayTitle(asText(data.title) || "Report", typeMeta.reportTypeLabel),
      html,
      mode: data.mode ? asText(data.mode) : undefined,
      subTaskIndex,
      reviewStatus: "pending",
      ...typeMeta
    };
  }
  const chunks = data.chunks;
  if (!Array.isArray(chunks)) return null;
  for (const chunk of chunks) {
    if (!chunk || typeof chunk !== "object") continue;
    const c = chunk as Record<string, unknown>;
    if (c.output_type !== "html") continue;
    const chunkHtml = asText(c.content).trim();
    if (!chunkHtml) continue;
    return {
      title: formatReportDisplayTitle(
        asText(c.title) || asText(data.title) || "Report",
        typeMeta.reportTypeLabel
      ),
      html: chunkHtml,
      mode: data.mode ? asText(data.mode) : undefined,
      subTaskIndex,
      reviewStatus: "pending",
      ...typeMeta
    };
  }
  return null;
};

const appendReportIfNew = (prev: ReportPayload[], report: ReportPayload): ReportPayload[] => {
  if (!report.html.trim()) return prev;
  if (prev.some((r) => r.html === report.html && (r.runId ?? "") === (report.runId ?? ""))) return prev;
  return [...prev, report];
};

const deriveReportsFromRecord = (record: {
  id?: number;
  reports?: Array<{
    title?: string;
    html?: string;
    mode?: string;
    sub_task_index?: number;
    report_type?: string;
    report_type_label?: string;
    review_status?: string;
    [key: string]: unknown;
  }>;
  tool_calls?: Array<{
    sub_task_index?: number;
    data?: {
      output_type?: string;
      html?: string;
      title?: string;
      mode?: string;
      chunks?: Array<{ output_type?: string; content?: string; title?: string }>;
    };
  }>;
}): ReportPayload[] => {
  const reports: ReportPayload[] = [];
  if (Array.isArray(record.reports)) {
    record.reports.forEach((r, idx) => {
      const html = asText(r?.html).trim();
      if (!html) return;
      const status = r?.review_status === "approved" ? "approved" : "pending";
      reports.push({
        title: asText(r?.title) || "Report",
        html,
        mode: r?.mode ? asText(r.mode) : undefined,
        subTaskIndex: r?.sub_task_index,
        reportType: r?.report_type ? asText(r.report_type) : undefined,
        reportTypeLabel: r?.report_type_label ? asText(r.report_type_label) : undefined,
        reportIndex: idx,
        reviewStatus: status
      });
    });
  }
  if (reports.length) return reports;
  if (!Array.isArray(record.tool_calls)) return reports;
  let merged = reports;
  record.tool_calls.forEach((call) => {
    const extracted = extractReportFromToolData(
      call?.data as Record<string, unknown> | undefined,
      call.sub_task_index
    );
    if (extracted) merged = appendReportIfNew(merged, extracted);
  });
  return merged;
};

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [executionSteps, setExecutionSteps] = useState<ExecutionStep[]>([]);
  const [summary, setSummary] = useState("");
  const [summaryByRunId, setSummaryByRunId] = useState<Record<string, string>>({});
  const [reports, setReports] = useState<ReportPayload[]>([]);
  const [queryResults, setQueryResults] = useState<QueryResult[]>([]);
  const [chartByRunId, setChartByRunId] = useState<Record<string, ChartRecommendation>>({});
  const [loading, setLoading] = useState(false);
  const [activity, setActivity] = useState("");
  const [runMetrics, setRunMetrics] = useState<RunMetrics>(EMPTY_RUN_METRICS);
  const [metricsByRunId, setMetricsByRunId] = useState<Record<string, RunMetrics>>({});
  const [clarifyByRunId, setClarifyByRunId] = useState<Record<string, ClarifyPayload>>({});
  const [taskPlanByRunId, setTaskPlanByRunId] = useState<Record<string, TaskPlanStage[]>>({});
  const [conversationId, setConversationId] = useState<number | undefined>(undefined);
  const [conversationTitle, setConversationTitle] = useState("");
  /** 当前进行中的一轮 runId，用于隔离各轮流式事件。 */
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const interruptRef = useRef<{ conversationId: number; id: string } | undefined>(undefined);
  const sendingRef = useRef(false);
  const activeRunIdRef = useRef<string | null>(null);
  const metricsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const progressRef = useRef<ProgressInput>({
    planCount: 0,
    donePlans: 0,
    runningPlans: 0,
    plannerDone: false,
    charterDone: false,
    hasChartOrReport: false,
    summarizerDone: false,
    hasSummary: false,
    toolDoneCount: 0,
    finished: false
  });
  const planDoneIdxRef = useRef<Set<number>>(new Set());
  const defaultDatasourceId = Number(process.env.NEXT_PUBLIC_DEFAULT_DATASOURCE_ID ?? 1);
  const [datasourceId, setDatasourceIdState] = useState<number>(defaultDatasourceId);
  const [reportAudience, setReportAudience] = useState<string | undefined>(undefined);

  const setDatasourceId = useCallback((id: number) => {
    setDatasourceIdState((prev) => {
      if (prev !== id) {
        setConversationId(undefined);
        setConversationTitle("");
      }
      return id;
    });
  }, []);

  const clearMetricsTimer = useCallback(() => {
    if (metricsTimerRef.current) {
      clearInterval(metricsTimerRef.current);
      metricsTimerRef.current = null;
    }
  }, []);

  const refreshProgress = useCallback((patch?: Partial<ProgressInput>) => {
    if (patch) {
      progressRef.current = { ...progressRef.current, ...patch };
    }
    const pct = computeProgressPct(progressRef.current);
    setRunMetrics((prev) => ({ ...prev, progressPct: pct }));
  }, []);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    const stoppedRunId = activeRunIdRef.current;
    // 作废当前 run，避免被中止的旧流在 finally 里误清掉新一轮的计时/loading
    activeRunIdRef.current = null;
    sendingRef.current = false;
    clearMetricsTimer();
    setLoading(false);
    setActivity("");
    setRunMetrics((prev) => {
      const next: RunMetrics = {
        ...prev,
        elapsedMs: prev.runStartedAt ? Date.now() - prev.runStartedAt : prev.elapsedMs,
        elapsedKnown: Boolean(prev.runStartedAt) || prev.elapsedKnown,
        progressPct: Math.max(prev.progressPct, 0),
        // 停止本轮计时。
        runStartedAt: null
      };
      if (stoppedRunId) {
        setMetricsByRunId((m) => ({
          ...m,
          [stoppedRunId]: { ...next, progressPct: 100 }
        }));
      }
      return next;
    });
  }, [clearMetricsTimer]);

  const ensureConversation = useCallback(async (targetDatasourceId: number, question: string, signal: AbortSignal) => {
    if (conversationId) return conversationId;
    const created = await createConversation({
      title: snippetConversationTitle(question),
      datasource_id: targetDatasourceId
    });
    if (!signal.aborted) {
      setConversationId(created.id);
      setConversationTitle(created.title || snippetConversationTitle(question));
    }
    notifyConversationChanged();
    return created.id;
  }, [conversationId]);

  const send = useCallback(
    async (input: string, options?: SendOptions) => {
      if (!input.trim()) return;
      if (sendingRef.current) return;
      sendingRef.current = true;
      const targetDatasourceId = options?.datasourceId ?? datasourceId;
      const targetAudience = options?.reportAudience ?? reportAudience;
      stop();
      // stop() 会清 sendingRef；立刻重新占住，避免 effect 重跑时同一问再发一遍
      sendingRef.current = true;
      const runId = genUUID();
      activeRunIdRef.current = runId;
      const userMsg: Message = { id: genUUID(), role: "user", content: asText(input), runId, createdAt: Date.now() };
      const assistantId = genUUID();
      setMessages((prev) => [...prev, userMsg, { id: assistantId, role: "assistant", content: "", runId }]);
      setLoading(true);
      setActivity("助手正在抓紧分析您的提问…");
      progressRef.current = {
        planCount: 0,
        donePlans: 0,
        runningPlans: 0,
        plannerDone: false,
        charterDone: false,
        hasChartOrReport: false,
        summarizerDone: false,
        hasSummary: false,
        toolDoneCount: 0,
        finished: false
      };
      planDoneIdxRef.current = new Set();
      const startedAt = Date.now();
      clearMetricsTimer();
      setRunMetrics({
        totalTokens: 0,
        tokenKnown: false,
        elapsedMs: 0,
        elapsedKnown: true,
        progressPct: 5,
        runStartedAt: startedAt
      });
      metricsTimerRef.current = setInterval(() => {
        setRunMetrics((prev) =>
          prev.runStartedAt
            ? { ...prev, elapsedMs: Date.now() - prev.runStartedAt }
            : prev
        );
      }, 200);

      const controller = new AbortController();
      abortRef.current = controller;
      const bootstrapId = `plan-bootstrap-${runId}`;
      setExecutionSteps((prev) => [
        ...prev,
        {
          id: bootstrapId,
          title: "准备执行计划",
          detail: "助手正在抓紧分析您的提问…",
          status: "running",
          runId,
          section: "plan"
        }
      ]);
      // 多轮对话保留历史报告/查询结果；本轮摘要待生成时先置空显示槽位
      setSummary("");

      let latest = "";
      let latestSql = "";
      const stripBootstrap = (prev: ExecutionStep[]) => prev.filter((s) => s.id !== bootstrapId);
      const withRun = <T extends object>(item: T): T & { runId: string } => ({ ...item, runId });
      const writeAssistant = (content: string) => {
        latest = content;
        setMessages((prev) => prev.map((msg) => (msg.id === assistantId ? { ...msg, content, createdAt: Date.now() } : msg)));
      };
      /** 收束本轮仍为 running 的步骤（如 Planner: start），避免左侧一直转圈 */
      const finalizeRunSteps = () => {
        setExecutionSteps((prev) =>
          prev.map((step) =>
            step.runId === runId && step.status === "running" ? { ...step, status: "done" as const } : step
          )
        );
      };

      let streamAborted = false;
      try {
        const convId = await ensureConversation(targetDatasourceId, input, controller.signal);
        if (activeRunIdRef.current !== runId) {
          streamAborted = true;
          return;
        }
        const interrupt = interruptRef.current?.conversationId === convId ? interruptRef.current.id : undefined;
        let nextInterrupt: string | undefined;
        const streamResult = await sendMessageStream(
          {
            question: input,
            datasource_id: targetDatasourceId,
            conversation_id: convId,
            resume_interrupt_id: interrupt,
            ...(targetAudience ? { report_audience: targetAudience } : {})
          },
          {
            onReasoning: (text) => {
              // 思考过程走步骤流 / 折叠区，不塞进左侧助手气泡（避免刷屏）
              const safeText = asText(text);
              if (!safeText.trim()) return;
              setExecutionSteps((prev) => [
                ...prev,
                {
                  id: genUUID(),
                  title: "分析思路",
                  kind: "thought",
                  detail: safeText,
                  status: "running",
                  runId,
                  section: "step"
                }
              ]);
              setActivity("助手正在梳理分析思路…");
            },
            onTaskPlan: (stages) => {
              setTaskPlanByRunId((prev) => ({ ...prev, [runId]: stages }));
            },
            onPlan: ({ plans, sub_task_agents }) => {
              if (!plans?.length) return;
              setActivity(`分析路径已确定，共 ${plans.length} 个子任务`);
              refreshProgress({
                planCount: plans.length,
                plannerDone: true,
                runningPlans: plans.length,
                donePlans: 0
              });
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                ...plans.map((p, idx) => ({
                  id: `plan-${runId}-${idx}`,
                  title: `计划 ${idx + 1}: ${p}`,
                  detail: sub_task_agents?.[idx] ? `执行角色: ${sub_task_agents[idx]}` : "",
                  status: "running" as const,
                  runId,
                  section: "plan" as const,
                  subTaskIndex: idx,
                  progressPct: 0
                }))
              ]);
            },
            onPlanUpdate: (payload) => {
              if (payload.index < 0) {
                setExecutionSteps((prev) => stripBootstrap(prev));
                return;
              }
              const nextStatus: ExecutionStep["status"] =
                payload.state === "ok" ? "done" : payload.state === "error" ? "error" : "running";
              if (payload.state === "ok" || payload.state === "error") {
                planDoneIdxRef.current.add(payload.index);
                const done = planDoneIdxRef.current.size;
                const total = Math.max(progressRef.current.planCount, done);
                refreshProgress({
                  planCount: total,
                  donePlans: done,
                  runningPlans: Math.max(0, total - done)
                });
              } else if (payload.state === "running") {
                refreshProgress({
                  runningPlans: Math.max(1, progressRef.current.planCount - planDoneIdxRef.current.size)
                });
              }
              setExecutionSteps((prev) => {
                const base = stripBootstrap(prev);
                const planId = `plan-${runId}-${payload.index}`;
                const found = base.some((step) => step.id === planId);
                const nextDetail =
                  payload.state === "error"
                    ? payload.error || ""
                    : payload.sql
                      ? `${payload.sub_task_agent ? `执行角色: ${payload.sub_task_agent}\n` : ""}SQL 已生成，返回 ${payload.row_count ?? 0} 行`
                      : payload.sub_task_agent
                        ? `执行角色: ${payload.sub_task_agent}`
                        : "";
                if (!found) {
                  return [
                    ...base,
                    {
                      id: planId,
                      title: `计划 ${payload.index + 1}: ${payload.sub_task || `子任务 ${payload.index + 1}`}`,
                      detail: nextDetail,
                      status: nextStatus,
                      runId,
                      section: "plan",
                      subTaskIndex: payload.index,
                      progressPct: payload.state === "ok" ? 100 : payload.state === "error" ? 0 : 0,
                      rowCount: payload.row_count
                    }
                  ];
                }
                return base.map((step) =>
                  step.id === planId
                    ? {
                        ...step,
                        title: payload.sub_task ? `计划 ${payload.index + 1}: ${payload.sub_task}` : step.title,
                        status: nextStatus,
                        detail: nextDetail || step.detail,
                        progressPct:
                          payload.state === "ok"
                            ? 100
                            : payload.state === "error"
                              ? step.progressPct ?? 0
                              : step.progressPct ?? 0,
                        rowCount: payload.row_count ?? step.rowCount
                      }
                    : step
                );
              });
            },
            onStep: (step) => {
              if (!step?.label) return;
              if (step.name === "deepagents_model") {
                setActivity(step.label);
                return;
              }
              if (step.name === "deepagents") return;
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: genUUID(),
                  title: asText(step.label),
                  kind: step.name === "analysis_update" ? "thought" : undefined,
                  detail: asText(step.detail),
                  status: step.status === "error" ? "error" : step.status === "running" ? "running" : "done",
                  round: step.round,
                  runId,
                  section: "step"
                }
              ]);
            },
            onAgentSpeak: ({ agent, status, error }) => {
              if (!agent || !status) return;
              const stepStatus =
                status === "error" ? "error" : status === "start" ? "running" : "done";
              setActivity(
                humanizeStepTitle(`${asText(agent)}: ${asText(status)}`, asText(error), stepStatus)
              );
              const agentName = asText(agent);
              const event = asText(status).toLowerCase();
              if (event === "end" || event === "done") {
                if (/^Planner$/i.test(agentName)) refreshProgress({ plannerDone: true });
                if (/^Charter$/i.test(agentName)) refreshProgress({ charterDone: true });
                if (/^Summarizer$/i.test(agentName)) refreshProgress({ summarizerDone: true });
              }
              setExecutionSteps((prev) => [
                ...prev,
                {
                  id: genUUID(),
                  title: `${asText(agent)}: ${asText(status)}`,
                  detail: asText(error),
                  status: stepStatus,
                  runId,
                  section: "step"
                }
              ]);
            },
            onChart: ({ chart_type, chart_config }) => {
              if (!chart_type && !chart_config) return;
              refreshProgress({ hasChartOrReport: true, charterDone: true });
              setActivity(humanizeTool("chart", "done"));
              const rec: ChartRecommendation = {
                chartType: chart_type || undefined,
                chartConfig: asChartConfig(chart_config)
              };
              setChartByRunId((prev) => ({ ...prev, [runId]: rec }));
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: genUUID(),
                  title: "图表推荐",
                  detail: `推荐图表类型: ${chart_type || "table"}`,
                  status: "done",
                  runId,
                  section: "result"
                }
              ]);
            },
            onReport: ({ title, html, mode, sub_task_index, report_type, report_type_label }) => {
              refreshProgress({ hasChartOrReport: true });
              const typeLabel = report_type_label ? asText(report_type_label) : undefined;
              const normalizedTitle = formatReportDisplayTitle(asText(title) || "Report", typeLabel);
              const report: ReportPayload = withRun({
                title: normalizedTitle,
                html: asText(html),
                mode: mode ? asText(mode) : undefined,
                subTaskIndex: sub_task_index,
                reportType: report_type ? asText(report_type) : undefined,
                reportTypeLabel: typeLabel,
                reviewStatus: "pending"
              });
              setActivity(humanizeTool("render_html_report", "done", { title: normalizedTitle }));
              setReports((prev) => appendReportIfNew(prev, report));
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: genUUID(),
                  title: "生成报告",
                  detail: `${normalizedTitle}${mode ? ` (${asText(mode)})` : ""}`,
                  status: "done",
                  runId,
                  section: "result",
                  subTaskIndex: sub_task_index
                }
              ]);
            },
            onAgentThought: ({ text, sub_task_index }) => {
              const safeText = asText(text);
              if (!safeText.trim()) return;
              setActivity("助手正在梳理分析思路…");
              setExecutionSteps((prev) => [
                ...prev,
                {
                  id: genUUID(),
                  title: "分析思路",
                  kind: "thought",
                  detail: safeText,
                  status: "running",
                  runId,
                  section: "step",
                  subTaskIndex: sub_task_index
                }
              ]);
            },
            onToolCall: ({ tool, round, sub_task, sub_task_index }) => {
              setActivity(humanizeTool(asText(tool), "running"));
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: `tool-${runId}-${sub_task_index ?? -1}-${round ?? prev.length}-${tool}`,
                  title: `调用工具: ${asText(tool)}`,
                  toolName: asText(tool),
                  stageContent: sub_task,
                  status: "running",
                  runId,
                  section: "step",
                  subTaskIndex: sub_task_index,
                  round
                }
              ]);
            },
            onToolResult: ({ tool, success, data, round, sub_task, sub_task_index, elapsed_ms }) => {
              const safeTool = asText(tool);
              const rowCount =
                data && typeof data === "object" && typeof data.row_count === "number"
                  ? data.row_count
                  : data && typeof data === "object" && Array.isArray(data.rows)
                    ? data.rows.length
                    : undefined;
              if (success) {
                refreshProgress({ toolDoneCount: progressRef.current.toolDoneCount + 1 });
              }
              setActivity(
                humanizeTool(safeTool, success ? "done" : "error", {
                  rowCount: typeof rowCount === "number" ? rowCount : undefined
                })
              );
              if (safeTool === "execute_sql" && data && typeof data === "object") {
                const rawColumns = Array.isArray(data.columns) ? data.columns : [];
                const rawRows = Array.isArray(data.rows) ? data.rows : [];
                const safeColumns = rawColumns.map((col) => asText(col));
                if (safeColumns.length && rawRows.length) {
                  setQueryResults((prev) => [
                    ...prev,
                    withRun({
                      key: genUUID(),
                      sql: asText(data.sql) || latestSql,
                      columns: safeColumns,
                      rows: rawRows,
                      rowCount: typeof data.row_count === "number" ? data.row_count : rawRows.length
                    })
                  ]);
                }
              }
              const id = `tool-${runId}-${sub_task_index ?? -1}-${round ?? -1}-${safeTool}`;
              if (data && typeof data === "object") {
                const extracted = extractReportFromToolData(data as Record<string, unknown>, sub_task_index);
                if (extracted) {
                  setReports((prev) => appendReportIfNew(prev, withRun(extracted)));
                }
              }
              setExecutionSteps((prev) => {
                const base = stripBootstrap(prev);
                const found = base.some((step) => step.id === id);
                if (found) {
                  return base.map((step) =>
                    step.id === id
                      ? {
                          ...step,
                          title: `工具结果: ${safeTool}`,
                          toolName: safeTool,
                          elapsedMs: elapsed_ms,
                          status: success ? "done" : "error",
                          runId,
                          section: "result",
                          rowCount: typeof rowCount === "number" ? rowCount : step.rowCount
                        }
                      : step
                  );
                }
                return [
                  ...base,
                  {
                    id,
                    title: `工具结果: ${safeTool}`,
                    toolName: safeTool,
                    stageContent: sub_task,
                    elapsedMs: elapsed_ms,
                    status: success ? "done" : "error",
                    runId,
                    section: "result",
                    subTaskIndex: sub_task_index,
                    round,
                    rowCount: typeof rowCount === "number" ? rowCount : undefined
                  }
                ];
              });
            },
            onSql: (sql) => {
              latestSql = asText(sql);
            },
            onResult: (result) => {
              const rowCount = result?.row_count ?? 0;
              const safeColumns = Array.isArray(result?.columns) ? result.columns.map((col) => asText(col)) : [];
              const safeRows = Array.isArray(result?.rows) ? result.rows : [];
              if (safeColumns.length && safeRows.length) {
                setQueryResults((prev) => [
                  ...prev,
                  withRun({
                    key: genUUID(),
                    sql: latestSql,
                    columns: safeColumns,
                    rows: safeRows,
                    rowCount
                  })
                ]);
              }
              setActivity(humanizeTool("execute_sql", "done", { rowCount }));
            },
            onFinalAnswer: (content) => {
              const safeContent = asText(content);
              if (safeContent.trim()) writeAssistant(safeContent);
            },
            onSummary: (content) => {
              const safeContent = asText(content);
              if (safeContent.trim()) {
                refreshProgress({ hasSummary: true, summarizerDone: true });
                setActivity("助手正在整理分析结论…");
                writeAssistant(safeContent);
                setSummary(safeContent);
                setSummaryByRunId((prev) => ({ ...prev, [runId]: safeContent }));
              }
            },
            onClarify: (payload) => {
              nextInterrupt = payload.interrupt_id;
              const prompt = asText(payload.prompt);
              if (prompt.trim()) {
                writeAssistant(prompt);
                setSummary(prompt);
                setSummaryByRunId((prev) => ({ ...prev, [runId]: prompt }));
              }
              setClarifyByRunId((prev) => ({ ...prev, [runId]: payload }));
              setActivity("请补充分析范围");
            },
            onUsage: (payload) => {
              const total =
                typeof payload.total_tokens === "number"
                  ? payload.total_tokens
                  : (payload.prompt_tokens ?? 0) + (payload.completion_tokens ?? 0);
              if (total <= 0 && payload.total_tokens == null) return;
              setRunMetrics((prev) => ({
                ...prev,
                totalTokens: total,
                tokenKnown: true
              }));
            },
            onError: (msg) => {
              const safeMsg = asText(msg);
              setActivity("本轮分析遇到问题，请查看右侧详情");
              writeAssistant(`请求失败：${safeMsg}`);
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev).map((step) =>
                  step.runId === runId && step.status === "running"
                    ? { ...step, status: "error" as const, detail: safeMsg || step.detail }
                    : step
                ),
                {
                  id: `tool-error-${genUUID()}`,
                  title: "工具调用失败",
                  detail: safeMsg,
                  status: "error",
                  runId,
                  section: "step"
                }
              ]);
            },
            onDone: async (recordId) => {
              interruptRef.current = nextInterrupt && convId ? { conversationId: convId, id: nextInterrupt } : undefined;
              finalizeRunSteps();
              clearMetricsTimer();
              progressRef.current = { ...progressRef.current, finished: true };
              setRunMetrics((prev) => {
                const next: RunMetrics = {
                  ...prev,
                  elapsedMs: prev.runStartedAt ? Date.now() - prev.runStartedAt : prev.elapsedMs,
                  elapsedKnown: true,
                  progressPct: 100,
                  runStartedAt: null
                };
                setMetricsByRunId((m) => ({
                  ...m,
                  [runId]: { ...next }
                }));
                return next;
              });
              setLoading(false);
              setActivity("");
              // 注意：不要把报告 runId 改成 record-*，否则与当前步骤的流式 runId 对不上，
              // 摘要区 scopedReports 会被滤空，必须刷新后才能看到。
              if (recordId > 0) {
                setReports((prev) => {
                  let idx = 0;
                  return prev.map((r) => {
                    if (r.runId !== runId) return r;
                    return {
                      ...r,
                      recordId,
                      reportIndex: idx++,
                      reviewStatus: r.reviewStatus || "pending"
                    };
                  });
                });
              }
              if (recordId > 0 && convId) {
                try {
                  const detail = await getConversationDetail(convId);
                  const record =
                    detail.records?.find((r) => r.id === recordId) ??
                    detail.records?.[detail.records.length - 1];
                  if (record) {
                    // 优先用服务端落库的 token/耗时覆盖本轮指标（与刷新历史一致）
                    if (record.total_tokens != null || record.elapsed_ms != null) {
                      const persisted = metricsFromPersisted(record.total_tokens, record.elapsed_ms);
                      setMetricsByRunId((m) => ({ ...m, [runId]: persisted }));
                      setRunMetrics((prev) => ({
                        ...prev,
                        totalTokens: persisted.tokenKnown ? persisted.totalTokens : prev.totalTokens,
                        tokenKnown: persisted.tokenKnown || prev.tokenKnown,
                        elapsedMs: persisted.elapsedKnown ? persisted.elapsedMs : prev.elapsedMs,
                        elapsedKnown: persisted.elapsedKnown || prev.elapsedKnown,
                        progressPct: 100
                      }));
                    }
                    const derived = deriveReportsFromRecord(record).map((r, i) =>
                      withRun({
                        ...r,
                        recordId: record.id,
                        reportIndex: r.reportIndex ?? i,
                        // 保留当前会话 runId，便于右侧摘要立即可见
                        reviewStatus: r.reviewStatus || "pending"
                      })
                    );
                    if (derived.length) {
                      setReports((prev) => {
                        const byKey = new Map(
                          prev
                            .filter(
                              (r) =>
                                r.runId === runId ||
                                r.recordId === recordId ||
                                r.runId === `record-${recordId}`
                            )
                            .map((r) => [`${r.title}::${r.subTaskIndex ?? ""}`, r])
                        );
                        let next = prev.filter(
                          (r) =>
                            r.runId !== runId &&
                            r.recordId !== recordId &&
                            r.runId !== `record-${recordId}`
                        );
                        const mergedForPersist: ReportPayload[] = [];
                        for (const d of derived) {
                          const local = byKey.get(`${d.title}::${d.subTaskIndex ?? ""}`);
                          const merged: ReportPayload = {
                            ...d,
                            html: local?.html || d.html,
                            reviewStatus: local?.reviewStatus || d.reviewStatus || "pending",
                            recordId: record.id,
                            reportIndex: d.reportIndex,
                            runId
                          };
                          mergedForPersist.push(merged);
                          next = appendReportIfNew(next, merged);
                        }
                        void replaceRecordReports({
                          conversationId: convId,
                          recordId: record.id,
                          reports: mergedForPersist.map((r) => ({
                            title: r.title,
                            html: r.html,
                            mode: r.mode,
                            sub_task_index: r.subTaskIndex,
                            report_type: r.reportType,
                            report_type_label: r.reportTypeLabel,
                            review_status: r.reviewStatus || "pending"
                          }))
                        }).catch(() => undefined);
                        return next;
                      });
                    }
                  }
                } catch {
                  // 忽略同步失败，onReport / onToolResult 已尽力填充
                }
              }
            }
          },
          controller.signal
        );
        streamAborted = Boolean(streamResult?.aborted);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (activeRunIdRef.current === runId) {
          writeAssistant(`请求失败：${asText(msg)}`);
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        // 仅当前仍有效的 run 才收口，避免旧流 Abort 后误停新一轮计时/进度
        if (activeRunIdRef.current !== runId) {
          return;
        }
        sendingRef.current = false;
        if (streamAborted) {
          // 主动中止：保留步骤原状态，由 stop() 负责 UI
          return;
        }
        // 步骤收口交给 onDone（finalizeRunSteps）；此处只停表并解除 loading。
        // 若在 finally 里把 running→done，会把「准备执行计划」误显示成「已制定路径」。
        clearMetricsTimer();
        setRunMetrics((prev) => {
          const next: RunMetrics = {
            ...prev,
            elapsedMs: prev.runStartedAt ? Date.now() - prev.runStartedAt : prev.elapsedMs,
            elapsedKnown: true,
            runStartedAt: null
          };
          setMetricsByRunId((m) => ({
            ...m,
            [runId]: { ...next, progressPct: Math.max(next.progressPct, 100) }
          }));
          return next;
        });
        setLoading(false);
        setActivity("");
      }
    },
    [
      stop,
      ensureConversation,
      datasourceId,
      reportAudience,
      clearMetricsTimer,
      refreshProgress
    ]
  );

  const loadConversation = useCallback(async (targetConversationId: number) => {
    stop();
    const detail = await getConversationDetail(targetConversationId);
    const records = detail.records ?? [];
    const interrupt = records[records.length - 1]?.exec_result?.interrupt_id;
    interruptRef.current = interrupt ? { conversationId: detail.id, id: interrupt } : undefined;
    syncWorkspaceOid(detail.oid);
    setConversationId(detail.id);
    setConversationTitle(detail.title);
    if (detail.datasource_id) {
      setDatasourceIdState(detail.datasource_id);
    }

    const nextMessages: Message[] = [];
    const nextSteps: ExecutionStep[] = [];
    let nextSummary = "";
    const nextSummaryByRunId: Record<string, string> = {};
    const nextMetricsByRunId: Record<string, RunMetrics> = {};
    const nextReports: ReportPayload[] = [];
    const nextQueryResults: QueryResult[] = [];
    const nextClarifyByRunId: Record<string, ClarifyPayload> = {};
    const nextTaskPlanByRunId: Record<string, TaskPlanStage[]> = {};
    const nextChartByRunId: Record<string, ChartRecommendation> = {};
    const queryResultSignatures = new Set<string>();

    detail.records?.forEach((record) => {
      const recordRunId = `record-${record.id}`;
      const createdAt = record.create_time ? Date.parse(record.create_time) : undefined;
      const finishedAt = record.finish_time ? Date.parse(record.finish_time) : createdAt;
      if (record.exec_result?.clarify) nextClarifyByRunId[recordRunId] = record.exec_result;

      nextMetricsByRunId[recordRunId] = metricsFromPersisted(record.total_tokens, record.elapsed_ms);
      const loadedChart = asChartConfig(record.chart_config);
      if (record.chart_type || loadedChart) {
        nextChartByRunId[recordRunId] = {
          chartType: record.chart_type || undefined,
          chartConfig: loadedChart
        };
      }
      if (asText(record.question).trim()) {
        nextMessages.push({ id: `u-${record.id}`, role: "user", content: asText(record.question), runId: recordRunId, createdAt });
      }
      const answer = asText(record.summary || record.reasoning || "").trim();
      if (answer) {
        nextMessages.push({ id: `a-${record.id}`, role: "assistant", content: answer, runId: recordRunId, createdAt: finishedAt });
      }
      if (asText(record.summary).trim()) {
        nextSummary = asText(record.summary);
        nextSummaryByRunId[recordRunId] = asText(record.summary);
      }
      deriveReportsFromRecord(record).forEach((r, idx) => {
        nextReports.push({
          ...r,
          runId: recordRunId,
          recordId: record.id,
          reportIndex: r.reportIndex ?? idx,
          reviewStatus: r.reviewStatus || "pending"
        });
      });
      const sqlText = asText(record.sql);
      const execColumns = Array.isArray(record.exec_result?.columns)
        ? record.exec_result.columns.map((col) => asText(col))
        : [];
      const execRows = Array.isArray(record.exec_result?.rows) ? record.exec_result.rows : [];
      if (sqlText && execColumns.length && Array.isArray(record.exec_result?.rows)) {
        const signature = `${recordRunId}|${sqlText}|${execColumns.join(",")}|${record.exec_result?.row_count ?? execRows.length}`;
        if (!queryResultSignatures.has(signature)) {
          queryResultSignatures.add(signature);
          nextQueryResults.push({
            key: `record-${record.id}-exec`,
            sql: sqlText,
            columns: execColumns,
            rows: execRows,
            rowCount: record.exec_result?.row_count ?? execRows.length,
            runId: recordRunId
          });
        }
      }

      if (record.plans?.length) {
        nextTaskPlanByRunId[recordRunId] = record.plans.map((content, index) => {
          const state = record.plan_states?.find((stage) => stage.index === index)?.state;
          const status = state === "ok" ? "completed" : state === "running" ? "in_progress" : state === "error" ? "failed" : "pending";
          return { content, status };
        });
      }

      const traceStart = nextSteps.length;
      record.tool_calls?.forEach((tc, idx) => {
        nextSteps.push({
          id: `tool-${record.id}-${idx}`,
          title: `工具结果: ${asText(tc.tool) || "tool"}`,
          toolName: asText(tc.tool),
          stageContent: tc.sub_task,
          elapsedMs: tc.elapsed_ms,
          rowCount: tc.data?.row_count ?? tc.data?.rows?.length,
          status: tc.success === false ? "error" : "done",
          runId: recordRunId,
          section: "result",
          subTaskIndex: tc.sub_task_index,
          round: tc.round
        });
        if (["execute_sql", "query_semantic"].includes(asText(tc.tool)) && tc.data && typeof tc.data === "object") {
          const rawColumns = Array.isArray(tc.data.columns) ? tc.data.columns : [];
          const rawRows = Array.isArray(tc.data.rows) ? tc.data.rows : [];
          const safeColumns = rawColumns.map((col) => asText(col));
          if (safeColumns.length && Array.isArray(tc.data.rows)) {
            const sql = asText(tc.data.sql);
            const rowCount = typeof tc.data.row_count === "number" ? tc.data.row_count : rawRows.length;
            const signature = `${recordRunId}|${sql}|${safeColumns.join(",")}|${rowCount}`;
            if (!queryResultSignatures.has(signature)) {
              queryResultSignatures.add(signature);
              nextQueryResults.push({
                key: `record-${record.id}-tool-${idx}`,
                sql,
                columns: safeColumns,
                rows: rawRows,
                rowCount,
                runId: recordRunId
              });
            }
          }
        }
      });

      record.steps?.forEach((st, idx) => {
        const title = asText(st.label || st.name || "步骤");
        if (/^(?:(?:生成|执行)\s*SQL|(?:generate|execute)_sql)$/i.test(title.trim())) return;
        if (st.name?.startsWith("agent_round_") && record.tool_calls?.some((call) => call.tool === "query_semantic")) return;
        nextSteps.push({
          id: `step-${record.id}-${idx}`,
          title,
          detail: asText(st.detail),
          kind: st.name === "analysis_update" ? "thought" : undefined,
          round: st.round,
          status: st.status === "error" ? "error" : "done",
          runId: recordRunId,
          section: "step",
          subTaskIndex: st.sub_task_index
        });
      });
      if (record.steps?.some((step) => step.name === "prepare_agent")) {
        const trace = nextSteps.splice(traceStart);
        nextSteps.push(...trace.sort((a, b) => {
          const order = (a.round ?? Infinity) - (b.round ?? Infinity);
          return order || Number(Boolean(a.toolName)) - Number(Boolean(b.toolName));
        }));
      }
    });

    setMessages(nextMessages);
    setExecutionSteps(nextSteps);
    setSummary(nextSummary);
    setSummaryByRunId(nextSummaryByRunId);
    setMetricsByRunId(nextMetricsByRunId);
    setRunMetrics(EMPTY_RUN_METRICS);
    setReports(nextReports);
    setQueryResults(nextQueryResults);
    setChartByRunId(nextChartByRunId);
    setClarifyByRunId(nextClarifyByRunId);
    setTaskPlanByRunId(nextTaskPlanByRunId);
    setLoading(false);
  }, [stop]);

  const clearConversation = useCallback(() => {
    stop();
    interruptRef.current = undefined;
    setConversationId(undefined);
    setConversationTitle("");
    setMessages([]);
    setExecutionSteps([]);
    setSummary("");
    setSummaryByRunId({});
    setMetricsByRunId({});
    setReports([]);
    setQueryResults([]);
    setChartByRunId({});
    setClarifyByRunId({});
    setTaskPlanByRunId({});
    setLoading(false);
    setActivity("");
    clearMetricsTimer();
    setRunMetrics(EMPTY_RUN_METRICS);
    planDoneIdxRef.current = new Set();
  }, [clearMetricsTimer, stop]);

  const patchReport = useCallback(
    async (
      target: ReportPayload,
      patch: { recommendationsText?: string; reviewStatus?: "pending" | "approved" }
    ): Promise<ReportPayload> => {
      let nextHtml = target.html;
      if (patch.recommendationsText != null) {
        nextHtml = replaceRecommendationsHtml(target.html, patch.recommendationsText);
      }
      const next: ReportPayload = {
        ...target,
        html: nextHtml,
        reviewStatus: patch.reviewStatus ?? target.reviewStatus ?? "pending"
      };

      const cid = conversationId;
      const rid = target.recordId;
      const ridx = target.reportIndex;
      if (cid && rid != null && ridx != null) {
        const persistBody = (list: ReportPayload[]) =>
          list
            .filter((r) => r.recordId === rid)
            .sort((a, b) => (a.reportIndex ?? 0) - (b.reportIndex ?? 0))
            .map((r) => ({
              title: r.title,
              html: r === target ? next.html : r.html,
              mode: r.mode,
              sub_task_index: r.subTaskIndex,
              report_type: r.reportType,
              report_type_label: r.reportTypeLabel,
              review_status: r === target ? next.reviewStatus : r.reviewStatus || "pending"
            }));
        const runUpdate = () =>
          updateReportReview({
            conversationId: cid,
            recordId: rid,
            reportIndex: ridx,
            recommendations_text: patch.recommendationsText,
            review_status: patch.reviewStatus
          });
        let resp: Awaited<ReturnType<typeof updateReportReview>> | undefined;
        try {
          resp = await runUpdate();
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          if (!msg.includes("404")) throw err;
          await replaceRecordReports({
            conversationId: cid,
            recordId: rid,
            reports: persistBody(reports)
          });
          resp = await runUpdate();
        }
        if (resp?.report?.html) {
          next.html = asText(resp.report.html);
        }
        if (resp?.report?.review_status === "approved" || resp?.report?.review_status === "pending") {
          next.reviewStatus = resp.report.review_status;
        }
      }

      setReports((prev) =>
        prev.map((r) => {
          const same =
            (rid != null && r.recordId === rid && r.reportIndex === ridx) ||
            (r.html === target.html && (r.runId ?? "") === (target.runId ?? "") && r.title === target.title);
          return same ? { ...r, ...next } : r;
        })
      );
      return next;
    },
    [conversationId, reports]
  );

  return {
    messages,
    executionSteps,
    summary,
    summaryByRunId,
    reports,
    queryResults,
    chartByRunId,
    loading,
    activity,
    runMetrics,
    metricsByRunId,
    clarifyByRunId,
    taskPlanByRunId,
    send,
    stop,
    loadConversation,
    clearConversation,
    patchReport,
    conversationId,
    conversationTitle,
    reportAudience,
    setReportAudience,
    datasourceId,
    setDatasourceId
  };
}
