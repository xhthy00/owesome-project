import {
  AuditOutlined,
  BarChartOutlined,
  CopyOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  ExpandOutlined,
  FullscreenOutlined,
  LineChartOutlined,
  PieChartOutlined,
  TableOutlined,
  FilePdfOutlined,
  FileWordOutlined
} from "@ant-design/icons";
import { Input, Pagination, message, Modal, Tooltip } from "antd";
import React, { useRef } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  ChartRecommendation,
  QueryResult,
  ReportPayload,
  formatReportDisplayTitle
} from "@/hooks/useChat";
import G2Chart, {
  G2ChartType,
  formatQuerySetLabel,
  inferChartFields,
  pickYField,
  preferQueryTableOverChart
} from "@/components/chat/G2Chart";
import { UserWatermark } from "@/components/UserWatermark";
import { labelColumns } from "@/utils/columnLabels";
import { formatNumericDisplay } from "@/utils/formatNumericDisplay";
import {
  extractRecommendationsText,
  hasRecommendationsSection
} from "@/utils/reportRecommendations";
import { exportReportAsWord } from "@/utils/exportReportWord";
import { exportIframeReportPdf } from "@/utils/exportReportPdf";
import {
  bindIframeWatermark,
  stampHtmlWatermark
} from "@/utils/userWatermark";
type Props = {
  reports?: ReportPayload[];
  queryResults?: QueryResult[];
  chartByRunId?: Record<string, ChartRecommendation>;
  selectedRunId: string;
  onPatchReport?: (
    report: ReportPayload,
    patch: { recommendationsText?: string; reviewStatus?: "pending" | "approved" }
  ) => Promise<ReportPayload>;
};

function formatQueryCell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "string") {
    const formatted = formatNumericDisplay(value);
    if (typeof value === "number") return formatted;
    if (formatted !== value) return formatted;
  }
  return normalizeToText(value);
}

function isTargetRankRow(columns: string[], row: unknown[]): boolean {
  const idx = columns.findIndex((col) => /标记|本班/.test(col));
  if (idx < 0) return false;
  return String((row as unknown[])[idx] ?? "").includes("本班");
}

