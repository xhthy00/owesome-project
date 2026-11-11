import { getApiBaseUrl, apiRequest } from "@/api/client";
import { getAccessToken } from "@/auth/session";
import { streamSSE } from "@/utils/sse";
import type { ContextUsage } from "@/utils/runMetrics";

/** 与 `pages/_app.tsx` / `api/client.ts` 中工作空间键一致 */
const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

/** 打开跨工作空间的历史任务时，把当前空间切到该会话所属 oid，后续请求才带对 Header。 */
export function syncWorkspaceOid(oid?: number | null) {
  if (typeof window === "undefined" || oid == null || !Number.isFinite(Number(oid))) return;
  const next = String(Number(oid));
  const current = window.localStorage.getItem(WORKSPACE_OID_STORAGE_KEY);
  if (current === next) return;
  window.localStorage.setItem(WORKSPACE_OID_STORAGE_KEY, next);
  window.dispatchEvent(new CustomEvent("workspace:changed", { detail: { oid: Number(oid) } }));
}

function chatStreamHeaders(): Record<string, string> {
  const token = getAccessToken();
  const wsOid =
    typeof window !== "undefined" ? window.localStorage.getItem(WORKSPACE_OID_STORAGE_KEY) : null;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream"
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (wsOid?.trim()) {
    headers["X-Workspace-Oid"] = wsOid.trim();
  }
  return headers;
}

export type SendMessagePayload = {
  conversationId?: string;
  message: string;
};

export type SendMessageResponse = {
  answer?: string;
};

export type Conversation = {
  id: number;
  title: string;
  datasource_id?: number | null;
  oid?: number;
  create_time?: string;
  update_time?: string;
};

export type ConversationRecord = {
  id: number;
  question: string;
  turn_type?: string;
  create_time?: string;
  finish_time?: string;
  reasoning?: string;
  summary?: string;
  total_tokens?: number | null;
  elapsed_ms?: number | null;
  steps?: Array<{ name?: string; label?: string; status?: string; elapsed_ms?: number; detail?: string; sub_task_index?: number; round?: number }>;
  tool_calls?: Array<{
    round?: number;
    tool?: string;
    success?: boolean;
    content?: string;
    elapsed_ms?: number;
    sub_task?: string;
    sub_task_index?: number;
    data?: {
      output_type?: string;
      html?: string;
      title?: string;
      mode?: string;
      sql?: string;
      columns?: unknown[];
      rows?: unknown[][];
      row_count?: number;
      chunks?: Array<{
        output_type?: string;
        content?: string;
        title?: string;
      }>;
    };
  }>;
  sql?: string;
  exec_result?: (ClarifyPayload & { columns?: string[]; rows?: unknown[][]; row_count?: number; clarify?: boolean; context_usage?: ContextUsage; human_reply_to?: string; human_answer?: string; human_cancelled_to?: string }) | null;
  chart_type?: string;
  chart_config?: Record<string, unknown> | null;
  reports?: Array<{
    title?: string;
    html?: string;
    mode?: string;
    sub_task_index?: number;
    report_type?: string;
    report_type_label?: string;
    review_status?: "pending" | "approved";
    recommendations_text?: string;
  }>;
  plan_states?: Array<{
    index: number;
    sub_task?: string;
    sub_task_agent?: string;
    state: "pending" | "running" | "ok" | "error";
    error?: string;
    row_count?: number;
  }>;
  plans?: string[];
  sub_task_agents?: string[];
};

export type ConversationDetail = Conversation & {
  records: ConversationRecord[];
};

export type TaskPlanStage = {
  content: string;
  status: "pending" | "in_progress" | "completed" | "failed";
};

export type ChatStreamPayload = {
  question: string;
  datasource_id: number;
  conversation_id?: number;
  resume_interrupt_id?: string;
};

export type HumanQuestionField = {
  label: string;
  kind: "single" | "multiple" | "text";
  options?: string[];
  required?: boolean;
  placeholder?: string;
};

export type ClarifyPayload = {
  interrupt_id?: string;
  question_id?: string;
  prompt?: string;
  fields?: HumanQuestionField[];
  status?: "pending" | "answered" | "cancelled";
  answer?: string;
  missing?: string[];
  options?: Record<string, string[]> | string[];
  filled?: Record<string, string>;
  original_question?: string;
  report_type?: string | null;
};

export type ErrorDetail = {
  failure_domain?:
    | "authorization"
    | "capability"
    | "sql_shape"
    | "execution"
    | "configuration"
    | "compiled_plan"
    | "context"
    | "service";
  is_permission_issue?: boolean;
  user_message?: string;
  repair_hint?: string;
};

