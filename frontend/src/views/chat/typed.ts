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
  sub_task_index?: number
}

/** 历史会话记录可能保存的 Agent 模式。 */
export type AgentMode = 'agent' | 'team' | 'legacy'

/** Team 模式下 Planner 拆出的每个 sub_task 的执行状态。 */
export interface PlanState {
  index: number
  sub_task?: string
  sub_task_agent?: string
  state: 'running' | 'ok' | 'error'
  error?: string
  sql?: string
  row_count?: number
}

/** Charter 推荐的图表配置——x 是类别轴字段名，y 是指标字段名数组。 */
export interface ChartConfig {
  x?: string
  y?: string[]
  title?: string
}

/** DataAnalyst ReAct 循环里的一次工具调用记录，tool_call 和 tool_result 会被合并成这一条。 */
export interface ToolCallRecord {
  round: number
  tool: string
  args?: Record<string, any>
  thought?: string
  sub_task_index?: number
  success?: boolean
  content?: string
  data?: any
  elapsed_ms?: number
}

/** Team 模式各 Agent 的发言事件（仅作状态流展示，不落库）。 */
export interface AgentSpeak {
  agent: string
  status: 'start' | 'end' | 'error'
  timestamp?: number
  sub_task_index?: number
  extra?: Record<string, any>
}

export interface ReportPayload {
  title: string
  html: string
  mode?: string
  sub_task_index?: number
  agent?: string
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
  chart_config?: ChartConfig
  is_success?: boolean = true
  finish_time?: Date | string
  create_time?: Date | string
  reasoning?: string = ''
  steps?: ReasoningStep[] = []
  /** 本条历史记录保存的 Agent 模式。 */
  agent_mode?: AgentMode
  plans?: string[]
  sub_task_agents?: string[]
  plan_states?: PlanState[]
  /** Team 模式 Summarizer 的最终结论；优先级高于 reasoning。 */
  summary?: string
  /** ReAct 循环里按 (round, sub_task_index) 合并的工具调用记录。 */
  tool_calls?: ToolCallRecord[]
  reports?: ReportPayload[]

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
    this.chart_config = data.chart_config || undefined
    this.is_success = data.is_success !== false
    this.finish_time = getDate(data.finish_time)
    this.create_time = getDate(data.create_time)
    this.reasoning = data.reasoning || ''
    this.steps = Array.isArray(data.steps) ? data.steps : []
    this.agent_mode = data.agent_mode
    this.plans = Array.isArray(data.plans) ? data.plans : undefined
    this.sub_task_agents = Array.isArray(data.sub_task_agents) ? data.sub_task_agents : undefined
    this.plan_states = Array.isArray(data.plan_states) ? data.plan_states : undefined
    this.summary = data.summary || undefined
    this.tool_calls = Array.isArray(data.tool_calls) ? data.tool_calls : undefined
    this.reports = Array.isArray(data.reports) ? data.reports : this._deriveReportsFromToolCalls(this.tool_calls)
  }

  private _deriveReportsFromToolCalls(toolCalls?: ToolCallRecord[]): ReportPayload[] | undefined {
    if (!Array.isArray(toolCalls) || !toolCalls.length) return undefined
    const reports: ReportPayload[] = []
    for (const call of toolCalls) {
      const data = call.data
      if (!data || typeof data !== 'object') continue
      if (data.output_type === 'html' && typeof data.html === 'string' && data.html.trim()) {
        reports.push({
          title: typeof data.title === 'string' && data.title.trim() ? data.title : 'Report',
          html: data.html,
          mode: typeof data.mode === 'string' ? data.mode : undefined,
          sub_task_index: call.sub_task_index,
        })
        continue
      }
      if (Array.isArray((data as any).chunks)) {
        for (const chunk of (data as any).chunks) {
          if (chunk?.output_type !== 'html') continue
          if (typeof chunk?.content !== 'string' || !chunk.content.trim()) continue
          reports.push({
            title: typeof chunk?.title === 'string' && chunk.title.trim() ? chunk.title : 'Report',
            html: chunk.content,
            mode: typeof data.mode === 'string' ? data.mode : undefined,
            sub_task_index: call.sub_task_index,
          })
        }
      }
    }
    return reports.length ? reports : undefined
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