function QueryResultTable({
  columns,
  rows,
  rowCount,
  page,
  pageSize,
  onPageChange,
  maxHeight
}: {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  maxHeight?: string | number;
}) {
  const headers = labelColumns(columns);
  return (
    <div
      className="overflow-auto rounded-md border border-[#e5e7eb] dark:border-[#2f3441]"
      style={maxHeight != null ? { maxHeight } : undefined}
    >
      <table className="min-w-full border-collapse text-xs">
        <thead className="sticky top-0 z-[1] bg-[#f8fafc] dark:bg-[#141923]">
          <tr>
            {columns.map((col, index) => (
              <th
                key={col}
                className="whitespace-nowrap border border-[#e5e7eb] px-2 py-1 text-left dark:border-[#2f3441]"
              >
                {headers[index]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx} className={isTargetRankRow(columns, row as unknown[]) ? "bg-[#eff6ff] dark:bg-[#172554]" : undefined}>
              {columns.map((col, colIdx) => (
                <td
                  key={`${idx}-${col}`}
                  className="whitespace-nowrap border border-[#e5e7eb] px-2 py-1 dark:border-[#2f3441]"
                >
                  {formatQueryCell((row as unknown[])[colIdx])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="sticky bottom-0 flex items-center justify-between gap-2 bg-white px-2 py-2 dark:bg-[#11131a]">
        <span className="text-[11px] text-[#98a2b3]">{`共 ${rowCount} 行`}</span>
        <Pagination
          size="small"
          current={page}
          pageSize={pageSize}
          total={rowCount}
          onChange={onPageChange}
          showSizeChanger={false}
        />
      </div>
    </div>
  );
}

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

export default function ChatExecutionPanel({
  reports: scopedReports = [],
  queryResults: scopedQueryResults = [],
  chartByRunId = {},
  selectedRunId,
  onPatchReport
}: Props) {
  const [showReportDialog, setShowReportDialog] = useState(false);
  const [showQueryDialog, setShowQueryDialog] = useState(false);
  const [selectedReportIndex, setSelectedReportIndex] = useState(-1);
  const [resultTab, setResultTab] = useState<"chart" | "data">("chart");
  const [chartType, setChartType] = useState<G2ChartType>("column");
  const [selectedQueryIndex, setSelectedQueryIndex] = useState(-1);
  const [showChartLabel, setShowChartLabel] = useState(false);
  const [dataPage, setDataPage] = useState(1);
  const [fullDataPage, setFullDataPage] = useState(1);
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const pageSize = 20;
  const fullPageSize = 100;
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
  const preferQueryTable = useMemo(() => {
    if (!activeQuery) return false;
    const fields = inferChartFields(activeQuery.columns, activeQuery.rows, {
      xField: chartFieldOverride.xField,
      yField: chartFieldOverride.yField
    });
    return preferQueryTableOverChart(fields.yField);
  }, [activeQuery, chartFieldOverride]);
  // 排名问句默认出表（附近班对照），不用名次当柱高
  const effectiveResultTab = preferQueryTable && resultTab === "chart" ? "data" : resultTab;
  const pagedRows = useMemo(() => {
    if (!activeQuery) return [];
    const start = (dataPage - 1) * pageSize;
    return activeQuery.rows.slice(start, start + pageSize);
  }, [activeQuery, dataPage]);
  const fullPagedRows = useMemo(() => {
    if (!activeQuery) return [];
    const start = (fullDataPage - 1) * fullPageSize;
    return activeQuery.rows.slice(start, start + fullPageSize);
  }, [activeQuery, fullDataPage]);
  useEffect(() => {
    setDataPage(1);
    setFullDataPage(1);
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
      await exportIframeReportPdf({
        doc,
        filename: `${safeReportTitle || "report"}.pdf`
      });
      message.success("PDF 已导出");
    } catch (e) {
      message.error("PDF 导出失败");
      // eslint-disable-next-line no-console
      console.error(e);
    } finally {
      setExportingPdf(false);
    }
  };
  return (
    <div className="min-w-0 space-y-3">
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
              {preferQueryTable ? null : (
                <button
                  onClick={() => setResultTab("chart")}
                  className={`rounded px-2 py-1 text-[11px] ${effectiveResultTab === "chart" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
                >
                  图表
                </button>
              )}
              <button
                onClick={() => setResultTab("data")}
                className={`rounded px-2 py-1 text-[11px] ${effectiveResultTab === "data" ? "bg-[#dbeafe] text-[#1d4ed8]" : "text-[#667085]"}`}
              >
                数据
              </button>
              <button
                onClick={() => {
                  setResultTab("data");
                  setShowQueryDialog(true);
                }}
                className="ml-1 inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] text-[#3b82f6] hover:bg-[#dbeafe]"
                title="全屏查看数据表"
              >
                <FullscreenOutlined />
                <span>全屏</span>
              </button>
            </div>
          </div>

          {effectiveResultTab === "chart" ? (
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
                    onClick={() => {
                      setChartType("line");
                      setShowChartLabel(true);
                    }}
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

          {effectiveResultTab === "data" ? (
            <QueryResultTable
              columns={activeQuery.columns}
              rows={pagedRows}
              rowCount={activeQuery.rowCount}
              page={dataPage}
              pageSize={pageSize}
              onPageChange={setDataPage}
              maxHeight={360}
            />
          ) : null}

        </div>
      ) : null}
      <Modal
        title={queryDisplayLabels[activeQueryIndex] || "查询结果"}
        open={showQueryDialog}
        onCancel={() => setShowQueryDialog(false)}
        footer={null}
        width="96%"
        style={{ top: 16 }}
        styles={{ body: { padding: 12 } }}
        destroyOnClose
      >
        {activeQuery ? (
          <UserWatermark>
            <QueryResultTable
              columns={activeQuery.columns}
              rows={fullPagedRows}
              rowCount={activeQuery.rowCount}
              page={fullDataPage}
              pageSize={fullPageSize}
              onPageChange={setFullDataPage}
              maxHeight="78vh"
            />
          </UserWatermark>
        ) : null}
      </Modal>
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