type StreamHandlers = {
  onStep?: (step: NonNullable<ConversationRecord["steps"]>[number]) => void;
  onTaskPlan?: (stages: TaskPlanStage[]) => void;
  onAgentSpeak?: (payload: { agent?: string; status?: string; error?: string }) => void;
  onChart?: (payload: {
    chart_type?: string;
    chart_config?: Record<string, unknown>;
    chart_spec?: Record<string, unknown>;
  }) => void;
  onReport?: (payload: {
    title?: string;
    html?: string;
    mode?: string;
    sub_task_index?: number;
    report_type?: string;
    report_type_label?: string;
  }) => void;
  onPlan?: (payload: { plans: string[]; sub_task_agents?: string[] }) => void;
  onPlanUpdate?: (payload: {
    index: number;
    state: "running" | "ok" | "error";
    sub_task?: string;
    sub_task_agent?: string;
    error?: string;
    sql?: string;
    row_count?: number;
  }) => void;
  onReasoning?: (text: string) => void;
  /** 模型思考流增量（reasoning_content / <think>），在结论之前到达 */
  onReasoningDelta?: (text: string) => void;
  onSql?: (sql: string, chartType: string) => void;
  onResult?: (result: { columns: string[]; rows: unknown[][]; row_count: number }) => void;
  onAgentThought?: (payload: { text: string; sub_task_index?: number }) => void;
  onToolCall?: (payload: { tool: string; args?: Record<string, unknown>; round?: number; sub_task?: string; sub_task_index?: number }) => void;
  onToolResult?: (payload: {
    tool: string;
    success: boolean;
    content?: string;
    data?: Record<string, unknown>;
    round?: number;
    sub_task?: string;
    sub_task_index?: number;
    elapsed_ms?: number;
  }) => void;
  onFinalAnswer?: (content: string) => void;
  /** 结论正文增量；在 summary 之前到达，用于边生成边展示 */
  onAnswerDelta?: (text: string) => void;
  /** 作废当前已展示的草稿（该轮其实是工具调用，不是最终结论） */
  onAnswerDiscard?: () => void;
  onSummary?: (content: string) => void;
  onUsage?: (payload: ContextUsage & {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    delta_total?: number;
  }) => void;
  onError?: (msg: string, metadata?: { preserveInterrupt?: boolean; errorDetail?: ErrorDetail }) => void;
  onHumanResolved?: (payload: { interrupt_id: string; status: "answered" | "cancelled"; answer?: string }) => void;
  onDone?: (recordId: number) => void | Promise<void>;
  onClarify?: (payload: ClarifyPayload) => void;
};

