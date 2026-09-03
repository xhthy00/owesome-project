import {
  AuditOutlined,
  BarChartOutlined,
  CodeOutlined,
  CopyOutlined,
  DownloadOutlined,
  DownOutlined,
  EditOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  ExpandOutlined,
  FileTextOutlined,
  LineChartOutlined,
  PieChartOutlined,
  TableOutlined,
  FilePdfOutlined,
  FileWordOutlined
} from "@ant-design/icons";
import { Input, Pagination, message, Modal } from "antd";
import React, { useRef } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  AgentMode,
  ChartRecommendation,
  ExecutionStep,
  QueryResult,
  ReportPayload,
  formatReportDisplayTitle
} from "@/hooks/useChat";
import G2Chart, { G2ChartType, formatQuerySetLabel, inferChartFields, pickYField } from "@/components/chat/G2Chart";
import { labelColumn } from "@/utils/columnLabels";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  extractRecommendationsText,
  hasRecommendationsSection
} from "@/utils/reportRecommendations";
import { exportReportAsWord } from "@/utils/exportReportWord";
import {
  bindIframeWatermark,
  captureElementWithWatermark,
  stampHtmlWatermark
} from "@/utils/userWatermark";
import AgentTeamStrip from "@/components/chat/AgentTeamStrip";
import {
  activePipelineAgent,
  AGENT_STATUS_LABEL,
  FLOW_AGENT_META,
  FLOW_AGENTS,
  FLOW_PIPELINE_LABELS,
  assignStepToAgent,
  computeFlowStatus,
  groupStepsByAgent,
  type FlowAgent
} from "@/utils/agentTeam";
import { formatElapsed, formatTokenCount, type RunMetrics } from "@/utils/runMetrics";
import { humanizeStepDetail, humanizeStepTitle } from "@/utils/toolLabels";

type Props = {
  steps: ExecutionStep[];
  summary?: string;
  summaryByRunId?: Record<string, string>;
  reports?: ReportPayload[];
  queryResults?: QueryResult[];
  chartByRunId?: Record<string, ChartRecommendation>;
  selectedStepId?: string;
  onSelectStep?: (stepId: string) => void;
  runMetrics?: RunMetrics | null;
  /** team：专家团协作；agent：单分析助手扁平时间线 */
  agentMode?: AgentMode;
  onPatchReport?: (
    report: ReportPayload,
    patch: { recommendationsText?: string; reviewStatus?: "pending" | "approved" }
  ) => Promise<ReportPayload>;
};

function normalizeToText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => normalizeToText(item)).join("\n");
  }
  if (React.isValidElement(value)) {
    return normalizeToText((value as React.ReactElement<{ children?: unknown }>).props?.children);
  }
  try {
    const seen = new WeakSet<object>();
    return JSON.stringify(
      value,
      (_key, val) => {
        if (typeof val === "object" && val !== null) {
          if (seen.has(val)) return "[Circular]";
          seen.add(val);
        }
        return val;
      },
      2
    );
  } catch {
    return String(value);
  }
}

function parseThinkContent(raw: unknown) {
  const text = normalizeToText(raw);
  const thinkRegex = /<think>([\s\S]*?)(?:<\/think>|$)/gi;
  const thinkBlocks: string[] = [];
  let m: RegExpExecArray | null = null;
  while ((m = thinkRegex.exec(text)) !== null) {
    const content = m[1]?.trim();
    if (content) thinkBlocks.push(content);
  }
  const plain = text.replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, "").trim();
  return { thinkBlocks, plain };
}

