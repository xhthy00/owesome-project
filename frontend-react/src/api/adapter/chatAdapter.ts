import { getApiBaseUrl, apiRequest } from "@/api/client";
import { streamSSE } from "@/utils/sse";

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
  create_time?: string;
  update_time?: string;
};

export type ConversationRecord = {
  id: number;
  question: string;
  reasoning?: string;
  summary?: string;
  steps?: Array<{ name?: string; label?: string; status?: string; elapsed_ms?: number; detail?: string; sub_task_index?: number }>;
  tool_calls?: Array<{
    round?: number;
    tool?: string;
    success?: boolean;
    content?: string;
    elapsed_ms?: number;
    sub_task_index?: number;
  }>;
  plan_states?: Array<{
    index: number;
    sub_task?: string;
    sub_task_agent?: string;
    state: "running" | "ok" | "error";
    error?: string;
    row_count?: number;
  }>;
  plans?: string[];
  sub_task_agents?: string[];
};

export type ConversationDetail = Conversation & {
  records: ConversationRecord[];
};

export type ChatStreamPayload = {
  question: string;
  datasource_id: number;
  conversation_id?: number;
  agent_mode?: "agent" | "team" | "legacy";
  enable_tool_agent?: boolean;
};

type StreamHandlers = {
  onStep?: (step: { name?: string; label?: string; status?: string; elapsed_ms?: number; detail?: string }) => void;
  onAgentSpeak?: (payload: { agent?: string; status?: string; error?: string }) => void;
  onChart?: (payload: { chart_type?: string; chart_config?: Record<string, unknown> }) => void;
  onReport?: (payload: { title?: string; html?: string; mode?: string; sub_task_index?: number }) => void;
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
  onSql?: (sql: string, chartType: string) => void;
  onResult?: (result: { columns: string[]; rows: unknown[][]; row_count: number }) => void;
  onAgentThought?: (payload: { text: string; sub_task_index?: number }) => void;
  onToolCall?: (payload: { tool: string; args?: Record<string, unknown>; round?: number; sub_task_index?: number }) => void;
  onToolResult?: (payload: {
    tool: string;
    success: boolean;
    content?: string;
    round?: number;
    sub_task_index?: number;
    elapsed_ms?: number;
  }) => void;
  onFinalAnswer?: (content: string) => void;
  onSummary?: (content: string) => void;
  onError?: (msg: string) => void;
  onDone?: (recordId: number) => void;
};

export async function createConversation(payload: { title?: string; datasource_id: number }) {
  return apiRequest<Conversation>("/chat/conversations", {
    method: "POST",
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

export async function sendMessageStream(
  payload: ChatStreamPayload,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  return streamSSE({
    url: `${getApiBaseUrl()}/chat/chat-stream`,
    body: payload,
    signal,
    onEvent: (evt) => {
      const data = (evt.data ?? {}) as Record<string, unknown>;
      switch (evt.event) {
        case "step":
          handlers.onStep?.(data as { name?: string; label?: string; status?: string; elapsed_ms?: number; detail?: string });
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
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined
          });
          break;
        case "tool_result":
          handlers.onToolResult?.({
            tool: (data.tool as string) ?? "tool",
            success: Boolean(data.success),
            content: (data.content as string) ?? "",
            round: typeof data.round === "number" ? (data.round as number) : undefined,
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined,
            elapsed_ms: typeof data.elapsed_ms === "number" ? (data.elapsed_ms as number) : undefined
          });
          break;
        case "final_answer":
          handlers.onFinalAnswer?.((data.text as string) ?? (data.content as string) ?? "");
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
            chart_config: (data.chart_config as Record<string, unknown>) ?? undefined
          });
          break;
        case "report":
          handlers.onReport?.({
            title: (data.title as string) ?? undefined,
            html: (data.html as string) ?? undefined,
            mode: (data.mode as string) ?? undefined,
            sub_task_index: typeof data.sub_task_index === "number" ? (data.sub_task_index as number) : undefined
          });
          break;
        case "summary":
          handlers.onSummary?.((data.content as string) ?? "");
          break;
        case "error":
          handlers.onError?.((data.error as string) ?? "Unknown error");
          break;
        case "done":
          handlers.onDone?.(Number(data.record_id ?? 0));
          break;
      }
    },
    onError: (err) => handlers.onError?.((err as Error)?.message ?? String(err))
  });
}
