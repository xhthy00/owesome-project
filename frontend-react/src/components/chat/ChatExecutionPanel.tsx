import {
  BarChartOutlined,
  CodeOutlined,
  CopyOutlined,
  DesktopOutlined,
  DownloadOutlined,
  DownOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  ExpandOutlined,
  FileTextOutlined,
  LineChartOutlined,
  PieChartOutlined,
  TableOutlined,
  FilePdfOutlined
} from "@ant-design/icons";
import { Pagination, message, Modal } from "antd";
import React, { useRef } from "react";
import { useEffect, useMemo, useState } from "react";
import { ExecutionStep, QueryResult, ReportPayload } from "@/hooks/useChat";
import G2Chart, { G2ChartType } from "@/components/chat/G2Chart";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  steps: ExecutionStep[];
  summary?: string;
  reports?: ReportPayload[];
  queryResults?: QueryResult[];
  selectedStepId?: string;
  onSelectStep?: (stepId: string) => void;
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
  reports = [],
  queryResults = [],
  selectedStepId,
  onSelectStep
}: Props) {
  const [activeTab, setActiveTab] = useState<"steps" | "summary">("steps");
  const [summaryThinkExpanded, setSummaryThinkExpanded] = useState(false);
  const [summaryConclusionExpanded, setSummaryConclusionExpanded] = useState(true);
  const [stepDetailExpanded, setStepDetailExpanded] = useState(true);
  const [showReportDialog, setShowReportDialog] = useState(false);
  const [selectedReportIndex, setSelectedReportIndex] = useState(-1);
  const [resultTab, setResultTab] = useState<"chart" | "data" | "sql">("chart");
  const [chartType, setChartType] = useState<G2ChartType>("column");
  const [selectedQueryIndex, setSelectedQueryIndex] = useState(-1);
  const [showChartLabel, setShowChartLabel] = useState(false);
  const [dataPage, setDataPage] = useState(1);
  const pageSize = 20;
  const flowAgents = ["Planner", "DataAnalyst", "Charter", "Summarizer"] as const;
  const flowAgentLabelMap: Record<(typeof flowAgents)[number], string> = {
    Planner: "规划",
    DataAnalyst: "分析",
    Charter: "制图",
    Summarizer: "总结"
  };
  const activeReportIndex = useMemo(() => {
    if (!reports.length) return -1;
    if (selectedReportIndex < 0 || selectedReportIndex >= reports.length) {
      return reports.length - 1;
    }
    return selectedReportIndex;
  }, [reports, selectedReportIndex]);
  const activeReport = reports.length ? reports[activeReportIndex] : undefined;
  const reportOptionLabels = useMemo(() => {
    return reports.map((r, idx) => {
      const sub = r.subTaskIndex;
      const prefix = sub != null ? `子任务 ${sub + 1} · ` : "";
      const tail = idx === reports.length - 1 ? "（最新）" : `报告 ${idx + 1}`;
      return `${prefix}${r.title || tail}`;
    });
  }, [reports]);

  const selectedStep = useMemo(
    () => steps.find((s) => s.id === selectedStepId) ?? steps[steps.length - 1],
    [steps, selectedStepId]
  );
  const selectedStepTitleText = useMemo(() => normalizeToText(selectedStep?.title), [selectedStep?.title]);
  const selectedStepStatusText = useMemo(() => normalizeToText(selectedStep?.status), [selectedStep?.status]);
  const detailText = useMemo(() => normalizeToText(selectedStep?.detail), [selectedStep?.detail]);
  const summaryText = useMemo(() => normalizeToText(summary), [summary]);
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
  const summaryMarkdownText = useMemo(
    () => normalizeToText(parsedSummary.plain || ""),
    [parsedSummary.plain]
  );
  const safeSummaryMarkdownText = useMemo(() => {
    const normalized = normalizeToText(summaryMarkdownText);
    return typeof normalized === "string" ? normalized : String(normalized ?? "");
  }, [summaryMarkdownText]);
  const markdownPlugins = useMemo(() => [remarkGfm], []);
  const safeReportHtml = useMemo(() => normalizeToText(activeReport?.html || ""), [activeReport?.html]);
  const safeReportTitle = useMemo(() => normalizeToText(activeReport?.title || "Report"), [activeReport?.title]);
  const activeQuery = useMemo(() => {
    if (!queryResults.length) return undefined;
    if (selectedQueryIndex < 0 || selectedQueryIndex >= queryResults.length) {
      return queryResults[queryResults.length - 1];
    }
    return queryResults[selectedQueryIndex];
  }, [queryResults, selectedQueryIndex]);
  const queryDisplayLabels = useMemo(() => {
    return queryResults.map((item, idx) => {
      const cols = item.columns || [];
      const metricCols = cols.length >= 2 ? cols.slice(1, 4) : cols.slice(0, 3);
      const metricLabel = metricCols.filter(Boolean).join(" / ");
      const sql = normalizeToText(item.sql).replace(/\s+/g, " ").trim();
      const sqlBrief = sql
        .replace(/^select\s+/i, "")
        .replace(/\s+from\s+[\s\S]*$/i, "")
        .slice(0, 40);
      const main =
        metricLabel ||
        sqlBrief ||
        `查询 ${idx + 1}`;
      const prefix = idx === queryResults.length - 1 ? "最终查询" : `查询 ${idx + 1}`;
      return `${prefix}：${main}`;
    });
  }, [queryResults]);
  const activeQueryIndex = useMemo(() => {
    if (!queryResults.length) return -1;
    if (selectedQueryIndex < 0 || selectedQueryIndex >= queryResults.length) {
      return queryResults.length - 1;
    }
    return selectedQueryIndex;
  }, [queryResults, selectedQueryIndex]);
  const pagedRows = useMemo(() => {
    if (!activeQuery) return [];
    const start = (dataPage - 1) * pageSize;
    return activeQuery.rows.slice(start, start + pageSize);
  }, [activeQuery, dataPage]);
  useEffect(() => {
    setDataPage(1);
  }, [activeQueryIndex]);
  useEffect(() => {
    if (!reports.length) return;
    setSelectedReportIndex((prev) => {
      if (prev < 0 || prev >= reports.length) return reports.length - 1;
      return prev;
    });
  }, [reports]);
  const isToolResultStep = selectedStepTitleText.startsWith("工具结果:");
  const isStepError =
    selectedStepStatusText === "error" ||
    /execute failed|failed|error|异常|失败/i.test(detailText);
  const stepTitle = (selectedStepTitleText || "选择一个步骤查看详情").replace(/^工具结果:\s*/, "");
  const copyReportHtml = async () => {
    try {
      await navigator.clipboard.writeText(safeReportHtml);
      message.success("HTML 已复制");
    } catch {
      message.error("复制失败");
    }
  };
  const downloadReportHtml = () => {
    const blob = new Blob([safeReportHtml], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeReportTitle || "report"}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const reportIframeRef = useRef<HTMLIFrameElement | null>(null);
  const [exportingPdf, setExportingPdf] = useState(false);
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
      const canvas = await html2canvas(doc.body, { scale: 2, useCORS: true, backgroundColor: "#ffffff" });
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
  const flowStatus = useMemo(() => {
    const statusMap: Record<string, "idle" | "running" | "done" | "error"> = {};
    flowAgents.forEach((agent) => {
      statusMap[agent] = "idle";
    });
    steps.forEach((step) => {
      const [agent, event] = normalizeToText(step.title).split(":").map((v) => v.trim());
      if (!flowAgents.includes(agent as (typeof flowAgents)[number])) return;
      if (event === "error" || step.status === "error") {
        statusMap[agent] = "error";
      } else if (event === "start" || step.status === "running") {
        if (statusMap[agent] !== "error") statusMap[agent] = "running";
      } else if (event === "end" || step.status === "done") {
        if (statusMap[agent] !== "error") statusMap[agent] = "done";
      }
    });
    // 历史会话通常不会保留 start/end 事件；若无 running 且存在执行记录，则将 idle 兜底为 done。
    const hasSteps = steps.length > 0;
    const hasRunning = steps.some((step) => step.status === "running");
    if (hasSteps && !hasRunning) {
      flowAgents.forEach((agent) => {
        if (statusMap[agent] === "idle") statusMap[agent] = "done";
      });
    }
    return statusMap;
  }, [steps]);

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden border-l border-[#eceff5] bg-[#f8f9fc] dark:border-[#2f3441] dark:bg-[#171b24]">
      <div className="flex h-14 items-center justify-between border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
        <div className="flex items-center gap-2 text-sm font-semibold text-[#1f2937] dark:text-[#e2e8f0]">
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full bg-[#ef4444]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#f59e0b]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#22c55e]" />
          </span>
          <DesktopOutlined className="ml-1 text-[13px]" />
          <span>终端显示</span>
        </div>
        <a className="cursor-pointer text-[12px] text-[#3b82f6]">分享</a>
      </div>

      <div className="flex h-11 items-center justify-between border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
        <div className="flex items-center">
          <button
            onClick={() => setActiveTab("steps")}
            className={`mr-6 pb-2 text-sm font-medium ${
              activeTab === "steps"
                ? "border-b-2 border-black text-[#111827] dark:border-white dark:text-[#f8fafc]"
                : "text-[#94a3b8]"
            }`}
          >
            执行步骤
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
        <div className="ml-4 flex min-w-0 items-center gap-2 overflow-hidden">
          {flowAgents.map((agent, idx) => {
            const st = flowStatus[agent];
            const color =
              st === "error"
                ? "bg-[#ef4444]"
                : st === "running"
                  ? "bg-[#f59e0b]"
                  : st === "done"
                    ? "bg-[#22c55e]"
                    : "bg-[#cbd5e1]";
            return (
              <div key={agent} className="flex shrink-0 items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${color}`} />
                <span className="text-[11px] text-[#64748b] dark:text-[#94a3b8]">{flowAgentLabelMap[agent]}</span>
                {idx < flowAgents.length - 1 ? <span className="text-[#cbd5e1]">→</span> : null}
              </div>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-scroll overflow-x-hidden p-4 text-[#94a3b8]">
        {activeTab === "steps" && steps.length ? (
          <div className="flex h-full min-w-0 flex-col">
            <div className="flex min-h-0 flex-1 min-w-0 flex-col rounded-lg border border-[#e5e7eb] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
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
                  <div className="truncate text-sm font-semibold text-[#0f172a] dark:text-[#e2e8f0]">
                    {isToolResultStep ? `工具结果: ${stepTitle}` : selectedStepTitleText || "选择一个步骤查看详情"}
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
              <div className="min-h-0 min-w-0 flex-1 overflow-auto">
                {stepDetailExpanded && parsedDetail.thinkBlocks.length ? (
                  <div className="mb-3 space-y-2">
                    {parsedDetail.thinkBlocks.map((block, idx) => (
                      <div key={`think-${idx}`} className="rounded-md border border-[#dbeafe] bg-[#eff6ff] px-3 py-2 dark:border-[#1d4ed8] dark:bg-[#172554]">
                        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">think</div>
                        <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-[#1e3a8a] dark:text-[#bfdbfe]">{block}</pre>
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
                      <pre className="m-0 text-[#1e3a8a] dark:text-[#bfdbfe]">
                        {markdownDetailText}
                      </pre>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : activeTab === "summary" ? (
          summary || reports.length || queryResults.length ? (
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
              {parsedSummary.plain ? (
                <div className="rounded-xl border border-[#e6eefc] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <button
                    onClick={() => setSummaryConclusionExpanded((prev) => !prev)}
                    className="flex w-full items-center justify-between text-left"
                  >
                    <span className="text-xs font-semibold tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">
                      结论总结
                    </span>
                    <DownOutlined
                      className={`text-[12px] text-[#64748b] transition-transform ${
                        summaryConclusionExpanded ? "rotate-180" : "rotate-0"
                      }`}
                    />
                  </button>
                  {summaryConclusionExpanded ? (
                    <div className="mt-2 rounded-md border border-[#dbeafe] bg-[#eff6ff] p-3 text-sm leading-7 text-[#1e3a8a] dark:border-[#1d4ed8] dark:bg-[#172554] dark:text-[#bfdbfe]">
                      <div className="prose max-w-none overflow-x-auto text-[#1e3a8a] dark:prose-invert dark:text-[#bfdbfe] [&_p]:text-inherit [&_li]:text-inherit [&_strong]:text-inherit [&_strong]:font-bold [&_b]:text-inherit [&_b]:font-bold [&_h1]:text-inherit [&_h1]:text-2xl [&_h1]:leading-9 [&_h1]:font-bold [&_h2]:text-inherit [&_h2]:text-xl [&_h2]:leading-8 [&_h2]:font-semibold [&_h3]:text-inherit [&_h3]:text-lg [&_h3]:leading-7 [&_h3]:font-semibold [&_h4]:text-inherit [&_h5]:text-inherit [&_h6]:text-inherit [&_code]:bg-transparent [&_code]:px-0 [&_code]:font-mono [&_pre]:bg-transparent [&_pre]:p-0 [&_table]:w-max [&_table]:min-w-full [&_table]:border-collapse [&_table]:border [&_table]:border-[#93c5fd] dark:[&_table]:border-[#2b425f] [&_th]:border [&_th]:border-[#93c5fd] dark:[&_th]:border-[#2b425f] [&_th]:px-2 [&_th]:py-1 [&_th]:font-semibold [&_td]:border [&_td]:border-[#93c5fd] dark:[&_td]:border-[#2b425f] [&_td]:px-2 [&_td]:py-1">
                        <ReactMarkdown remarkPlugins={markdownPlugins}>{safeSummaryMarkdownText}</ReactMarkdown>
                      </div>
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
                        {reports.length > 1 ? (
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
                          onClick={() => setShowReportDialog(true)}
                          className="inline-flex h-6 items-center gap-1 rounded-md border border-[#d9e2ef] bg-white px-2 text-[11px] text-[#3b82f6] transition-colors hover:border-[#93c5fd] dark:border-[#334155] dark:bg-[#0f172a]"
                        >
                          <ExpandOutlined />
                          <span>展开</span>
                        </button>
                      </div>
                    </div>
                    <iframe
                      key={`report-${activeReportIndex}-${safeReportHtml.length}`}
                      ref={reportIframeRef}
                      title={safeReportTitle}
                      className="h-[360px] w-full border-0"
                      srcDoc={safeReportHtml}
                      sandbox="allow-scripts allow-same-origin"
                      referrerPolicy="no-referrer"
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
                        {queryResults.map((item, idx) => (
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
                        <div className="text-xs font-semibold text-[#344054] dark:text-[#cbd5e1]">图表展示</div>
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
                                {col}
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
              {!parsedSummary.thinkBlocks.length && !parsedSummary.plain && !reports.length && !activeQuery ? (
                <div className="flex h-full items-center justify-center text-sm">暂无摘要</div>
              ) : null}
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm">暂无摘要</div>
          )
        ) : steps.length ? (
          <div className="space-y-2">
            {steps.map((step) => (
              <button
                key={step.id}
                onClick={() => onSelectStep?.(step.id)}
                className="w-full rounded-xl border border-[#e5e7eb] bg-white p-3 text-left dark:border-[#2f3441] dark:bg-[#11131a]"
              >
                <div className="text-sm font-medium text-[#0f172a] dark:text-[#e2e8f0]">{normalizeToText(step.title)}</div>
                {step.detail ? (
                  <div className="mt-1 line-clamp-3 text-xs text-[#64748b] dark:text-[#94a3b8]">{normalizeToText(step.detail)}</div>
                ) : null}
              </button>
            ))}
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-2xl bg-white/70 text-3xl dark:bg-white/10">
              <FileTextOutlined />
            </div>
            <div className="text-base">选择一个步骤查看详情</div>
            <div className="mt-2 text-sm">点击左侧的步骤卡片以显示执行结果</div>
          </div>
        )}
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
          key={`report-dialog-${safeReportHtml.length}`}
          title={`${safeReportTitle}-full`}
          className="h-[72vh] w-full border-0"
          srcDoc={safeReportHtml}
          sandbox="allow-scripts allow-same-origin"
          referrerPolicy="no-referrer"
        />
      </Modal>
    </div>
  );
}