export async function createConversation(payload: { title?: string; datasource_id: number }) {
  return apiRequest<Conversation>("/chat/conversations", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function updateConversation(id: number, payload: { title?: string; datasource_id?: number }) {
  return apiRequest<Conversation>(`/chat/conversations/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function listConversations(limit = 50) {
  const resp = await apiRequest<{ total: number; items: Conversation[] }>(`/chat/conversations?limit=${limit}`);
  return resp.items ?? [];
}

export async function getConversationDetail(conversationId: number) {
  return apiRequest<ConversationDetail>(`/chat/conversations/${conversationId}`);
}

export async function updateReportReview(payload: {
  conversationId: number;
  recordId: number;
  reportIndex: number;
  recommendations_text?: string;
  review_status?: "pending" | "approved";
}) {
  const { conversationId, recordId, reportIndex, ...body } = payload;
  return apiRequest<{
    record_id: number;
    report_index: number;
    report: {
      title?: string;
      html?: string;
      review_status?: string;
      recommendations_text?: string;
    };
  }>(`/chat/conversations/${conversationId}/records/${recordId}/reports/${reportIndex}`, {
    method: "PATCH",
    body: JSON.stringify(body)
  });
}

export async function replaceRecordReports(payload: {
  conversationId: number;
  recordId: number;
  reports: Array<Record<string, unknown>>;
}) {
  return apiRequest<{ record_id: number; reports: Array<Record<string, unknown>> }>(
    `/chat/conversations/${payload.conversationId}/records/${payload.recordId}/reports`,
    {
      method: "PUT",
      body: JSON.stringify({ reports: payload.reports })
    }
  );
}

export async function sendMessageStream(
  payload: ChatStreamPayload,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  return streamSSE({
    url: `${getApiBaseUrl()}/chat/chat-stream`,
    body: payload,
    headers: chatStreamHeaders(),
    signal,
    requireDoneEvent: true,
    onEvent: async (evt) => {
      if (signal?.aborted) return;
      const data = (evt.data ?? {}) as Record<string, unknown>;
      switch (evt.event) {
        case "step":
          handlers.onStep?.(data);
          break;
        case "task_plan":
          if (Array.isArray(data.stages)) handlers.onTaskPlan?.(data.stages as TaskPlanStage[]);
          break;
        case "plan":
          handlers.onPlan?.({
            plans: (data.plans as string[]) ?? [],
            sub_task_agents: (data.sub_task_agents as string[]) ?? []
          });
          break;
        case "plan_update":
          handlers.onPlanUpdate?.({
            index: Number(data.index ?? -1),
            state: ((data.state as string) ?? "running") as "running" | "ok" | "error",
            sub_task: (data.sub_task as string) ?? undefined,
            sub_task_agent: (data.sub_task_agent as string) ?? undefined,
            error: (data.error as string) ?? undefined,
            sql: (data.sql as string) ?? undefined,
            row_count: typeof data.row_count === "number" ? (data.row_count as number) : undefined
          });
          break;
        case "reasoning":
          handlers.onReasoning?.((data.text as string) ?? "");
          break;
        case "reasoning_delta":
          handlers.onReasoningDelta?.((data.text as string) ?? "");
          break;
        case "sql":
          handlers.onSql?.((data.sql as string) ?? "", (data.chart_type as string) ?? "table");
          break;
        case "result":
          handlers.onResult?.(data as { columns: string[]; rows: unknown[][]; row_count: number });
          break;
        case "agent_thought":
          handlers.onAgentThought?.({
            text: (data.text as string) ?? "",
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined
          });
          break;
        case "tool_call":
          handlers.onToolCall?.({
            tool: (data.tool as string) ?? "tool",
            args: (data.args as Record<string, unknown>) ?? {},
            round: typeof data.round === "number" ? (data.round as number) : undefined,
            sub_task: typeof data.sub_task === "string" ? data.sub_task : undefined,
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined
          });
          break;
        case "tool_result":
          handlers.onToolResult?.({
            tool: (data.tool as string) ?? "tool",
            success: Boolean(data.success),
            content: (data.content as string) ?? "",
            data: (data.data as Record<string, unknown>) ?? undefined,
            round: typeof data.round === "number" ? (data.round as number) : undefined,
            sub_task: typeof data.sub_task === "string" ? data.sub_task : undefined,
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined,
            elapsed_ms: typeof data.elapsed_ms === "number" ? (data.elapsed_ms as number) : undefined
          });
          break;
        case "final_answer":
          handlers.onFinalAnswer?.((data.text as string) ?? (data.content as string) ?? "");
          break;
        case "answer_delta":
          handlers.onAnswerDelta?.((data.text as string) ?? "");
          break;
        case "answer_discard":
          handlers.onAnswerDiscard?.();
          break;
        case "agent_speak":
          handlers.onAgentSpeak?.({
            agent: (data.agent as string) ?? "",
            status: (data.status as string) ?? "",
            error: (data.error as string) ?? undefined
          });
          break;
        case "chart":
          handlers.onChart?.({
            chart_type: (data.chart_type as string) ?? undefined,
            chart_config: (data.chart_config as Record<string, unknown>) ?? undefined,
            chart_spec: (data.chart_spec as Record<string, unknown>) ?? undefined
          });
          break;
        case "report":
          handlers.onReport?.({
            title: (data.title as string) ?? undefined,
            html: (data.html as string) ?? undefined,
            mode: (data.mode as string) ?? undefined,
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined,
            report_type: (data.report_type as string) ?? undefined,
            report_type_label: (data.report_type_label as string) ?? undefined
          });
          break;
        case "summary":
          handlers.onSummary?.((data.content as string) ?? "");
          break;
        case "usage":
          handlers.onUsage?.({
            prompt_tokens: typeof data.prompt_tokens === "number" ? data.prompt_tokens : undefined,
            completion_tokens:
              typeof data.completion_tokens === "number" ? data.completion_tokens : undefined,
            total_tokens: typeof data.total_tokens === "number" ? data.total_tokens : undefined,
            delta_total: typeof data.delta_total === "number" ? data.delta_total : undefined,
            context_tokens: typeof data.context_tokens === "number" ? data.context_tokens : undefined,
            context_limit: typeof data.context_limit === "number" ? data.context_limit : null,
            context_model: typeof data.context_model === "string" ? data.context_model : undefined,
            context_key: typeof data.context_key === "string" ? data.context_key : undefined,
            context_estimated: typeof data.context_estimated === "boolean" ? data.context_estimated : undefined
          });
          break;
        case "clarify":
          handlers.onClarify?.({
            ...(typeof data.interrupt_id === "string" ? { interrupt_id: data.interrupt_id } : {}),
            question_id: typeof data.question_id === "string" ? data.question_id : undefined,
            prompt: (data.prompt as string) ?? "",
            fields: Array.isArray(data.fields) ? data.fields as HumanQuestionField[] : [],
            status: "pending",
            missing: Array.isArray(data.missing) ? (data.missing as string[]) : [],
            options: (data.options as ClarifyPayload["options"]) ?? {},
            filled: (data.filled as Record<string, string>) ?? {},
            original_question: (data.original_question as string) ?? "",
            report_type: (data.report_type as string) ?? null
          });
          break;
        case "human.answered":
        case "human.cancelled":
          if (typeof data.interrupt_id === "string") handlers.onHumanResolved?.({
            interrupt_id: data.interrupt_id,
            status: evt.event === "human.answered" ? "answered" : "cancelled",
            answer: typeof data.answer === "string" ? data.answer : undefined
          });
          break;
        case "error":
          handlers.onError?.((data.error as string) ?? "Unknown error", {
            preserveInterrupt: data.preserve_interrupt === true,
            errorDetail: data.error_detail as ErrorDetail | undefined
          });
          break;
        case "done":
          await handlers.onDone?.(Number(data.record_id ?? 0));
          break;
      }
    },
    onError: (err) => handlers.onError?.((err as Error)?.message ?? String(err))
  });
}
