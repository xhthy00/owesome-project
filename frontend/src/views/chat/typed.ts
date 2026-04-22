import { getDate } from '@/utils/utils'

export interface ExecResult {
  columns?: string[]
  rows?: any[][]
  row_count?: number
}

export interface ReasoningStep {
  name: string
  label: string
  status: 'ok' | 'error' | string
  elapsed_ms: number
  detail?: string
}

export class ChatRecord {
  id?: number
  conversation_id?: number
  user_id?: number
  question?: string
  sql?: string
  sql_answer?: string
  sql_error?: string
  exec_result?: ExecResult | null
  chart_type?: string = 'table'
  is_success?: boolean = true
  finish_time?: Date | string
  create_time?: Date | string
  reasoning?: string = ''
  steps?: ReasoningStep[] = []

  constructor(data?: any) {
    if (!data) return
    this.id = data.id
    this.conversation_id = data.conversation_id
    this.user_id = data.user_id
    this.question = data.question
    this.sql = data.sql
    this.sql_answer = data.sql_answer
    this.sql_error = data.sql_error
    this.exec_result = data.exec_result || null
    this.chart_type = data.chart_type || 'table'
    this.is_success = data.is_success !== false
    this.finish_time = getDate(data.finish_time)
    this.create_time = getDate(data.create_time)
    this.reasoning = data.reasoning || ''
    this.steps = Array.isArray(data.steps) ? data.steps : []
  }
}

export class Chat {
  id?: number
  user_id?: number
  title?: string
  datasource_id?: number
  datasource_name?: string
  db_type?: string
  create_time?: Date | string
  update_time?: Date | string

  constructor(data?: any) {
    if (!data) return
    this.id = data.id
    this.user_id = data.user_id
    this.title = data.title
    this.datasource_id = data.datasource_id
    this.datasource_name = data.datasource_name || ''
    this.db_type = data.db_type || ''
    this.create_time = getDate(data.create_time)
    this.update_time = getDate(data.update_time)
  }
}

export class ChatInfo extends Chat {
  records: ChatRecord[] = []

  constructor(data?: any) {
    super(data)
    if (data?.records) {
      this.records = data.records.map((r: any) => new ChatRecord(r))
    }
  }
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  question?: string
  record?: ChatRecord
  pending?: boolean
  error?: string
  create_time?: Date | string
}