export default function ChatExecutionPanel({
  steps,
  summary,
  summaryByRunId = {},
  reports = [],
  queryResults = [],
  chartByRunId = {},
  selectedStepId,
  onSelectStep,
  runMetrics = null,
  agentMode = "team",
  onPatchReport
}: Props) {
  const isSingleAgent = agentMode === "agent";
  const [activeTab, setActiveTab] = useState<"steps" | "summary">("steps");
  const [summaryThinkExpanded, setSummaryThinkExpanded] = useState(false);
  const [stepDetailExpanded, setStepDetailExpanded] = useState(true);
  const [showReportDialog, setShowReportDialog] = useState(false);
  const [selectedReportIndex, setSelectedReportIndex] = useState(-1);
  const [resultTab, setResultTab] = useState<"chart" | "data" | "sql">("chart");
  const [chartType, setChartType] = useState<G2ChartType>("column");
  const [selectedQueryIndex, setSelectedQueryIndex] = useState(-1);
  const [showChartLabel, setShowChartLabel] = useState(false);
  const [dataPage, setDataPage] = useState(1);
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [expandedAgents, setExpandedAgents] = useState<Set<FlowAgent>>(() => new Set());
  const pageSize = 20;
  const selectedStep = useMemo(
    () => steps.find((s) => s.id === selectedStepId) ?? steps[steps.length - 1],
    [steps, selectedStepId]
  );
  const selectedRunId = selectedStep?.runId;
  const scopedSteps = useMemo(() => {
    if (!selectedRunId) return steps;
    const matched = steps.filter((s) => s.runId === selectedRunId);
    return matched.length ? matched : steps;
  }, [steps, selectedRunId]);
  const scopedReports = useMemo(() => {
    if (!selectedRunId) return reports;
    return reports.filter((r) => r.runId === selectedRunId);
  }, [reports, selectedRunId]);
  const scopedQueryResults = useMemo(() => {
    if (!selectedRunId) return queryResults;
    return queryResults.filter((q) => q.runId === selectedRunId);
  }, [queryResults, selectedRunId]);
  const scopedSummary = useMemo(() => {
    if (selectedRunId && summaryByRunId[selectedRunId]) {
      return summaryByRunId[selectedRunId];
    }
    return summary || "";
  }, [selectedRunId, summaryByRunId, summary]);
  const activeReportIndex = useMemo(() => {
    if (!scopedReports.length) return -1;
    if (selectedReportIndex < 0 || selectedReportIndex >= scopedReports.length) {
      return scopedReports.length - 1;
    }
    return selectedReportIndex;
  }, [scopedReports, selectedReportIndex]);
  const activeReport = scopedReports.length ? scopedReports[activeReportIndex] : undefined;
  const reportOptionLabels = useMemo(() => {
    return scopedReports.map((r, idx) => {
      const sub = r.subTaskIndex;
      const prefix = sub != null ? `子任务 ${sub + 1} · ` : "";
      const withType = formatReportDisplayTitle(r.title || "", r.reportTypeLabel);
      const tail = idx === scopedReports.length - 1 ? "（最新）" : `报告 ${idx + 1}`;
      return `${prefix}${withType || tail}`;
    });
  }, [scopedReports]);

  const selectedStepAgentLabel = useMemo(() => {
    if (!selectedStep) return "";
    if (isSingleAgent) return "分析助手";
    return FLOW_AGENT_META[assignStepToAgent(selectedStep)].identity;
  }, [selectedStep, isSingleAgent]);
  const selectedStepTitleText = useMemo(
    () =>
      humanizeStepTitle(
        normalizeToText(selectedStep?.title),
        normalizeToText(selectedStep?.detail),
        selectedStep?.status
      ),
    [selectedStep?.title, selectedStep?.detail, selectedStep?.status]
  );
  const selectedStepStatusText = useMemo(() => normalizeToText(selectedStep?.status), [selectedStep?.status]);
  const detailText = useMemo(
    () => humanizeStepDetail(normalizeToText(selectedStep?.detail)),
    [selectedStep?.detail]
  );
  const summaryText = useMemo(() => normalizeToText(scopedSummary), [scopedSummary]);
  const parsedDetail = useMemo(() => parseThinkContent(detailText), [detailText]);
  const parsedSummary = useMemo(() => parseThinkContent(summaryText), [summaryText]);
  const detailFallbackText = useMemo(
    () => (detailText ? "" : "点击左侧步骤卡片查看详细执行结果"),
    [detailText]
  );
  const markdownDetailText = useMemo(
    () => normalizeToText(parsedDetail.plain || detailFallbackText),
    [parsedDetail.plain, detailFallbackText]
  );
  const safeMarkdownText = useMemo(() => {
    const normalized = normalizeToText(markdownDetailText);
    return typeof normalized === "string" ? normalized : String(normalized ?? "");
  }, [markdownDetailText]);
  const markdownPlugins = useMemo(() => [remarkGfm], []);
  const safeReportHtml = useMemo(() => normalizeToText(activeReport?.html || ""), [activeReport?.html]);
  const safeReportTitle = useMemo(
    () => formatReportDisplayTitle(activeReport?.title || "Report", activeReport?.reportTypeLabel),
    [activeReport?.title, activeReport?.reportTypeLabel]
  );
  const reportApproved = activeReport?.reviewStatus === "approved";
  const reportCanEdit = Boolean(activeReport?.html) && !reportApproved;
  const reportHasRecommendations = useMemo(
    () => (activeReport?.html ? hasRecommendationsSection(activeReport.html) : false),
    [activeReport?.html]
  );

  const openEditRecommendations = () => {
    if (!activeReport) return;
    if (reportApproved) {
      message.warning("报告已审核，不可再编辑");
      return;
    }
    if (!reportHasRecommendations) {
      message.warning("本报告无可编辑的建议区");
      return;
    }
    setEditText(extractRecommendationsText(activeReport.html) || "");
    setEditOpen(true);
  };

  const saveEditRecommendations = async () => {
    if (!activeReport || !onPatchReport) {
      message.error("无法保存：缺少报告更新接口");
      return;
    }
    setEditSaving(true);
    try {
      await onPatchReport(activeReport, { recommendationsText: editText });
      message.success("建议已更新");
      setEditOpen(false);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setEditSaving(false);
    }
  };

  const confirmReviewReport = () => {
    if (!activeReport) return;
    Modal.confirm({
      title: "确认审核",
      content: "审核通过后将开放导出，且不可再编辑建议。是否确认？",
      okText: "确认审核",
      cancelText: "取消",
      onOk: async () => {
        if (!onPatchReport) {
          message.error("无法审核：缺少报告更新接口");
          return;
        }
        setReviewSaving(true);
        try {
          await onPatchReport(activeReport, { reviewStatus: "approved" });
          message.success("已审核通过");
        } catch (err) {
          message.error(err instanceof Error ? err.message : "审核失败");
          throw err;
        } finally {
          setReviewSaving(false);
        }
      }
    });
  };
  const activeQuery = useMemo(() => {
    if (!scopedQueryResults.length) return undefined;
    if (selectedQueryIndex < 0 || selectedQueryIndex >= scopedQueryResults.length) {
      return scopedQueryResults[scopedQueryResults.length - 1];
    }
    return scopedQueryResults[selectedQueryIndex];
  }, [scopedQueryResults, selectedQueryIndex]);
  const queryDisplayLabels = useMemo(() => {
    const total = scopedQueryResults.length;
    return scopedQueryResults.map((item, idx) => {
      const isFinal = idx === total - 1;
      const rec = isFinal && item.runId ? chartByRunId[item.runId] : undefined;
      const cfg = rec?.chartConfig;
      const xOverride = cfg?.x && item.columns.includes(cfg.x) ? cfg.x : undefined;
      const yOverride = pickYField((cfg?.y || []).filter((col) => item.columns.includes(col)));
      const fields = inferChartFields(item.columns, item.rows, {
        xField: xOverride,
        yField: yOverride
      });
      return formatQuerySetLabel({
        xField: fields.xField,
        yField: fields.yField,
        chartTitle: cfg?.title,
        isFinal,
        index: idx,
        total
      });
    });
  }, [scopedQueryResults, chartByRunId]);
  const activeQueryIndex = useMemo(() => {
    if (!scopedQueryResults.length) return -1;
    if (selectedQueryIndex < 0 || selectedQueryIndex >= scopedQueryResults.length) {
      return scopedQueryResults.length - 1;
    }
    return selectedQueryIndex;
  }, [scopedQueryResults, selectedQueryIndex]);
  const chartFieldOverride = useMemo(() => {
    if (!activeQuery || !scopedQueryResults.length) return { xField: undefined as string | undefined, yField: undefined as string | undefined };
    const isFinalQuery = activeQuery === scopedQueryResults[scopedQueryResults.length - 1];
    if (!isFinalQuery) return { xField: undefined, yField: undefined };
    const rec = activeQuery.runId ? chartByRunId[activeQuery.runId] : undefined;
    const cfg = rec?.chartConfig;
    if (!cfg) return { xField: undefined, yField: undefined };
    const cols = activeQuery.columns;
    const xField = cfg.x && cols.includes(cfg.x) ? cfg.x : undefined;
    const yCandidates = (cfg.y || []).filter((col) => cols.includes(col));
    return { xField, yField: pickYField(yCandidates) };
  }, [activeQuery, scopedQueryResults, chartByRunId]);
  const pagedRows = useMemo(() => {
    if (!activeQuery) return [];
    const start = (dataPage - 1) * pageSize;
    return activeQuery.rows.slice(start, start + pageSize);
  }, [activeQuery, dataPage]);
  useEffect(() => {
    setDataPage(1);
  }, [activeQueryIndex]);
  useEffect(() => {
    setSelectedReportIndex(-1);
    setSelectedQueryIndex(-1);
  }, [selectedRunId]);
  useEffect(() => {
    if (!scopedReports.length) return;
    setSelectedReportIndex((prev) => {
      if (prev < 0 || prev >= scopedReports.length) return scopedReports.length - 1;
      return prev;
    });
  }, [scopedReports]);
  // 本轮有报告时切到摘要（含报告刚到达、或切换历史轮次）
  useEffect(() => {
    if (scopedReports.length) setActiveTab("summary");
  }, [selectedRunId, scopedReports.length]);
  const rawSelectedTitle = normalizeToText(selectedStep?.title);
  const isToolResultStep = /^工具结果:/i.test(rawSelectedTitle);
  const isStepError =
    selectedStepStatusText === "error" ||
    /execute failed|failed|error|异常|失败/i.test(detailText);
  const copyReportHtml = async () => {
    try {
      await navigator.clipboard.writeText(stampHtmlWatermark(safeReportHtml));
      message.success("HTML 已复制");
    } catch {
      message.error("复制失败");
    }
  };
  const downloadReportHtml = () => {
    const blob = new Blob([stampHtmlWatermark(safeReportHtml)], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeReportTitle || "report"}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const reportIframeRef = useRef<HTMLIFrameElement | null>(null);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [exportingWord, setExportingWord] = useState(false);
  const exportReportWord = async () => {
    setExportingWord(true);
    try {
      await exportReportAsWord({
        title: safeReportTitle || "report",
        html: safeReportHtml,
        iframe: reportIframeRef.current
      });
      message.success("Word 已导出");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "Word 导出失败");
      // eslint-disable-next-line no-console
      console.error(e);
    } finally {
      setExportingWord(false);
    }
  };
  const exportReportPdf = async () => {
    const iframe = reportIframeRef.current;
    const doc = iframe?.contentDocument;
    if (!doc || !doc.body) {
      message.error("无法访问报告内容");
      return;
    }
    setExportingPdf(true);
    try {
      const [{ default: html2canvas }, jspdfMod] = await Promise.all([
        import("html2canvas"),
        import("jspdf")
      ]);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const JsPDF = (jspdfMod as any).jsPDF;
      const canvas = await captureElementWithWatermark(doc.body, html2canvas, {
        scale: 2,
        useCORS: true,
        backgroundColor: "#ffffff"
      });
      const pdf = new JsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
      const pageW = pdf.internal.pageSize.getWidth();
      const pageH = pdf.internal.pageSize.getHeight();
      const imgW = pageW;
      const imgH = (canvas.height * imgW) / canvas.width;
      let remaining = imgH;
      let position = 0;
      const imgData = canvas.toDataURL("image/png");
      pdf.addImage(imgData, "PNG", 0, position, imgW, imgH);
      remaining -= pageH;
      while (remaining > 0) {
        position -= pageH;
        pdf.addPage();
        pdf.addImage(imgData, "PNG", 0, position, imgW, imgH);
        remaining -= pageH;
      }
      pdf.save(`${safeReportTitle || "report"}.pdf`);
      message.success("PDF 已导出");
    } catch (e) {
      message.error("PDF 导出失败");
      // eslint-disable-next-line no-console
      console.error(e);
    } finally {
      setExportingPdf(false);
    }
  };
  const flowStatus = useMemo(() => computeFlowStatus(scopedSteps), [scopedSteps]);
  const agentGroups = useMemo(
    () => groupStepsByAgent(scopedSteps, flowStatus),
    [scopedSteps, flowStatus]
  );
  const agentActions = useMemo(() => {
    const fallbackDone: Record<FlowAgent, string> = {
      Planner: "已完成问题拆解",
      DataAnalyst: "已完成数据分析",
      Charter: "已完成图表方案",
      Summarizer: "已整理分析结论"
    };
    const actions = Object.fromEntries(FLOW_AGENTS.map((a) => [a, ""])) as Record<FlowAgent, string>;
    agentGroups.forEach((g) => {
      const last = g.steps[g.steps.length - 1];
      if (last) {
        actions[g.agent] = humanizeStepTitle(
          normalizeToText(last.title),
          normalizeToText(last.detail),
          last.status
        );
      } else if (flowStatus[g.agent] === "done") {
        actions[g.agent] = fallbackDone[g.agent];
      }
    });
    return actions;
  }, [agentGroups, flowStatus]);

  const prevFlowStatusRef = useRef(flowStatus);
  useEffect(() => {
    const prev = prevFlowStatusRef.current;
    prevFlowStatusRef.current = flowStatus;
    const justStarted = FLOW_AGENTS.filter(
      (a) => flowStatus[a] === "running" && prev[a] !== "running"
    );
    if (!justStarted.length) return;
    setExpandedAgents((expanded) => {
      const next = new Set(expanded);
      justStarted.forEach((a) => next.add(a));
      return next;
    });
  }, [flowStatus]);

  const toggleAgentExpand = (agent: FlowAgent) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(agent)) next.delete(agent);
      else next.add(agent);
      return next;
    });
  };

  const onSelectAgent = (agent: FlowAgent) => {
    setActiveTab("steps");
    setExpandedAgents((prev) => new Set(prev).add(agent));
    const first = agentGroups.find((g) => g.agent === agent)?.steps[0];
    if (first) onSelectStep?.(first.id);
  };

  const focusAgent =
    FLOW_AGENTS.find((a) => flowStatus[a] === "running") ??
    FLOW_AGENTS.find((a) => expandedAgents.has(a)) ??
    null;

  const showRunMetrics = Boolean(
    runMetrics &&
      (runMetrics.runStartedAt != null ||
        runMetrics.tokenKnown ||
        runMetrics.elapsedKnown ||
        runMetrics.progressPct >= 100)
  );

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden border-l border-[#eceff5] bg-[#f8f9fc] dark:border-[#2f3441] dark:bg-[#171b24]">
      <div className="flex h-11 items-center justify-between gap-3 border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex shrink-0 items-center gap-2 text-sm font-semibold text-[#1f2937] dark:text-[#e2e8f0]">
            <img
              src={
                isSingleAgent
                  ? `${FLOW_AGENT_META.DataAnalyst.avatar}?v=1`
                  : "/expert-team-avatar.png?v=2"
              }
              alt=""
              width={28}
              height={28}
              className="h-7 w-7 shrink-0 rounded-lg object-cover shadow-sm ring-1 ring-[#dbeafe]"
            />
            <span>{isSingleAgent ? "分析助手" : "专家团协作"}</span>
          </div>
          {!isSingleAgent ? (
            <>
              <span className="hidden h-3.5 w-px shrink-0 bg-[#e2e8f0] sm:block dark:bg-[#334155]" />
              <div className="flex min-w-0 flex-wrap items-center gap-x-1 gap-y-0.5 text-[11px] text-[#94a3b8]">
                {FLOW_AGENTS.map((agent, idx) => {
                  const active = activePipelineAgent(flowStatus) === agent;
                  const done = flowStatus[agent] === "done";
                  return (
                    <span key={agent} className="inline-flex items-center gap-1">
                      {idx > 0 ? <span className="text-[#cbd5e1]">→</span> : null}
                      <span
                        className={
                          active
                            ? "font-semibold text-[#2563eb]"
                            : done
                              ? "text-[#64748b]"
                              : "text-[#94a3b8]"
                        }
                      >
                        {FLOW_PIPELINE_LABELS[agent]}
                      </span>
                    </span>
                  );
                })}
              </div>
            </>
          ) : (
            <span className="hidden text-[11px] text-[#94a3b8] sm:inline">思考 · 工具调用 · 结果</span>
          )}
        </div>
        {showRunMetrics && runMetrics ? (
          <span className="run-metrics-blink shrink-0 text-[13px] font-bold tabular-nums">
            Token {formatTokenCount(runMetrics.totalTokens, runMetrics.tokenKnown)}
            {" · "}
            耗时 {formatElapsed(runMetrics.elapsedMs, runMetrics.elapsedKnown)}
          </span>
        ) : null}
      </div>

      {!isSingleAgent ? (
        <AgentTeamStrip
          statusMap={flowStatus}
          actions={agentActions}
          focusAgent={focusAgent}
          onSelectAgent={onSelectAgent}
          timerEpoch={`${selectedRunId ?? ""}:${runMetrics?.runStartedAt ?? 0}`}
        />
      ) : null}

      <div className="flex h-11 items-center border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
        <button
          onClick={() => setActiveTab("steps")}
          className={`mr-6 pb-2 text-sm font-medium ${
            activeTab === "steps"
              ? "border-b-2 border-black text-[#111827] dark:border-white dark:text-[#f8fafc]"
              : "text-[#94a3b8]"
          }`}
        >
          分析过程
        </button>
        <button
          onClick={() => setActiveTab("summary")}
          className={`pb-2 text-sm font-medium ${
            activeTab === "summary"
              ? "border-b-2 border-black text-[#111827] dark:border-white dark:text-[#f8fafc]"
              : "text-[#94a3b8]"
          }`}
        >
          摘要
        </button>
      </div>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-scroll overflow-x-hidden p-4 text-[#94a3b8]">
        {activeTab === "steps" ? (
          scopedSteps.length ? (
            <div className="flex min-h-0 flex-col gap-3">
              {isSingleAgent ? (
                <div className="space-y-1.5 rounded-xl border border-[#86efac] bg-white p-2 dark:bg-[#11131a]">
                  {scopedSteps.map((step) => {
                    const active = step.id === selectedStep?.id;
                    return (
                      <button
                        key={step.id}
                        type="button"
                        onClick={() => onSelectStep?.(step.id)}
                        className={`flex w-full items-start gap-2.5 rounded-lg border px-2.5 py-2 text-left ${
                          active
                            ? "border-[#86efac] bg-[#f0fdf4] dark:border-[#166534] dark:bg-[#052e16]"
                            : "border-transparent hover:bg-[#f8fafc] dark:hover:bg-[#0f172a]"
                        }`}
                      >
                        <span
                          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                            step.status === "error"
                              ? "bg-[#ef4444]"
                              : step.status === "running"
                                ? "bg-[#22c55e] animate-pulse"
                                : "bg-[#86efac]"
                          }`}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-medium text-[#0f172a] dark:text-[#e2e8f0]">
                            {humanizeStepTitle(
                              normalizeToText(step.title),
                              normalizeToText(step.detail),
                              step.status
                            )}
                          </div>
                          {step.detail ? (
                            <div className="mt-0.5 line-clamp-2 text-[11px] text-[#64748b]">
                              {humanizeStepDetail(normalizeToText(step.detail))}
                            </div>
                          ) : null}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
              <div className="space-y-2">
                {agentGroups.map((group) => {
                  const meta = FLOW_AGENT_META[group.agent];
                  const open = expandedAgents.has(group.agent);
                  return (
                    <div
                      key={group.agent}
                      className={`rounded-xl border bg-white dark:bg-[#11131a] ${meta.border}`}
                    >
                      <button
                        type="button"
                        onClick={() => toggleAgentExpand(group.agent)}
                        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <DownOutlined
                            className={`text-[11px] text-[#64748b] transition-transform ${
                              open ? "rotate-0" : "-rotate-90"
                            }`}
                          />
                          <img
                            src={`${meta.avatar}?v=1`}
                            alt=""
                            width={22}
                            height={22}
                            className="h-[22px] w-[22px] shrink-0 rounded-full object-cover ring-1 ring-[#e2e8f0]"
                          />
                          <span className={`text-sm font-semibold ${meta.text}`}>{meta.identity}</span>
                          <span className="text-[11px] text-[#94a3b8]">
                            {AGENT_STATUS_LABEL[group.status]}
                            {group.steps.length ? ` · ${group.steps.length}` : ""}
                          </span>
                        </div>
                      </button>
                      {open ? (
                        <div className="space-y-1.5 border-t border-[#eef2f7] px-2 py-2 dark:border-[#2f3441]">
                          {group.steps.length ? (
                            group.steps.map((step) => {
                              const active = step.id === selectedStep?.id;
                              return (
                                <button
                                  key={step.id}
                                  type="button"
                                  onClick={() => onSelectStep?.(step.id)}
                                  className={`w-full rounded-lg border px-2.5 py-2 text-left ${
                                    active
                                      ? "border-[#93c5fd] bg-[#eff6ff] dark:border-[#1d4ed8] dark:bg-[#172554]"
                                      : "border-transparent hover:bg-[#f8fafc] dark:hover:bg-[#0f172a]"
                                  }`}
                                >
                                  <div className="truncate text-xs font-medium text-[#0f172a] dark:text-[#e2e8f0]">
                                    {humanizeStepTitle(
                                      normalizeToText(step.title),
                                      normalizeToText(step.detail),
                                      step.status
                                    )}
                                  </div>
                                  {step.detail ? (
                                    <div className="mt-0.5 line-clamp-2 text-[11px] text-[#64748b]">
                                      {humanizeStepDetail(normalizeToText(step.detail))}
                                    </div>
                                  ) : null}
                                </button>
                              );
                            })
                          ) : (
                            <div className="px-2 py-1.5 text-[11px] text-[#94a3b8]">
                              {group.status === "idle" ? "尚未开始" : "暂无明细步骤"}
                            </div>
                          )}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
              )}

              {selectedStep ? (
                <div className="flex min-h-0 min-w-0 flex-col rounded-lg border border-[#e5e7eb] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <button
                    onClick={() => setStepDetailExpanded((prev) => !prev)}
                    className="mb-2 flex w-full min-w-0 items-center justify-between gap-3 rounded-md border border-[#e5e7eb] bg-white px-2.5 py-1.5 text-left dark:border-[#2f3441] dark:bg-[#11131a]"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <DownOutlined
                        className={`text-[12px] text-[#64748b] transition-transform ${
                          stepDetailExpanded ? "rotate-0" : "-rotate-90"
                        }`}
                      />
                      {selectedStepAgentLabel ? (
                        <span className="shrink-0 rounded-full border border-[#bfdbfe] bg-[#eff6ff] px-2 py-0.5 text-[11px] font-medium text-[#1d4ed8] dark:border-[#1e3a5f] dark:bg-[#172554] dark:text-[#93c5fd]">
                          {selectedStepAgentLabel}
                        </span>
                      ) : null}
                      <div className="truncate text-sm font-semibold text-[#0f172a] dark:text-[#e2e8f0]">
                        {selectedStepTitleText || "选择一个步骤查看详情"}
                      </div>
                    </div>
                    {selectedStepStatusText ? (
                      <span
                        className={`ml-2 shrink-0 rounded px-2 py-0.5 text-[11px] ${
                          selectedStepStatusText === "error"
                            ? "bg-[#fee2e2] text-[#b91c1c] dark:bg-[#7f1d1d]/30 dark:text-[#fecaca]"
                            : selectedStepStatusText === "running"
                              ? "bg-[#fef3c7] text-[#92400e] dark:bg-[#78350f]/30 dark:text-[#fde68a]"
                              : "bg-[#dcfce7] text-[#166534] dark:bg-[#14532d]/30 dark:text-[#bbf7d0]"
                        }`}
                      >
                        {selectedStepStatusText}
                      </span>
                    ) : null}
                  </button>
                  <div className="min-h-0 min-w-0 overflow-auto">
                    {stepDetailExpanded && parsedDetail.thinkBlocks.length ? (
                      <div className="mb-3 space-y-2">
                        {parsedDetail.thinkBlocks.map((block, idx) => (
                          <div
                            key={`think-${idx}`}
                            className="rounded-md border border-[#dbeafe] bg-[#eff6ff] px-3 py-2 dark:border-[#1d4ed8] dark:bg-[#172554]"
                          >
                            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">
                              think
                            </div>
                            <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-[#1e3a8a] dark:text-[#bfdbfe]">
                              {block}
                            </pre>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {stepDetailExpanded ? (
                      <div
                        className={`rounded-md border px-3 py-2 ${
                          isToolResultStep
                            ? "min-w-0 w-full max-w-full flex-none border-[#bfdbfe] bg-[#eff6ff] px-4 py-3 overflow-x-auto overflow-y-hidden"
                            : "border-[#dbeafe] bg-[#eff6ff] dark:border-[#1d4ed8] dark:bg-[#172554]"
                        }`}
                      >
                        {isToolResultStep ? (
                          <div
                            className={`prose max-w-none w-full max-w-full overflow-x-auto leading-relaxed [&_p]:text-inherit [&_li]:text-inherit [&_strong]:text-inherit [&_strong]:font-bold [&_b]:text-inherit [&_b]:font-bold [&_h1]:text-inherit [&_h1]:text-2xl [&_h1]:leading-9 [&_h1]:font-bold [&_h2]:text-inherit [&_h2]:text-xl [&_h2]:leading-8 [&_h2]:font-semibold [&_h3]:text-inherit [&_h3]:text-lg [&_h3]:leading-7 [&_h3]:font-semibold [&_h4]:text-inherit [&_h5]:text-inherit [&_h6]:text-inherit [&_code]:bg-transparent [&_code]:px-0 [&_code]:font-mono [&_pre]:bg-transparent [&_pre]:p-0 [&_table]:w-max [&_table]:min-w-full [&_table]:border-collapse [&_table]:border [&_table]:border-[#93c5fd] [&_th]:border [&_th]:border-[#93c5fd] [&_th]:px-2 [&_th]:py-1 [&_th]:font-semibold [&_td]:border [&_td]:border-[#93c5fd] [&_td]:px-2 [&_td]:py-1 ${
                              isStepError ? "text-[#b91c1c]" : "text-[#111827]"
                            }`}
                          >
                            <ReactMarkdown remarkPlugins={markdownPlugins}>{safeMarkdownText}</ReactMarkdown>
                          </div>
                        ) : (
                          <pre className="m-0 text-[#1e3a8a] dark:text-[#bfdbfe]">{markdownDetailText}</pre>
                        )}
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center">
              <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-2xl bg-white/70 text-3xl dark:bg-white/10">
                <FileTextOutlined />
              </div>
              <div className="text-base">
                {isSingleAgent ? "等待分析助手开始" : "等待专家团上场"}
              </div>
              <div className="mt-2 text-sm">
                {isSingleAgent
                  ? "提问后，分析助手将依次完成思考、工具调用与结果整理"
                  : "提问后，专家团将按角色接力完成分析"}
              </div>
            </div>
          )
        ) : activeTab === "summary" ? (
          scopedSummary || scopedReports.length || scopedQueryResults.length ? (
            <div className="space-y-3">
              {parsedSummary.thinkBlocks.length ? (
                <div className="rounded-xl border border-[#e6eefc] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <button
                    onClick={() => setSummaryThinkExpanded((prev) => !prev)}
                    className="flex w-full items-center justify-between text-left"
                  >
                    <span className="text-xs font-semibold tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">
                      思维推理
                    </span>
                    <DownOutlined
                      className={`text-[12px] text-[#64748b] transition-transform ${
                        summaryThinkExpanded ? "rotate-180" : "rotate-0"
                      }`}
                    />
                  </button>
                  {summaryThinkExpanded ? (
                    <div className="mt-2 space-y-2">
                      {parsedSummary.thinkBlocks.map((block, idx) => (
                        <div
                          key={`summary-think-${idx}`}
                          className="rounded-md border border-[#dbeafe] bg-[#eff6ff] px-3 py-2 dark:border-[#1d4ed8] dark:bg-[#172554]"
                        >
                          <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-[#1e3a8a] dark:text-[#bfdbfe]">
                            {block}
                          </pre>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {activeReport?.html ? (
                <div className="rounded-xl border border-[#e6eefc] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <div className="overflow-hidden rounded-lg border border-[#dbe5f1] bg-white dark:border-[#2f3441] dark:bg-[#11131a]">
                    <div className="flex h-9 items-center justify-between gap-2 border-b border-[#dbe5f1] bg-[#f8fafc] px-3 dark:border-[#2f3441] dark:bg-[#141923]">
                      <span className="truncate text-xs font-semibold text-[#344054] dark:text-[#e2e8f0]">
                        {safeReportTitle}
                      </span>
                      <div className="flex items-center gap-1.5">
                        {scopedReports.length > 1 ? (
                          <select
                            value={activeReportIndex}
                            onChange={(e) => setSelectedReportIndex(Number(e.target.value))}
                            className="h-6 rounded-md border border-[#d9e2ef] bg-white px-1 text-[11px] text-[#475467] dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            aria-label="选择报告"
                          >
                            {reportOptionLabels.map((label, idx) => (
                              <option key={idx} value={idx}>
                                {label}
                              </option>
                            ))}
                          </select>
                        ) : null}
                        {reportApproved ? (
                          <>
                            <button
                              onClick={copyReportHtml}
                              className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#475467] transition-colors hover:border-[#c5d4e8] dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            >
                              <CopyOutlined />
                              <span>复制HTML</span>
                            </button>
                            <button
                              onClick={downloadReportHtml}
                              className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#475467] transition-colors hover:border-[#c5d4e8] dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            >
                              <DownloadOutlined />
                              <span>下载</span>
                            </button>
                            <button
                              onClick={exportReportPdf}
                              disabled={exportingPdf}
                              className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#475467] transition-colors hover:border-[#c5d4e8] disabled:opacity-50 dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            >
                              <FilePdfOutlined />
                              <span>{exportingPdf ? "导出中…" : "PDF"}</span>
                            </button>
                            <button
                              onClick={() => void exportReportWord()}
                              disabled={exportingWord}
                              className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#475467] transition-colors hover:border-[#c5d4e8] disabled:opacity-50 dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            >
                              <FileWordOutlined />
                              <span>{exportingWord ? "导出中…" : "Word"}</span>
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={openEditRecommendations}
                              disabled={!reportCanEdit || !reportHasRecommendations}
                              className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#475467] transition-colors hover:border-[#c5d4e8] disabled:opacity-50 dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            >
                              <EditOutlined />
                              <span>编辑</span>
                            </button>
                            <button
                              onClick={confirmReviewReport}
                              disabled={reviewSaving || !onPatchReport}
                              className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#475467] transition-colors hover:border-[#c5d4e8] disabled:opacity-50 dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                            >
                              <AuditOutlined />
                              <span>{reviewSaving ? "审核中…" : "审核"}</span>
                            </button>
                          </>
                        )}
                        <button
                          onClick={() => setShowReportDialog(true)}
                          className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#3b82f6] transition-colors hover:border-[#93c5fd] dark:border-[#334155] dark:bg-[#0f172a]"
                        >
                          <ExpandOutlined />
                          <span>展开</span>
                        </button>
                      </div>
                    </div>
                    <iframe
                      key={`report-${selectedRunId || "latest"}-${activeReportIndex}`}
                      ref={reportIframeRef}
                      title={safeReportTitle}
                      className="h-[360px] w-full border-0"
                      srcDoc={safeReportHtml}
                      sandbox="allow-scripts allow-same-origin"
                      referrerPolicy="no-referrer"
                      onLoad={(e) => bindIframeWatermark(e.currentTarget)}
                    />
                  </div>
                </div>
              ) : null}
              {activeQuery ? (
                <div className="rounded-xl border border-[#d9e2ef] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-[#d9e2ef] bg-[#f8fafc] p-2 dark:border-[#334155] dark:bg-[#141923]">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-[#98a2b3]">查询集</span>
                      <select
                        value={activeQueryIndex}
                        onChange={(e) => setSelectedQueryIndex(Number(e.target.value))}
                        className="rounded-md border border-[#d9e2ef] bg-white px-2 py-1 text-xs text-[#475467] dark:border-[#334155] dark:bg-[#0f172a] dark:text-[#cbd5e1]"
                      >
                        {scopedQueryResults.map((item, idx) => (
                          <option key={item.key} value={idx}>
                            {queryDisplayLabels[idx]}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => setResultTab("chart")}
                        className={`rounded px-2 py-1 text-[11px] ${resultTab === "chart" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                      >
                        图表
                      </button>
                      <button
                        onClick={() => setResultTab("data")}
                        className={`rounded px-2 py-1 text-[11px] ${resultTab === "data" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                      >
                        数据
                      </button>
                      <button
                        onClick={() => setResultTab("sql")}
                        className={`rounded px-2 py-1 text-[11px] ${resultTab === "sql" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                      >
                        SQL
                      </button>
                    </div>
                  </div>

                  {resultTab === "chart" ? (
                    <div className="rounded-md border border-[#e5e7eb] p-3 dark:border-[#2f3441]">
                      <div className="mb-2 flex items-center justify-between">
                        <div className="text-xs font-semibold text-[#344054] dark:text-[#cbd5e1]">
                          {queryDisplayLabels[activeQueryIndex] || "图表展示"}
                        </div>
                        <div className="flex items-center gap-1 rounded-md border border-[#d9e2ef] p-1 dark:border-[#334155]">
                          <button
                            onClick={() => setChartType("column")}
                            className={`rounded p-1 ${chartType === "column" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                          >
                            <BarChartOutlined />
                          </button>
                          <button
                            onClick={() => setChartType("bar")}
                            className={`rounded p-1 ${chartType === "bar" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                          >
                            <TableOutlined />
                          </button>
                          <button
                            onClick={() => setChartType("line")}
                            className={`rounded p-1 ${chartType === "line" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                          >
                            <LineChartOutlined />
                          </button>
                          <button
                            onClick={() => setChartType("pie")}
                            className={`rounded p-1 ${chartType === "pie" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                          >
                            <PieChartOutlined />
                          </button>
                          <span className="mx-1 h-3 w-px bg-[#d9e2ef] dark:bg-[#334155]" />
                          <button
                            onClick={() => setShowChartLabel((prev) => !prev)}
                            className={`rounded p-1 ${showChartLabel ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                            title={showChartLabel ? "隐藏标签" : "显示标签"}
                          >
                            {showChartLabel ? <EyeOutlined /> : <EyeInvisibleOutlined />}
                          </button>
                        </div>
                      </div>
                      <div className="h-[340px] rounded-md bg-white p-2 dark:bg-[#0f172a]">
                        <G2Chart
                          type={chartType}
                          columns={activeQuery.columns}
                          rows={activeQuery.rows}
                          showLabel={showChartLabel}
                          xField={chartFieldOverride.xField}
                          yField={chartFieldOverride.yField}
                        />
                      </div>
                    </div>
                  ) : null}

                  {resultTab === "data" ? (
                    <div className="overflow-x-auto rounded-md border border-[#e5e7eb] dark:border-[#2f3441]">
                      <table className="min-w-full border-collapse text-xs">
                        <thead className="bg-[#f8fafc] dark:bg-[#141923]">
                          <tr>
                            {activeQuery.columns.map((col) => (
                              <th key={col} className="border border-[#e5e7eb] px-2 py-1 text-left dark:border-[#2f3441]">
                                {labelColumn(col)}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {pagedRows.map((row, idx) => (
                            <tr key={idx}>
                              {activeQuery.columns.map((col, colIdx) => (
                                <td key={`${idx}-${col}`} className="border border-[#e5e7eb] px-2 py-1 dark:border-[#2f3441]">
                                  {normalizeToText((row as unknown[])[colIdx])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div className="flex items-center justify-between gap-2 px-2 py-2">
                        <span className="text-[11px] text-[#98a2b3]">{`共 ${activeQuery.rowCount} 行`}</span>
                        <Pagination
                          size="small"
                          current={dataPage}
                          pageSize={pageSize}
                          total={activeQuery.rowCount}
                          onChange={setDataPage}
                          showSizeChanger={false}
                        />
                      </div>
                    </div>
                  ) : null}

                  {resultTab === "sql" ? (
                    <div className="rounded-md border border-[#1f314f] bg-gray-900 p-3">
                      <div className="mb-1 flex items-center gap-2 text-xs text-[#9ca3af]">
                        <CodeOutlined />
                        <span>SQL</span>
                      </div>
                      <pre className="m-0 whitespace-pre-wrap break-words font-mono text-xs leading-6 text-green-400">
                        {activeQuery.sql || "--"}
                      </pre>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {!parsedSummary.thinkBlocks.length && !scopedReports.length && !activeQuery ? (
                <div className="flex h-full items-center justify-center text-sm text-[#64748b]">
                  {scopedSummary ? "结论已在左侧对话区展示" : "暂无摘要"}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm">暂无摘要</div>
          )
        ) : null}
      </div>

      <div className="h-7 border-t border-[#e5e7eb] px-4 text-[10px] leading-7 text-[#94a3b8] dark:border-[#2f3441]">
        就绪
      </div>
      <Modal
        title={safeReportTitle}
        open={showReportDialog}
        onCancel={() => setShowReportDialog(false)}
        footer={null}
        width="85%"
        styles={{ body: { padding: 0 } }}
      >
        <iframe
          key={`report-dialog-${selectedRunId || "latest"}-${activeReportIndex}`}
          title={`${safeReportTitle}-full`}
          className="h-[72vh] w-full border-0"
          srcDoc={safeReportHtml}
          sandbox="allow-scripts allow-same-origin"
          referrerPolicy="no-referrer"
          onLoad={(e) => bindIframeWatermark(e.currentTarget)}
        />
      </Modal>
      <Modal
        title="编辑备考/教学建议"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void saveEditRecommendations()}
        okText="确认修改"
        cancelText="取消"
        confirmLoading={editSaving}
        destroyOnClose
        width={640}
      >
        <p className="mb-2 text-xs text-[#667085]">仅修改报告中的建议正文，不影响图表与结论。</p>
        <Input.TextArea
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          rows={12}
          placeholder="请输入建议内容"
        />
      </Modal>
    </div>
  );
}
