import { request } from '@/utils/request'
import { Chat, ChatInfo, ChatRecord, type ReasoningStep } from '@/views/chat/typed'
import { streamSSE } from '@/utils/sse'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

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
   */
  executeStream: (
    payload: { question: string; datasource_id: number; conversation_id?: number },
    handlers: {
      onStep?: (step: ReasoningStep) => void
      onReasoning?: (text: string) => void
      onSql?: (sql: string, chartType: string) => void
      onResult?: (result: { columns: string[]; rows: any[][]; row_count: number }) => void
      onError?: (msg: string) => void
      onDone?: (recordId: number) => void
    },
    signal?: AbortSignal
  ): Promise<void> => {
    return streamSSE({
      url: `${API_BASE_URL}/chat/chat-stream`,
      body: payload,
      signal,
      onEvent: (evt) => {
        switch (evt.event) {
          case 'step':
            handlers.onStep?.(evt.data as ReasoningStep)
            break
          case 'reasoning':
            handlers.onReasoning?.(evt.data?.text || '')
            break
          case 'sql':
            handlers.onSql?.(evt.data?.sql || '', evt.data?.chart_type || 'table')
            break
          case 'result':
            handlers.onResult?.(evt.data)
            break
          case 'error':
            handlers.onError?.(evt.data?.error || 'Unknown error')
            break
          case 'done':
            handlers.onDone?.(evt.data?.record_id || 0)
            break
        }
      },
      onError: (err) => handlers.onError?.((err && err.message) || String(err)),
    })
  },
}
