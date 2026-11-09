import { request } from '@/utils/request'
import {
  Chat,
  ChatInfo,
  ChatRecord,
  type AgentMode,
  type AgentSpeak,
  type ChartConfig,
  type PlanState,
  type ReportPayload,
  type ReasoningStep,
  type ToolCallRecord,
} from '@/views/chat/typed'
import { streamSSE } from '@/utils/sse'
import { useCache } from '@/utils/useCache'
import { humanQuestionText } from '@/utils/humanQuestions'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
const pendingInterrupts = new Map<number, string>()

export interface ChatListResult {
  total: number
  items: Chat[]
}

export interface ChatExecuteResult {
  record_id: number
  sql: string
  result: {
    columns: string[]
    rows: any[][]
    row_count: number
  } | null
  error: string
  chart_type: string
  reasoning?: string
  steps?: ReasoningStep[]
}

export const chatApi = {
  list: async (limit = 50): Promise<ChatListResult> => {
    const data: any = await request.get('/chat/conversations', { params: { limit } })
    return {
      total: data.total,
      items: (data.items || []).map((it: any) => new Chat(it)),
    }
  },
  get: async (id: number): Promise<ChatInfo> => {
    const data: any = await request.get(`/chat/conversations/${id}`)
    const interrupt = data.records?.[data.records.length - 1]?.exec_result?.interrupt_id
    if (typeof interrupt === 'string') pendingInterrupts.set(id, interrupt)
    else pendingInterrupts.delete(id)
    return new ChatInfo(data)
  },
  create: async (payload: { title?: string; datasource_id?: number }): Promise<Chat> => {
    const data: any = await request.post('/chat/conversations', payload)
    return new Chat(data)
  },
  rename: async (id: number, title: string): Promise<Chat> => {
    const data: any = await request.put(`/chat/conversations/${id}`, { title })
    return new Chat(data)
  },
  delete: (id: number): Promise<void> => request.delete(`/chat/conversations/${id}`),
  execute: async (payload: {
    question: string
    datasource_id: number
    conversation_id?: number
  }): Promise<ChatRecord> => {
    const data: ChatExecuteResult = await request.post('/chat/execute-sql', payload)
    return new ChatRecord({
      id: data.record_id,
      question: payload.question,
      sql: data.sql,
      sql_error: data.error,
      exec_result: data.result,
      chart_type: data.chart_type,
      is_success: !data.error,
      conversation_id: payload.conversation_id,
      reasoning: data.reasoning || '',
      steps: data.steps || [],
    })
  },
  recentQuestions: (datasource_id: number, limit = 10): Promise<{ questions: string[] }> =>
    request.get(`/chat/recent-questions/${datasource_id}`, { params: { limit } }),

  /**
   * Send a question via SSE so the UI can render thinking steps in real-time.
   * The provided callbacks are invoked as events arrive from the backend.
   *
   * 事件契约（与后端 `/chat/chat-stream` 对齐）：
   * - 兼容层（agent/team/legacy 通用）：step / reasoning / sql / result / error / done
   * - Agent 模式扩展：tool_call / tool_result / agent_thought / final_answer
   * - Team 模式扩展：plan / plan_update / agent_speak / chart / summary / report
   *
   * 客户端按"存在即调用"处理——若后端未发某事件，对应 handler 不会触发。
   */
  executeStream: (
    payload: {
      question: string
      datasource_id: number
      conversation_id?: number
      agent_mode?: AgentMode
      resume_interrupt_id?: string
      enable_tool_agent?: boolean
    },
    handlers: {
      onStep?: (step: ReasoningStep) => void
      onReasoning?: (text: string) => void
      onSql?: (sql: string, chartType: string) => void
      onResult?: (result: { columns: string[]; rows: any[][]; row_count: number }) => void
      onError?: (msg: string) => void
      onDone?: (recordId: number) => void | Promise<void>
      // Agent / Team 共享：DataAnalyst ReAct 循环
      onToolCall?: (payload: ToolCallRecord & { agent?: string }) => void
      onToolResult?: (payload: ToolCallRecord & { agent?: string }) => void
      onAgentThought?: (payload: {
        agent?: string
        round?: number
        thought?: string
        sub_task_index?: number
      }) => void
      onFinalAnswer?: (payload: { agent?: string; content?: string; sub_task_index?: number }) => void
      // Team 模式
      onPlan?: (payload: { plans: string[]; sub_task_agents?: string[] }) => void
      onPlanUpdate?: (state: PlanState) => void
      onAgentSpeak?: (speak: AgentSpeak) => void
      onChart?: (payload: { chart_type: string; chart_config?: ChartConfig }) => void
      onSummary?: (content: string) => void
      onReport?: (payload: ReportPayload) => void
    },
    signal?: AbortSignal
  ): Promise<void> => {
    const token = useCache().wsCache.get('user.token')
    let answer = ''
    let nextInterrupt: string | undefined
    let clarificationText: string | undefined
    let preserveInterrupt = false
    return streamSSE({
      url: `${API_BASE_URL}/chat/chat-stream`,
      body: { ...payload, resume_interrupt_id: payload.resume_interrupt_id ??
        (payload.conversation_id ? pendingInterrupts.get(payload.conversation_id) : undefined) },
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      requireDoneEvent: true,
      signal,
      onEvent: async (evt) => {
        if (signal?.aborted) return
        const data = evt.data || {}
        switch (evt.event) {
          // --------------------- 兼容层 ---------------------
          case 'step':
            handlers.onStep?.(data as ReasoningStep)
            break
          case 'reasoning':
          case 'reasoning_delta':
            handlers.onReasoning?.(data?.text || '')
            break
          case 'sql':
            handlers.onSql?.(data?.sql || '', data?.chart_type || 'table')
            break
          case 'result':
            handlers.onResult?.(data)
            break
          case 'error':
            preserveInterrupt = data?.preserve_interrupt === true
            handlers.onError?.(data?.error || 'Unknown error')
            break
          case 'human.answered':
          case 'human.cancelled':
            if (payload.conversation_id && pendingInterrupts.get(payload.conversation_id) === data?.interrupt_id) {
              pendingInterrupts.delete(payload.conversation_id)
            }
            break
          case 'done':
            if (payload.conversation_id && !nextInterrupt && !preserveInterrupt) pendingInterrupts.delete(payload.conversation_id)
            await handlers.onDone?.(data?.record_id || 0)
            break
          // --------------------- Agent / Team ---------------------
          case 'tool_call':
            handlers.onToolCall?.(data)
            break
          case 'tool_result':
            handlers.onToolResult?.(data)
            break
          case 'agent_thought':
            handlers.onAgentThought?.(data)
            break
          case 'final_answer':
            handlers.onFinalAnswer?.(data)
            break
          // --------------------- Team 专属 ---------------------
          case 'plan':
            handlers.onPlan?.({
              plans: Array.isArray(data?.plans) ? data.plans : [],
              sub_task_agents: Array.isArray(data?.sub_task_agents) ? data.sub_task_agents : undefined,
            })
            break
          case 'plan_update':
            handlers.onPlanUpdate?.(data as PlanState)
            break
          case 'agent_speak':
            handlers.onAgentSpeak?.(data as AgentSpeak)
            break
          case 'chart':
            handlers.onChart?.({
              chart_type: data?.chart_type || 'table',
              chart_config: data?.chart_config,
            })
            break
          case 'summary':
            handlers.onSummary?.(clarificationText ?? data?.content ?? '')
            break
          case 'answer_delta':
            answer += data?.text || ''
            handlers.onSummary?.(answer)
            break
          case 'answer_discard':
            answer = ''
            handlers.onSummary?.('')
            break
          case 'clarify':
            nextInterrupt = data?.interrupt_id
            if (payload.conversation_id && nextInterrupt) pendingInterrupts.set(payload.conversation_id, nextInterrupt)
            clarificationText = humanQuestionText(data)
            handlers.onSummary?.(clarificationText)
            break
          case 'task_plan': {
            const stages = Array.isArray(data?.stages) ? data.stages : []
            handlers.onPlan?.({ plans: stages.map((stage: any) => stage.content) })
            stages.forEach((stage: any, index: number) => handlers.onPlanUpdate?.({
              index, sub_task: stage.content,
              state: stage.status === 'completed' ? 'ok' : stage.status === 'failed' ? 'error' : 'running',
            }))
            break
          }
          case 'report':
            handlers.onReport?.({
              title: data?.title || 'Report',
              html: data?.html || '',
              mode: data?.mode,
              sub_task_index: data?.sub_task_index,
              agent: data?.agent,
            })
            break
        }
      },
      onError: (err) => handlers.onError?.(err instanceof Error ? err.message : String(err)),
    })
  },
}
