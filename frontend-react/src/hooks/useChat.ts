import { useCallback, useRef, useState } from "react";
import { createConversation, getConversationDetail, sendMessageStream } from "@/api/adapter/chatAdapter";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  runId?: string;
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
};

export type ReportPayload = {
  title: string;
  html: string;
  mode?: string;
  subTaskIndex?: number;
};

export type QueryResult = {
  key: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  rowCount: number;
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

export const extractReportFromToolData = (
  data: Record<string, unknown> | undefined,
  subTaskIndex?: number
): ReportPayload | null => {
  if (!data) return null;
  const html = asText(data.html).trim();
  if (data.output_type === "html" && html) {
    return {
      title: asText(data.title) || "Report",
      html,
      mode: data.mode ? asText(data.mode) : undefined,
      subTaskIndex
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
      title: asText(c.title) || asText(data.title) || "Report",
      html: chunkHtml,
      mode: data.mode ? asText(data.mode) : undefined,
      subTaskIndex
    };
  }
  return null;
};

const appendReportIfNew = (prev: ReportPayload[], report: ReportPayload): ReportPayload[] => {
  if (!report.html.trim()) return prev;
  if (prev.some((r) => r.html === report.html)) return prev;
  return [...prev, report];
};

const deriveReportsFromRecord = (record: {
  reports?: Array<{ title?: string; html?: string; mode?: string; sub_task_index?: number }>;
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
    record.reports.forEach((r) => {
      const html = asText(r?.html).trim();
      if (!html) return;
      reports.push({
        title: asText(r?.title) || "Report",
        html,
        mode: r?.mode ? asText(r.mode) : undefined,
        subTaskIndex: r?.sub_task_index
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
  const [reports, setReports] = useState<ReportPayload[]>([]);
  const [queryResults, setQueryResults] = useState<QueryResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<number | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const defaultDatasourceId = Number(process.env.NEXT_PUBLIC_DEFAULT_DATASOURCE_ID ?? 1);
  const [datasourceId, setDatasourceIdState] = useState<number>(defaultDatasourceId);
  // 为了与 Vue 版本保持一致，这里固定使用 team 模式（Planner → 子任务 → 工具调用）。
  const agentMode: "team" = "team";
  const [reportAudience, setReportAudience] = useState<string | undefined>(undefined);

  const setDatasourceId = useCallback((id: number) => {
    setDatasourceIdState((prev) => {
      if (prev !== id) {
        setConversationId(undefined);
      }
      return id;
    });
  }, []);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setLoading(false);
  }, []);

  const ensureConversation = useCallback(async (targetDatasourceId: number) => {
    if (conversationId) return conversationId;
    const created = await createConversation({
      title: "New Chat",
      datasource_id: targetDatasourceId
    });
    setConversationId(created.id);
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
      const runId = crypto.randomUUID();
      const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: asText(input), runId };
      const assistantId = crypto.randomUUID();
      setMessages((prev) => [...prev, userMsg, { id: assistantId, role: "assistant", content: "", runId }]);
      setLoading(true);

      const controller = new AbortController();
      abortRef.current = controller;
      const bootstrapId = `plan-bootstrap-${runId}`;
      setExecutionSteps((prev) => [
        ...prev,
        {
          id: bootstrapId,
          title: "准备执行计划",
          detail: "正在初始化 Planner...",
          status: "running",
          runId,
          section: "plan"
        }
      ]);
      setSummary("");
      setReports([]);
      setQueryResults([]);

      let latest = "";
      let latestSql = "";
      const stripBootstrap = (prev: ExecutionStep[]) => prev.filter((s) => s.id !== bootstrapId);
      const writeAssistant = (content: string) => {
        latest = content;
        setMessages((prev) => prev.map((msg) => (msg.id === assistantId ? { ...msg, content } : msg)));
      };
      const appendAssistant = (content: string) => {
        writeAssistant(latest ? `${latest}\n\n${content}` : content);
      };

      try {
        const convId = await ensureConversation(targetDatasourceId);
        await sendMessageStream(
          {
            question: input,
            datasource_id: targetDatasourceId,
            conversation_id: convId,
            agent_mode: agentMode,
            enable_tool_agent: true,
            ...(targetAudience ? { report_audience: targetAudience } : {})
          },
          {
            onReasoning: (text) => {
              const safeText = asText(text);
              if (safeText.trim()) appendAssistant(`思考：\n${safeText}`);
            },
            onPlan: ({ plans, sub_task_agents }) => {
              if (!plans?.length) return;
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
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: crypto.randomUUID(),
                  title: asText(step.label),
                  detail: asText(step.detail),
                  status: step.status === "error" ? "error" : "done",
                  runId,
                  section: "step"
                }
              ]);
            },
            onAgentSpeak: ({ agent, status, error }) => {
              if (!agent || !status) return;
              setExecutionSteps((prev) => [
                ...prev,
                {
                  id: crypto.randomUUID(),
                  title: `${asText(agent)}: ${asText(status)}`,
                  detail: asText(error),
                  status: status === "error" ? "error" : status === "start" ? "running" : "done",
                  runId,
                  section: "step"
                }
              ]);
            },
            onChart: ({ chart_type }) => {
              if (!chart_type) return;
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: crypto.randomUUID(),
                  title: "图表推荐",
                  detail: `推荐图表类型: ${chart_type}`,
                  status: "done",
                  runId,
                  section: "result"
                }
              ]);
            },
            onReport: ({ title, html, mode, sub_task_index }) => {
              const report: ReportPayload = {
                title: asText(title) || "Report",
                html: asText(html),
                mode: mode ? asText(mode) : undefined,
                subTaskIndex: sub_task_index
              };
              setReports((prev) => appendReportIfNew(prev, report));
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: crypto.randomUUID(),
                  title: "生成报告",
                  detail: `${asText(title) || "Report"}${mode ? ` (${asText(mode)})` : ""}`,
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
              setExecutionSteps((prev) => [
                ...prev,
                {
                  id: crypto.randomUUID(),
                  title: "Agent 思考",
                  detail: safeText,
                  status: "running",
                  runId,
                  section: "step",
                  subTaskIndex: sub_task_index
                }
              ]);
            },
            onToolCall: ({ tool, args, round, sub_task_index }) => {
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: `tool-${runId}-${sub_task_index ?? -1}-${round ?? prev.length}-${tool}`,
                  title: `调用工具: ${asText(tool)}`,
                  detail: asText(args),
                  status: "running",
                  runId,
                  section: "step",
                  subTaskIndex: sub_task_index,
                  round
                }
              ]);
            },
            onToolResult: ({ tool, success, content, data, round, sub_task_index, elapsed_ms }) => {
              const safeTool = asText(tool);
              if (safeTool === "execute_sql" && data && typeof data === "object") {
                const rawColumns = Array.isArray(data.columns) ? data.columns : [];
                const rawRows = Array.isArray(data.rows) ? data.rows : [];
                const safeColumns = rawColumns.map((col) => asText(col));
                if (safeColumns.length && rawRows.length) {
                  setQueryResults((prev) => [
                    ...prev,
                    {
                      key: crypto.randomUUID(),
                      sql: asText(data.sql) || latestSql,
                      columns: safeColumns,
                      rows: rawRows,
                      rowCount: typeof data.row_count === "number" ? data.row_count : rawRows.length
                    }
                  ]);
                }
              }
              const id = `tool-${runId}-${sub_task_index ?? -1}-${round ?? -1}-${safeTool}`;
              if (data && typeof data === "object") {
                const extracted = extractReportFromToolData(data as Record<string, unknown>, sub_task_index);
                if (extracted) {
                  setReports((prev) => appendReportIfNew(prev, extracted));
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
                          detail: `${asText(content)}${elapsed_ms ? `\n耗时: ${elapsed_ms}ms` : ""}`.trim(),
                          status: success ? "done" : "error",
                          runId,
                          section: "result"
                        }
                      : step
                  );
                }
                return [
                  ...base,
                  {
                    id,
                    title: `工具结果: ${safeTool}`,
                    detail: `${asText(content)}${elapsed_ms ? `\n耗时: ${elapsed_ms}ms` : ""}`.trim(),
                    status: success ? "done" : "error",
                    runId,
                    section: "result",
                    subTaskIndex: sub_task_index,
                    round
                  }
                ];
              });
            },
            onSql: (sql, chartType) => {
              const safeSql = asText(sql);
              latestSql = safeSql;
              if (safeSql.trim()) appendAssistant(`SQL（${asText(chartType)}）：\n${safeSql}`);
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                { id: crypto.randomUUID(), title: "生成 SQL", detail: safeSql, status: "done", runId, section: "result" }
              ]);
            },
            onResult: (result) => {
              const rowCount = result?.row_count ?? 0;
              const safeColumns = Array.isArray(result?.columns) ? result.columns.map((col) => asText(col)) : [];
              const safeRows = Array.isArray(result?.rows) ? result.rows : [];
              if (safeColumns.length && safeRows.length) {
                setQueryResults((prev) => [
                  ...prev,
                  {
                    key: crypto.randomUUID(),
                    sql: latestSql,
                    columns: safeColumns,
                    rows: safeRows,
                    rowCount
                  }
                ]);
              }
              appendAssistant(`执行完成，返回 ${rowCount} 行结果。`);
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                { id: crypto.randomUUID(), title: "执行 SQL", detail: `返回 ${rowCount} 行`, status: "done", runId, section: "result" }
              ]);
            },
            onFinalAnswer: (content) => {
              const safeContent = asText(content);
              if (safeContent.trim()) writeAssistant(safeContent);
            },
            onSummary: (content) => {
              const safeContent = asText(content);
              if (safeContent.trim()) {
                writeAssistant(safeContent);
                setSummary(safeContent);
              }
            },
            onError: (msg) => {
              const safeMsg = asText(msg);
              writeAssistant(`请求失败：${safeMsg}`);
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev).map((step) =>
                  step.section === "plan" && step.status === "running"
                    ? { ...step, status: "error" as const, detail: safeMsg || step.detail }
                    : step
                ),
                {
                  id: `tool-error-${crypto.randomUUID()}`,
                  title: "工具调用失败",
                  detail: safeMsg,
                  status: "error",
                  runId,
                  section: "step"
                }
              ]);
            },
            onDone: async (recordId) => {
              setLoading(false);
              if (recordId > 0 && convId) {
                try {
                  const detail = await getConversationDetail(convId);
                  const record =
                    detail.records?.find((r) => r.id === recordId) ??
                    detail.records?.[detail.records.length - 1];
                  if (record) {
                    const derived = deriveReportsFromRecord(record);
                    if (derived.length) {
                      setReports((prev) => {
                        let next = [...prev];
                        for (const r of derived) {
                          next = appendReportIfNew(next, r);
                        }
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
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        writeAssistant(`请求失败：${asText(msg)}`);
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        sendingRef.current = false;
        setLoading(false);
      }
    },
    [stop, ensureConversation, datasourceId, agentMode, reportAudience]
  );

  const loadConversation = useCallback(async (targetConversationId: number) => {
    const detail = await getConversationDetail(targetConversationId);
    setConversationId(detail.id);
    if (detail.datasource_id) {
      setDatasourceIdState(detail.datasource_id);
    }

    const nextMessages: Message[] = [];
    const nextSteps: ExecutionStep[] = [];
    let nextSummary = "";
    const nextReports: ReportPayload[] = [];
    const nextQueryResults: QueryResult[] = [];
    const queryResultSignatures = new Set<string>();

    detail.records?.forEach((record) => {
      if (asText(record.question).trim()) {
        nextMessages.push({ id: `u-${record.id}`, role: "user", content: asText(record.question), runId: `record-${record.id}` });
      }
      const answer = asText(record.summary || record.reasoning || "").trim();
      if (answer) {
        nextMessages.push({ id: `a-${record.id}`, role: "assistant", content: answer, runId: `record-${record.id}` });
      }
      if (asText(record.summary).trim()) {
        nextSummary = asText(record.summary);
      }
      nextReports.push(...deriveReportsFromRecord(record));
      const sqlText = asText(record.sql);
      const execColumns = Array.isArray(record.exec_result?.columns)
        ? record.exec_result.columns.map((col) => asText(col))
        : [];
      const execRows = Array.isArray(record.exec_result?.rows) ? record.exec_result.rows : [];
      if (sqlText && execColumns.length && execRows.length) {
        const signature = `${sqlText}|${execColumns.join(",")}|${record.exec_result?.row_count ?? execRows.length}`;
        if (!queryResultSignatures.has(signature)) {
          queryResultSignatures.add(signature);
          nextQueryResults.push({
            key: `record-${record.id}-exec`,
            sql: sqlText,
            columns: execColumns,
            rows: execRows,
            rowCount: record.exec_result?.row_count ?? execRows.length
          });
        }
      }

      if (record.plans?.length) {
        record.plans.forEach((p, idx) => {
          const ps = record.plan_states?.find((s) => s.index === idx);
          nextSteps.push({
            id: `plan-${record.id}-${idx}`,
            title: `计划 ${idx + 1}: ${p}`,
            detail: ps?.sub_task_agent ? `执行角色: ${asText(ps.sub_task_agent)}` : "",
            status: ps?.state === "ok" ? "done" : ps?.state === "error" ? "error" : "running",
            runId: `record-${record.id}`,
            section: "plan",
            subTaskIndex: idx,
            progressPct: ps?.state === "ok" ? 100 : 0,
            rowCount: ps?.row_count
          });
        });
      }

      record.tool_calls?.forEach((tc, idx) => {
        nextSteps.push({
          id: `tool-${record.id}-${idx}`,
          title: `工具结果: ${asText(tc.tool) || "tool"}`,
          detail: `${asText(tc.content)}${tc.elapsed_ms ? `\n耗时: ${tc.elapsed_ms}ms` : ""}`.trim(),
          status: tc.success === false ? "error" : "done",
          runId: `record-${record.id}`,
          section: "result",
          subTaskIndex: tc.sub_task_index,
          round: tc.round
        });
        if (asText(tc.tool) === "execute_sql" && tc.data && typeof tc.data === "object") {
          const rawColumns = Array.isArray(tc.data.columns) ? tc.data.columns : [];
          const rawRows = Array.isArray(tc.data.rows) ? tc.data.rows : [];
          const safeColumns = rawColumns.map((col) => asText(col));
          if (safeColumns.length && rawRows.length) {
            const sql = asText(tc.data.sql);
            const rowCount = typeof tc.data.row_count === "number" ? tc.data.row_count : rawRows.length;
            const signature = `${sql}|${safeColumns.join(",")}|${rowCount}`;
            if (!queryResultSignatures.has(signature)) {
              queryResultSignatures.add(signature);
              nextQueryResults.push({
                key: `record-${record.id}-tool-${idx}`,
                sql,
                columns: safeColumns,
                rows: rawRows,
                rowCount
              });
            }
          }
        }
      });

      record.steps?.forEach((st, idx) => {
        nextSteps.push({
          id: `step-${record.id}-${idx}`,
          title: asText(st.label || st.name || "步骤"),
          detail: asText(st.detail),
          status: st.status === "error" ? "error" : "done",
          runId: `record-${record.id}`,
          section: "step",
          subTaskIndex: st.sub_task_index
        });
      });
    });

    setMessages(nextMessages);
    setExecutionSteps(nextSteps);
    setSummary(nextSummary);
    setReports(nextReports);
    setQueryResults(nextQueryResults);
    setLoading(false);
  }, []);

  const clearConversation = useCallback(() => {
    setConversationId(undefined);
    setMessages([]);
    setExecutionSteps([]);
    setSummary("");
    setReports([]);
    setQueryResults([]);
    setLoading(false);
  }, []);

  return {
    messages,
    executionSteps,
    summary,
    reports,
    queryResults,
    loading,
    send,
    stop,
    loadConversation,
    clearConversation,
    reportAudience,
    setReportAudience,
    datasourceId,
    setDatasourceId
  };
}
