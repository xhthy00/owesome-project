import { useCallback, useRef, useState } from "react";
import { createConversation, getConversationDetail, sendMessageStream } from "@/api/adapter/chatAdapter";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export type ExecutionStep = {
  id: string;
  title: string;
  detail?: string;
  status: "running" | "done" | "error";
  section?: "plan" | "step" | "result";
  subTaskIndex?: number;
  round?: number;
  progressPct?: number;
  rowCount?: number;
};

type SendOptions = {
  datasourceId?: number;
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

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [executionSteps, setExecutionSteps] = useState<ExecutionStep[]>([]);
  const [summary, setSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<number | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const datasourceId = Number(process.env.NEXT_PUBLIC_DEFAULT_DATASOURCE_ID ?? 1);
  // 为了与 Vue 版本保持一致，这里固定使用 team 模式（Planner → 子任务 → 工具调用）。
  const agentMode: "team" = "team";

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
      stop();
      const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: asText(input) };
      const assistantId = crypto.randomUUID();
      setMessages((prev) => [...prev, userMsg, { id: assistantId, role: "assistant", content: "" }]);
      setLoading(true);

      const controller = new AbortController();
      abortRef.current = controller;
      setExecutionSteps([
        {
          id: "plan-bootstrap",
          title: "准备执行计划",
          detail: "正在初始化 Planner...",
          status: "running",
          section: "plan"
        }
      ]);
      setSummary("");

      let latest = "";
      const stripBootstrap = (prev: ExecutionStep[]) => prev.filter((s) => s.id !== "plan-bootstrap");
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
            enable_tool_agent: true
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
                  id: `plan-${idx}`,
                  title: `计划 ${idx + 1}: ${p}`,
                  detail: sub_task_agents?.[idx] ? `执行角色: ${sub_task_agents[idx]}` : "",
                  status: "running" as const,
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
                const planId = `plan-${payload.index}`;
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
                  section: "step"
                }
              ]);
            },
            onAgentSpeak: ({ agent, status, error }) => {
              if (!agent || !status) return;
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: crypto.randomUUID(),
                  title: `${asText(agent)}: ${asText(status)}`,
                  detail: asText(error),
                  status: status === "error" ? "error" : status === "start" ? "running" : "done",
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
                  section: "result"
                }
              ]);
            },
            onReport: ({ title, mode, sub_task_index }) => {
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: crypto.randomUUID(),
                  title: "生成报告",
                  detail: `${asText(title) || "Report"}${mode ? ` (${asText(mode)})` : ""}`,
                  status: "done",
                  section: "result",
                  subTaskIndex: sub_task_index
                }
              ]);
            },
            onAgentThought: ({ text, sub_task_index }) => {
              const safeText = asText(text);
              if (!safeText.trim()) return;
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: crypto.randomUUID(),
                  title: "Agent 思考",
                  detail: safeText,
                  status: "running",
                  section: "step",
                  subTaskIndex: sub_task_index
                }
              ]);
            },
            onToolCall: ({ tool, args, round, sub_task_index }) => {
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                {
                  id: `tool-${sub_task_index ?? -1}-${round ?? prev.length}-${tool}`,
                  title: `调用工具: ${asText(tool)}`,
                  detail: asText(args),
                  status: "running",
                  section: "step",
                  subTaskIndex: sub_task_index,
                  round
                }
              ]);
            },
            onToolResult: ({ tool, success, content, round, sub_task_index, elapsed_ms }) => {
              const safeTool = asText(tool);
              const id = `tool-${sub_task_index ?? -1}-${round ?? -1}-${safeTool}`;
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
                    section: "result",
                    subTaskIndex: sub_task_index,
                    round
                  }
                ];
              });
            },
            onSql: (sql, chartType) => {
              const safeSql = asText(sql);
              if (safeSql.trim()) appendAssistant(`SQL（${asText(chartType)}）：\n${safeSql}`);
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                { id: crypto.randomUUID(), title: "生成 SQL", detail: safeSql, status: "done", section: "result" }
              ]);
            },
            onResult: (result) => {
              const rowCount = result?.row_count ?? 0;
              appendAssistant(`执行完成，返回 ${rowCount} 行结果。`);
              setExecutionSteps((prev) => [
                ...stripBootstrap(prev),
                { id: crypto.randomUUID(), title: "执行 SQL", detail: `返回 ${rowCount} 行`, status: "done", section: "result" }
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
                    ? { ...step, status: "error", detail: safeMsg || step.detail }
                    : step
                ),
                {
                  id: `tool-error-${crypto.randomUUID()}`,
                  title: "工具调用失败",
                  detail: safeMsg,
                  status: "error",
                  section: "step"
                }
              ]);
            },
            onDone: () => {
              setLoading(false);
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
    [stop, ensureConversation, datasourceId, agentMode]
  );

  const loadConversation = useCallback(async (targetConversationId: number) => {
    const detail = await getConversationDetail(targetConversationId);
    setConversationId(detail.id);

    const nextMessages: Message[] = [];
    const nextSteps: ExecutionStep[] = [];
    let nextSummary = "";

    detail.records?.forEach((record) => {
      if (asText(record.question).trim()) {
        nextMessages.push({ id: `u-${record.id}`, role: "user", content: asText(record.question) });
      }
      const answer = asText(record.summary || record.reasoning || "").trim();
      if (answer) {
        nextMessages.push({ id: `a-${record.id}`, role: "assistant", content: answer });
      }
      if (asText(record.summary).trim()) {
        nextSummary = asText(record.summary);
      }

      if (record.plans?.length) {
        record.plans.forEach((p, idx) => {
          const ps = record.plan_states?.find((s) => s.index === idx);
          nextSteps.push({
            id: `plan-${record.id}-${idx}`,
            title: `计划 ${idx + 1}: ${p}`,
            detail: ps?.sub_task_agent ? `执行角色: ${asText(ps.sub_task_agent)}` : "",
            status: ps?.state === "ok" ? "done" : ps?.state === "error" ? "error" : "running",
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
          section: "result",
          subTaskIndex: tc.sub_task_index,
          round: tc.round
        });
      });

      record.steps?.forEach((st, idx) => {
        nextSteps.push({
          id: `step-${record.id}-${idx}`,
          title: asText(st.label || st.name || "步骤"),
          detail: asText(st.detail),
          status: st.status === "error" ? "error" : "done",
          section: "step",
          subTaskIndex: st.sub_task_index
        });
      });
    });

    setMessages(nextMessages);
    setExecutionSteps(nextSteps);
    setSummary(nextSummary);
    setLoading(false);
  }, []);

  const clearConversation = useCallback(() => {
    setConversationId(undefined);
    setMessages([]);
    setExecutionSteps([]);
    setSummary("");
    setLoading(false);
  }, []);

  return { messages, executionSteps, summary, loading, send, stop, loadConversation, clearConversation };
}
