<script lang="ts" setup>
import { ref, computed } from 'vue'
import {
  ChatDotRound,
  User,
  Loading,
  ArrowDown,
  CircleCheckFilled,
  CircleCloseFilled,
  Grid,
  Histogram,
  TrendCharts,
  PieChart,
  DataAnalysis,
  View,
  Hide,
  Tools,
  Reading,
  CopyDocument,
  Refresh,
  Search,
  Document,
  EditPen,
  Monitor,
  QuestionFilled,
  Connection,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { ChatMessage, ChartConfig, ChatRecord, ExecResult, ToolCallRecord } from './typed'
import type { ChartAxis, ChartTypes } from './component/BaseChart'
import ChartComponent from './component/ChartComponent.vue'
import ReportPreview from './component/ReportPreview.vue'
import MarkdownContent from '@/components/markdown/MarkdownContent.vue'
import { datetimeFormat } from '@/utils/utils'

const props = defineProps<{ messages: ChatMessage[] }>()
const emit = defineEmits<{
  retry: [question: string]
}>()

const PAGE_SIZE = 20

const pageMap = ref<Record<number, number>>({})
const expandThinking = ref<Record<number, boolean>>({})
const expandPlans = ref<Record<number, boolean>>({})
const expandTools = ref<Record<number, boolean>>({})
const expandToolCalls = ref<Record<string, boolean>>({})
const chartTypeMap = ref<Record<number, ChartTypes>>({})
const showLabelMap = ref<Record<number, boolean>>({})
const resultPanelTabMap = ref<Record<number, 'chart' | 'sql' | 'data'>>({})
const queryIndexMap = ref<Record<number, number>>({})

interface QuerySnapshot {
  key: string
  label: string
  sql: string
  exec_result: ExecResult
  round?: number
  sub_task_index?: number
}

const queryDataName = (msg: ChatMessage, query: QuerySnapshot, idx: number, total: number): string => {
  const isFinal = idx === total - 1
  const cfg = msg.record?.chart_config
  if (isFinal && cfg?.y?.length) {
    return cfg.y.slice(0, 3).join(' / ')
  }
  const cols = query.exec_result?.columns || []
  if (cols.length >= 2) {
    // 默认图表通常把第 1 列当维度，后续列是指标。
    return cols.slice(1, 4).join(' / ')
  }
  if (cols.length === 1) return cols[0]
  return isFinal ? '最终查询' : `查询 #${idx + 1}`
}

const CHART_OPTIONS: { value: ChartTypes; label: string; icon: any }[] = [
  { value: 'table', label: 'chat.chart_type.table', icon: Grid },
  { value: 'column', label: 'chat.chart_type.column', icon: DataAnalysis },
  { value: 'bar', label: 'chat.chart_type.bar', icon: Histogram },
  { value: 'line', label: 'chat.chart_type.line', icon: TrendCharts },
  { value: 'pie', label: 'chat.chart_type.pie', icon: PieChart },
]

const getChartType = (i: number, fallback?: string): ChartTypes => {
  if (chartTypeMap.value[i]) return chartTypeMap.value[i]
  const t = (fallback || 'table') as ChartTypes
  return ['table', 'column', 'bar', 'line', 'pie'].includes(t) ? t : 'table'
}
const setChartType = (i: number, t: ChartTypes) => (chartTypeMap.value[i] = t)
const getShowLabel = (i: number) => !!showLabelMap.value[i]
const toggleShowLabel = (i: number) => (showLabelMap.value[i] = !showLabelMap.value[i])

const getPage = (i: number) => pageMap.value[i] || 1
const setPage = (i: number, p: number) => (pageMap.value[i] = p)
const getResultPanelTab = (i: number, msg: ChatMessage): 'chart' | 'sql' | 'data' => {
  if (resultPanelTabMap.value[i]) return resultPanelTabMap.value[i]
  const query = activeQuery(i, msg)
  const hasChart = getChartType(i, msg.record?.chart_type) !== 'table' && !!query?.exec_result
  const hasData = !!query?.exec_result
  resultPanelTabMap.value[i] = hasChart ? 'chart' : hasData ? 'data' : 'sql'
  return resultPanelTabMap.value[i]
}
const setResultPanelTab = (i: number, tab: 'chart' | 'sql' | 'data') => {
  resultPanelTabMap.value[i] = tab
}

const querySnapshots = (msg: ChatMessage): QuerySnapshot[] => {
  const record = msg.record
  if (!record) return []
  const out: QuerySnapshot[] = []
  const seen = new Set<string>()
  const toolCalls = Array.isArray(record.tool_calls) ? record.tool_calls : []
  for (const call of toolCalls) {
    if (call.tool !== 'execute_sql' || call.success !== true) continue
    const data = call.data
    if (!data || typeof data !== 'object') continue
    const sql = typeof data.sql === 'string' ? data.sql : ''
    const columns = Array.isArray(data.columns) ? data.columns : []
    const rows = Array.isArray(data.rows) ? data.rows : []
    if (!sql || !columns.length) continue
    const rowCount = typeof data.row_count === 'number' ? data.row_count : rows.length
    const key = `${call.sub_task_index ?? -1}-${call.round ?? -1}-${out.length}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({
      key,
      label: '',
      sql,
      exec_result: { columns, rows, row_count: rowCount },
      round: call.round,
      sub_task_index: call.sub_task_index,
    })
  }
  if (record.sql && record.exec_result) {
    const fallbackKey = `${record.sql}|${record.exec_result.row_count ?? record.exec_result.rows?.length ?? 0}`
    const exists = out.some(
      (it) =>
        it.sql === record.sql &&
        (it.exec_result.row_count ?? it.exec_result.rows?.length ?? 0) ===
          (record.exec_result?.row_count ?? record.exec_result?.rows?.length ?? 0)
    )
    if (!exists && !seen.has(fallbackKey)) {
      out.push({
        key: fallbackKey,
        label: '',
        sql: record.sql,
        exec_result: record.exec_result,
      })
    }
  }
  return out.map((item, idx) => ({
    ...item,
    label: queryDataName(msg, item, idx, out.length),
  }))
}

const getQueryIndex = (i: number, msg: ChatMessage): number => {
  const snapshots = querySnapshots(msg)
  if (!snapshots.length) return 0
  const maxIdx = snapshots.length - 1
  const current = queryIndexMap.value[i]
  if (typeof current !== 'number' || current < 0 || current > maxIdx) {
    queryIndexMap.value[i] = maxIdx
    return maxIdx
  }
  return current
}

const setQueryIndex = (i: number, idx: number, msg: ChatMessage) => {
  const snapshots = querySnapshots(msg)
  if (!snapshots.length) {
    queryIndexMap.value[i] = 0
    return
  }
  queryIndexMap.value[i] = Math.max(0, Math.min(idx, snapshots.length - 1))
}

const activeQuery = (i: number, msg: ChatMessage): QuerySnapshot | undefined => {
  const snapshots = querySnapshots(msg)
  if (!snapshots.length) return undefined
  return snapshots[getQueryIndex(i, msg)]
}

const isThinkingExpanded = (i: number, pending = false): boolean => {
  if (i in expandThinking.value) return expandThinking.value[i]
  return pending
}
const toggleThinking = (i: number, pending = false) =>
  (expandThinking.value[i] = !isThinkingExpanded(i, pending))

const totalElapsedMs = (steps: any[] | undefined) =>
  (steps || []).reduce((sum, s) => sum + (s?.elapsed_ms || 0), 0)

const formatElapsed = (ms: number) =>
  ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`

const hasThinking = (msg: any) =>
  !!(msg.record && ((msg.record.steps && msg.record.steps.length) || msg.record.reasoning))

const hasPlans = (msg: any): boolean => {
  const r: ChatRecord | undefined = msg.record
  return !!(r && r.plans && r.plans.length > 0)
}

const hasToolCalls = (msg: any): boolean => {
  const r: ChatRecord | undefined = msg.record
  return !!(r && r.tool_calls && r.tool_calls.length > 0)
}

const hasSummary = (msg: any): boolean => !!msg?.record?.summary
const hasReports = (msg: any): boolean => !!(msg?.record?.reports && msg.record.reports.length)
const hasSqlAnswer = (msg: any): boolean => !!msg?.record?.sql_answer

const groupedReports = (msg: any): Array<{ sub_task_index?: number; items: any[] }> => {
  const reports = msg?.record?.reports || []
  if (!Array.isArray(reports) || !reports.length) return []
  const groups = new Map<number | '__none__', any[]>()
  for (const r of reports) {
    const k: number | '__none__' =
      typeof r?.sub_task_index === 'number' ? r.sub_task_index : '__none__'
    const list = groups.get(k) || []
    list.push(r)
    groups.set(k, list)
  }
  return Array.from(groups.entries()).map(([k, list]) => ({
    sub_task_index: k === '__none__' ? undefined : k,
    items: list,
  }))
}

const isPlanExpanded = (i: number, pending = false): boolean => {
  if (i in expandPlans.value) return expandPlans.value[i]
  return pending
}
const togglePlan = (i: number, pending = false) =>
  (expandPlans.value[i] = !isPlanExpanded(i, pending))

const isToolsExpanded = (i: number, pending = false): boolean => {
  if (i in expandTools.value) return !!expandTools.value[i]
  return pending
}
const toggleTools = (i: number, pending = false) =>
  (expandTools.value[i] = !isToolsExpanded(i, pending))

const groupedToolCalls = (calls: ToolCallRecord[] | undefined) => {
  if (!calls || !calls.length) return []
  const groups = new Map<number | '__none__', ToolCallRecord[]>()
  for (const c of calls) {
    const k: number | '__none__' =
      typeof c.sub_task_index === 'number' ? c.sub_task_index : '__none__'
    const list = groups.get(k) || []
    list.push(c)
    groups.set(k, list)
  }
  return Array.from(groups.entries()).map(([k, list]) => ({
    sub_task_index: k === '__none__' ? undefined : k,
    items: list.sort((a, b) => a.round - b.round),
  }))
}

const chartConfigToAxes = (cfg: ChartConfig | undefined): ChartAxis[] | undefined => {
  if (!cfg) return undefined
  const axes: ChartAxis[] = []
  if (cfg.x) axes.push({ name: cfg.x, value: cfg.x, type: 'x' })
  const ys = Array.isArray(cfg.y) ? cfg.y.filter(Boolean) : []
  ys.forEach((y) =>
    axes.push({ name: y, value: y, type: 'y', 'multi-quota': ys.length > 1 })
  )
  return axes.length ? axes : undefined
}

const planStateIcon = (state: string) => {
  if (state === 'ok') return CircleCheckFilled
  if (state === 'error') return CircleCloseFilled
  return Loading
}

const planAgentLabel = (agent?: string): string => {
  if (agent === 'ToolExpert') return 'ToolExpert'
  if (agent === 'DataAnalyst') return 'DataAnalyst'
  return ''
}

const truncate = (s: string | undefined, n: number): string => {
  if (!s) return ''
  return s.length <= n ? s : s.slice(0, n) + '…'
}

const formatArgs = (args: Record<string, any> | undefined): string => {
  if (!args || typeof args !== 'object' || !Object.keys(args).length) return ''
  try {
    const s = JSON.stringify(args)
    return truncate(s, 200)
  } catch {
    return ''
  }
}

const formatToolName = (tool: string): string =>
  tool
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')

const toolCardKey = (msgIndex: number, groupIndex: number, callIndex: number): string =>
  `${msgIndex}-${groupIndex}-${callIndex}`

const isToolCardExpanded = (
  msgIndex: number,
  groupIndex: number,
  callIndex: number,
  c: ToolCallRecord,
  pending?: boolean
): boolean => {
  const key = toolCardKey(msgIndex, groupIndex, callIndex)
  if (key in expandToolCalls.value) return !!expandToolCalls.value[key]
  return toolStatus(c, pending) !== 'ok'
}

const toggleToolCard = (
  msgIndex: number,
  groupIndex: number,
  callIndex: number,
  c: ToolCallRecord,
  pending?: boolean
) => {
  const key = toolCardKey(msgIndex, groupIndex, callIndex)
  expandToolCalls.value[key] = !isToolCardExpanded(msgIndex, groupIndex, callIndex, c, pending)
}

const toolStatus = (c: ToolCallRecord, pending?: boolean): 'running' | 'error' | 'ok' => {
  if (c.success === false) return 'error'
  if (c.success === true) return 'ok'
  if (pending) return 'running'
  // 历史消息如果有耗时、输出或数据，默认视为已完成，避免全部显示 Running。
  if ((c.elapsed_ms || 0) > 0 || !!c.content || c.data != null) return 'ok'
  return 'running'
}

const toolStatusText = (c: ToolCallRecord, pending?: boolean): string => {
  const s = toolStatus(c, pending)
  if (s === 'error') return 'Error'
  if (s === 'ok') return 'Done'
  return 'Running'
}

const toolStatusClass = (c: ToolCallRecord, pending?: boolean): string => {
  const s = toolStatus(c, pending)
  if (s === 'error') return 'error'
  if (s === 'ok') return 'ok'
  return 'running'
}

const toolIconFor = (tool: string): any => {
  const t = (tool || '').toLowerCase()
  if (t.includes('read') || t.includes('file') || t.includes('write')) return Document
  if (t.includes('search') || t.includes('grep') || t.includes('glob')) return Search
  if (t.includes('edit') || t.includes('patch')) return EditPen
  if (t.includes('bash') || t.includes('shell') || t.includes('command')) return Monitor
  if (t.includes('task') || t.includes('delegate')) return Connection
  if (t.includes('question') || t.includes('ask')) return QuestionFilled
  return Tools
}

const toolSubtitle = (c: ToolCallRecord): string => {
  const args = c.args || {}
  const preferred = ['file_path', 'path', 'query', 'pattern', 'command', 'sql', 'url']
  for (const k of preferred) {
    const v = args[k]
    if (typeof v === 'string' && v.trim()) return truncate(v.trim(), 64)
  }
  const keys = Object.keys(args)
  if (!keys.length) return ''
  return truncate(
    keys
      .slice(0, 2)
      .map((k) => `${k}: ${String(args[k])}`)
      .join(' · '),
    64
  )
}

const slicedRows = (i: number, rows: any[][] | undefined) => {
  if (!rows) return []
  const p = getPage(i)
  const start = (p - 1) * PAGE_SIZE
  return rows.slice(start, start + PAGE_SIZE)
}

const dotInterval = ref(0)
setInterval(() => (dotInterval.value = (dotInterval.value + 1) % 4), 500)
const dots = computed(() => '.'.repeat(dotInterval.value))

const onCopy = async (msg: ChatMessage) => {
  const text =
    msg.record?.summary ||
    msg.record?.sql_answer ||
    msg.record?.sql ||
    msg.question ||
    ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

const canRetry = (i: number, msg: ChatMessage): boolean => {
  if (msg.pending || msg.role !== 'assistant') return false
  return !!props.messages[i - 1]?.question
}

const onRetry = (i: number) => {
  const q = props.messages[i - 1]?.question
  if (!q) return
  emit('retry', q)
}

void props
</script>

<template>
  <div class="chat-record">
    <template v-for="(msg, i) in messages" :key="i">
      <!-- 用户消息 -->
      <div v-if="msg.role === 'user'" class="turn user-turn">
        <div class="turn-body">
          <div class="bubble">{{ msg.question }}</div>
          <div class="meta">{{ datetimeFormat(msg.create_time) }}</div>
        </div>
        <div class="avatar user-avatar">
          <el-icon :size="16"><User /></el-icon>
        </div>
      </div>

      <!-- 助手消息 -->
      <div v-else class="turn assistant-turn">
        <div class="avatar ai-avatar">
          <el-icon :size="16"><ChatDotRound /></el-icon>
        </div>
        <div class="turn-body">
          <div class="agent-name">
            <span>智能问数助手</span>
            <span v-if="msg.record?.agent_mode" class="mode-tag">
              {{ msg.record.agent_mode === 'team' ? '团队协作' : '单体分析' }}
            </span>
          </div>

          <div class="assistant-card">
            <div class="assistant-split">
              <section class="thinking-col">
                <div class="panel-title">执行情况</div>
                <div class="thinking-panel">
                <div v-if="hasPlans(msg)" class="block plans-block">
                  <div class="block-header" @click="togglePlan(i, msg.pending)">
                    <el-icon class="caret" :class="{ open: isPlanExpanded(i, msg.pending) }" :size="12">
                      <ArrowDown />
                    </el-icon>
                    <el-icon class="block-icon plan" :size="14"><Reading /></el-icon>
                    <span class="block-title">{{ $t('chat.plan') }}</span>
                    <span class="badge plan-badge">
                      {{ $t('chat.plan_steps', { n: msg.record!.plans!.length }) }}
                    </span>
                  </div>
                  <ol v-show="isPlanExpanded(i, msg.pending)" class="plan-body">
                    <li
                      v-for="(st, si) in msg.record!.plan_states || []"
                      :key="si"
                      class="plan-item"
                      :class="{ error: st.state === 'error' }"
                    >
                      <el-icon
                        :size="14"
                        :class="['plan-icon', st.state]"
                        :style="st.state === 'running' ? 'animation: spin 1s linear infinite' : ''"
                      >
                        <component :is="planStateIcon(st.state)" />
                      </el-icon>
                      <span class="plan-label">{{ st.sub_task || `#${st.index + 1}` }}</span>
                      <span v-if="planAgentLabel(st.sub_task_agent)" class="plan-agent">
                        {{ planAgentLabel(st.sub_task_agent) }}
                      </span>
                      <span v-if="st.state === 'ok' && st.row_count != null" class="muted">
                        {{ $t('chat.rows', { n: st.row_count }) }}
                      </span>
                      <span v-else-if="st.state === 'error' && st.error" class="error-text">
                        {{ truncate(st.error, 60) }}
                      </span>
                    </li>
                  </ol>
                </div>

                <template v-if="msg.pending">
                  <div v-if="hasThinking(msg)" class="block thinking-block streaming">
                    <div class="block-header" @click="toggleThinking(i, true)">
                      <el-icon class="caret" :class="{ open: isThinkingExpanded(i, true) }" :size="12">
                        <ArrowDown />
                      </el-icon>
                      <el-icon class="is-loading streaming-icon" :size="14"><Loading /></el-icon>
                      <span class="block-title">{{ $t('chat.thinking_now') }}{{ dots }}</span>
                      <span class="elapsed">{{ formatElapsed(totalElapsedMs(msg.record!.steps)) }}</span>
                    </div>
                    <div v-show="isThinkingExpanded(i, true)" class="thinking-body">
                      <ol v-if="msg.record!.steps && msg.record!.steps.length" class="steps">
                        <li v-for="(step, si) in msg.record!.steps" :key="si" class="step">
                          <el-icon
                            :size="14"
                            :class="['step-icon', step.status === 'error' ? 'error' : 'ok']"
                          >
                            <CircleCloseFilled v-if="step.status === 'error'" />
                            <CircleCheckFilled v-else />
                          </el-icon>
                          <span class="step-label">{{ step.label }}</span>
                          <span v-if="step.detail" class="step-detail muted">{{ step.detail }}</span>
                          <span class="step-elapsed muted">{{ formatElapsed(step.elapsed_ms || 0) }}</span>
                        </li>
                      </ol>
                      <div v-if="msg.record!.reasoning" class="reasoning">
                        <pre>{{ msg.record!.reasoning }}</pre>
                      </div>
                    </div>
                  </div>
                  <div v-else class="block thinking-block pending">
                    <el-icon class="is-loading icon" :size="14"><Loading /></el-icon>
                    <span class="block-title">{{ $t('chat.thinking_now') }}{{ dots }}</span>
                  </div>
                </template>

                <div v-if="hasToolCalls(msg)" class="block tools-block">
                  <div class="block-header" @click="toggleTools(i, !!msg.pending)">
                    <el-icon class="caret" :class="{ open: isToolsExpanded(i, !!msg.pending) }" :size="12">
                      <ArrowDown />
                    </el-icon>
                    <el-icon class="block-icon tool" :size="14"><Tools /></el-icon>
                    <span class="block-title">{{ $t('chat.tool_calls') }}</span>
                    <span class="badge">
                      {{ $t('chat.tool_calls_count', { n: msg.record!.tool_calls!.length }) }}
                    </span>
                  </div>
                  <div v-show="isToolsExpanded(i, !!msg.pending)" class="tools-body">
                    <div
                      v-for="(grp, gi) in groupedToolCalls(msg.record!.tool_calls)"
                      :key="gi"
                      class="tool-group"
                    >
                      <div v-if="grp.sub_task_index != null" class="tool-group-title">
                        {{ $t('chat.sub_task_label', { n: grp.sub_task_index + 1 }) }}
                        <span v-if="msg.record!.plans && msg.record!.plans[grp.sub_task_index]">
                          · {{ truncate(msg.record!.plans[grp.sub_task_index], 40) }}
                        </span>
                      </div>
                      <div v-for="(c, ci) in grp.items" :key="ci" class="tool-item">
                        <div class="tool-trigger" @click="toggleToolCard(i, gi, ci, c, !!msg.pending)">
                          <div class="tool-trigger-main">
                            <el-icon class="tool-type-icon" :size="14">
                              <component :is="toolIconFor(c.tool)" />
                            </el-icon>
                            <div class="tool-meta">
                              <div class="tool-title-line">
                                <span class="tool-name">{{ formatToolName(c.tool) }}</span>
                                <span v-if="toolSubtitle(c)" class="tool-subtitle">{{ toolSubtitle(c) }}</span>
                              </div>
                              <div v-if="formatArgs(c.args)" class="tool-args muted">
                                {{ formatArgs(c.args) }}
                              </div>
                            </div>
                          </div>
                          <div class="tool-trigger-action">
                            <span class="tool-status" :class="toolStatusClass(c, !!msg.pending)">
                              <span class="status-dot" />
                              <span>{{ toolStatusText(c, !!msg.pending) }}</span>
                            </span>
                            <span v-if="c.elapsed_ms" class="tool-elapsed muted">
                              {{ formatElapsed(c.elapsed_ms) }}
                            </span>
                            <el-icon
                              class="caret small"
                              :class="{ open: isToolCardExpanded(i, gi, ci, c, !!msg.pending) }"
                              :size="11"
                            >
                              <ArrowDown />
                            </el-icon>
                          </div>
                        </div>
                        <div v-show="isToolCardExpanded(i, gi, ci, c, !!msg.pending)" class="tool-detail">
                          <div v-if="c.thought" class="tool-thought">
                            <span class="tag">{{ $t('chat.thought') }}</span> {{ truncate(c.thought, 200) }}
                          </div>
                          <div v-if="c.content" class="tool-content">
                            <span class="tag">{{ $t('chat.observation') }}</span>
                            <pre>{{ truncate(c.content, 320) }}</pre>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                </div>
              </section>

              <section v-if="!msg.pending" class="result-col">
                <div class="panel-title">结果展现</div>
                <div v-if="hasSummary(msg)" class="block summary-block result-card">
                  <div class="block-header static">
                    <el-icon class="block-icon summary" :size="14"><ChatDotRound /></el-icon>
                    <span class="block-title">{{ $t('chat.summary') }}</span>
                  </div>
                  <div class="summary-body">
                    <MarkdownContent :content="msg.record!.summary" />
                  </div>
                </div>

                <div v-if="hasReports(msg)" class="block report-block result-card">
                  <div class="block-header static">
                    <el-icon class="block-icon summary" :size="14"><Reading /></el-icon>
                    <span class="block-title">{{ $t('chat.report') }}</span>
                    <span class="badge">{{ $t('chat.report_count', { n: msg.record!.reports!.length }) }}</span>
                  </div>
                  <div class="report-body">
                    <div
                      v-for="(grp, gi) in groupedReports(msg)"
                      :key="gi"
                      class="report-group"
                    >
                      <div v-if="grp.sub_task_index != null" class="report-group-title">
                        {{ $t('chat.sub_task_label', { n: grp.sub_task_index + 1 }) }}
                      </div>
                      <ReportPreview
                        v-for="(rp, ri) in grp.items"
                        :key="`${gi}-${ri}`"
                        :title="rp.title || `Report ${ri + 1}`"
                        :html="rp.html"
                      />
                    </div>
                  </div>
                </div>

                <div v-if="hasSqlAnswer(msg)" class="answer-text result-text-card result-card">
                  <MarkdownContent :content="msg.record!.sql_answer" />
                </div>

                <div v-if="!msg.pending && msg.error" class="block error-block result-card">
                  <el-alert type="error" :title="$t('chat.execution_failed')" :closable="false">
                    <pre>{{ msg.error }}</pre>
                  </el-alert>
                </div>

                <div
                  v-if="msg.record && (querySnapshots(msg).length || msg.record.sql_error)"
                  class="answer"
                >
                  <div v-if="msg.record.sql_error" class="block error-block result-card">
                    <el-alert type="error" :title="$t('chat.execution_failed')" :closable="false">
                      <pre>{{ msg.record.sql_error }}</pre>
                    </el-alert>
                  </div>

                  <div v-if="querySnapshots(msg).length" class="result-block result-card">
                    <div class="result-switch">
                      <div class="query-picker">
                        <span class="picker-label">{{ $t('chat.query_set') }}</span>
                        <el-select
                          :model-value="getQueryIndex(i, msg)"
                          size="small"
                          class="query-select"
                          @change="(v:number) => setQueryIndex(i, v, msg)"
                        >
                          <el-option
                            v-for="(q, qi) in querySnapshots(msg)"
                            :key="q.key"
                            :label="
                              qi === querySnapshots(msg).length - 1
                                ? `${q.label}（最终）`
                                : q.label
                            "
                            :value="qi"
                          />
                        </el-select>
                      </div>
                      <button
                        class="switch-tab"
                        :class="{ active: getResultPanelTab(i, msg) === 'chart' }"
                        @click="setResultPanelTab(i, 'chart')"
                      >
                        {{ $t('chat.result_tab.chart') }}
                      </button>
                      <button
                        class="switch-tab"
                        :class="{ active: getResultPanelTab(i, msg) === 'data' }"
                        @click="setResultPanelTab(i, 'data')"
                      >
                        {{ $t('chat.result_tab.data') }}
                      </button>
                      <button
                        class="switch-tab"
                        :class="{ active: getResultPanelTab(i, msg) === 'sql' }"
                        @click="setResultPanelTab(i, 'sql')"
                      >
                        {{ $t('chat.result_tab.sql') }}
                      </button>
                    </div>

                    <div
                      v-if="
                        getResultPanelTab(i, msg) === 'chart' &&
                        getChartType(i, msg.record!.chart_type) !== 'table' &&
                        activeQuery(i, msg)?.exec_result
                      "
                      class="result-section"
                    >
                      <div class="result-header">
                        <div class="result-title">{{ $t('chat.result_tab.chart') }}</div>
                        <div class="chart-toolbar">
                          <el-tooltip
                            v-for="opt in CHART_OPTIONS.filter((it) => it.value !== 'table')"
                            :key="opt.value"
                            :content="$t(opt.label)"
                            placement="top"
                            :show-after="300"
                          >
                            <button
                              class="tool-btn"
                              :class="{ active: getChartType(i, msg.record!.chart_type) === opt.value }"
                              @click="setChartType(i, opt.value)"
                            >
                              <el-icon :size="14">
                                <component :is="opt.icon" />
                              </el-icon>
                            </button>
                          </el-tooltip>
                          <span class="divider" />
                          <el-tooltip
                            :content="getShowLabel(i) ? $t('chat.hide_label') : $t('chat.show_label')"
                            placement="top"
                            :show-after="300"
                          >
                            <button
                              class="tool-btn"
                              :class="{ active: getShowLabel(i) }"
                              @click="toggleShowLabel(i)"
                            >
                              <el-icon :size="14">
                                <View v-if="getShowLabel(i)" />
                                <Hide v-else />
                              </el-icon>
                            </button>
                          </el-tooltip>
                        </div>
                      </div>
                      <div class="chart-wrapper">
                        <ChartComponent
                          :id="msg.record!.id || i"
                          :type="
                            getChartType(i, msg.record!.chart_type) as
                              | 'column'
                              | 'bar'
                              | 'line'
                              | 'pie'
                          "
                          :columns="activeQuery(i, msg)?.exec_result?.columns || []"
                          :rows="activeQuery(i, msg)?.exec_result?.rows || []"
                          :show-label="getShowLabel(i)"
                          :axes-override="
                            getQueryIndex(i, msg) === querySnapshots(msg).length - 1
                              ? chartConfigToAxes(msg.record.chart_config)
                              : undefined
                          "
                        />
                      </div>
                    </div>

                    <div
                      v-if="getResultPanelTab(i, msg) === 'data' && activeQuery(i, msg)?.exec_result"
                      class="result-section"
                    >
                      <div class="result-header">
                        <div class="result-title">
                          {{ $t('chat.result_tab.data') }}
                          <span class="muted">
                            {{
                              $t('chat.rows', {
                                n:
                                  activeQuery(i, msg)?.exec_result?.row_count ??
                                  activeQuery(i, msg)?.exec_result?.rows?.length ??
                                  0,
                              })
                            }}
                          </span>
                        </div>
                      </div>
                      <el-table
                        :data="
                          slicedRows(i, activeQuery(i, msg)?.exec_result?.rows)?.map((row) =>
                            Object.fromEntries(
                              (activeQuery(i, msg)?.exec_result?.columns || []).map((c, idx) => [c, row[idx]])
                            )
                          )
                        "
                        stripe
                        size="small"
                        border
                      >
                        <el-table-column
                          v-for="col in activeQuery(i, msg)?.exec_result?.columns || []"
                          :key="col"
                          :prop="col"
                          :label="col"
                        />
                      </el-table>
                      <el-pagination
                        v-if="(activeQuery(i, msg)?.exec_result?.row_count ?? 0) > PAGE_SIZE"
                        :current-page="getPage(i)"
                        :page-size="PAGE_SIZE"
                        :total="activeQuery(i, msg)?.exec_result?.row_count ?? 0"
                        layout="prev, pager, next, total"
                        small
                        class="pagination"
                        @current-change="(p: number) => setPage(i, p)"
                      />
                    </div>

                    <div v-if="getResultPanelTab(i, msg) === 'sql'" class="result-section">
                      <div class="result-header">
                        <div class="result-title">{{ $t('chat.result_tab.sql') }}</div>
                      </div>
                      <div class="sql-block">
                        <pre><code>{{ activeQuery(i, msg)?.sql || '--' }}</code></pre>
                      </div>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </div>

          <div v-if="!msg.pending" class="message-footer">
            <span class="meta">{{ datetimeFormat(msg.create_time) }}</span>
            <div class="actions">
              <button class="action-btn" @click="onCopy(msg)">
                <el-icon :size="13"><CopyDocument /></el-icon>
                <span>复制</span>
              </button>
              <button class="action-btn" :disabled="!canRetry(i, msg)" @click="onRetry(i)">
                <el-icon :size="13"><Refresh /></el-icon>
                <span>重试</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style lang="less" scoped>
.chat-record {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: min(1400px, 96%);
  margin: 0 auto;

  .turn {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    width: 100%;
  }

  .meta {
    font-size: 11.5px;
    color: #98a2b3;
  }

  .avatar {
    width: 30px;
    height: 30px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-top: 2px;
    box-shadow: 0 2px 6px rgba(16, 24, 40, 0.08);
  }

  .user-avatar {
    background: #fff;
    color: #475467;
    border: 1px solid var(--border-color);
  }

  .ai-avatar {
    background: linear-gradient(135deg, #1677ff 0%, #69b1ff 100%);
    color: #fff;
  }

  .turn-body {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  /* ============== 用户消息 ============== */
  .user-turn {
    flex-direction: row-reverse;

    .turn-body {
      align-items: flex-end;
    }

    .bubble {
      background: var(--el-color-primary);
      color: #fff;
      border-radius: 12px 12px 4px 12px;
      padding: 9px 13px;
      font-size: 13.5px;
      line-height: 22px;
      white-space: pre-wrap;
      word-break: break-word;
      max-width: 76%;
      box-shadow: 0 2px 8px rgba(22, 119, 255, 0.18);
    }
  }

  /* ============== 助手消息 ============== */
  .assistant-turn {
    .agent-name {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12.5px;
      font-weight: 600;
      color: #344054;

      .mode-tag {
        padding: 1px 8px;
        font-size: 11px;
        font-weight: 500;
        color: var(--el-color-primary);
        background: var(--el-color-primary-light-9);
        border-radius: 10px;
      }
    }

    .assistant-card {
      display: block;
      background: #fff;
      border: 1px solid var(--border-color);
      border-radius: 12px 12px 12px 4px;
      padding: 12px 14px;
      box-shadow: 0 1px 3px rgba(16, 24, 40, 0.05);
    }

    .assistant-split {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
      align-items: start;
    }

    .thinking-col,
    .result-col {
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .thinking-col {
      padding-right: 0;
      border-right: 0;
      position: relative;

      .thinking-panel {
        position: static;
        max-height: none;
        overflow: visible;
        padding-right: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
    }

    .result-col {
      padding-left: 0;
    }

    .result-card {
      background: #fff;
      border: 1px solid var(--border-color-light);
      border-radius: 10px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }

    .panel-title {
      height: 26px;
      display: inline-flex;
      align-items: center;
      font-size: 12px;
      font-weight: 600;
      color: #475467;
      letter-spacing: 0.2px;
      text-transform: uppercase;
    }

    .message-footer {
      display: flex;
      align-items: center;
      gap: 12px;
      padding-left: 4px;

      .actions {
        display: flex;
        gap: 4px;
      }

      .action-btn {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        height: 24px;
        padding: 0 8px;
        background: transparent;
        border: none;
        border-radius: 6px;
        font-size: 11.5px;
        color: #98a2b3;
        cursor: pointer;

        &:hover:not(:disabled) {
          background: rgba(31, 35, 41, 0.06);
          color: #475467;
        }

        &:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
      }
    }
  }

  /* ============== 通用 block ============== */
  .block {
    border-radius: 8px;
    font-size: 13px;
  }

  .block-header {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 32px;
    padding: 0 10px;
    cursor: pointer;
    user-select: none;

    &.static {
      cursor: default;
    }

    .caret {
      transition: transform 0.15s ease;
      color: #98a2b3;
      transform: rotate(-90deg);

      &.open {
        transform: rotate(0deg);
      }
    }

    .block-icon {
      &.plan { color: var(--el-color-primary); }
      &.tool { color: #f79009; }
      &.summary { color: var(--el-color-primary); }
    }

    .block-title {
      font-weight: 500;
      color: #344054;
    }

    .badge {
      margin-left: auto;
      padding: 1px 8px;
      font-size: 11px;
      color: #475467;
      background: rgba(31, 35, 41, 0.06);
      border-radius: 10px;

      &.plan-badge {
        color: var(--el-color-primary);
        background: var(--el-color-primary-light-9);
      }
    }

    .elapsed {
      margin-left: auto;
      font-size: 11.5px;
      color: #98a2b3;
    }
  }

  .muted {
    color: #98a2b3;
    font-size: 12px;
  }

  /* plans */
  .plans-block {
    background: var(--el-color-primary-light-9);
    border: 1px solid var(--el-color-primary-light-7);

    .plan-body {
      list-style: none;
      padding: 4px 12px 10px 32px;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 6px;

      .plan-item {
        display: flex;
        align-items: center;
        gap: 8px;
        line-height: 20px;
        font-size: 13px;
        position: relative;

        &::before {
          content: '';
          position: absolute;
          left: -13px;
          top: 16px;
          bottom: -10px;
          width: 1px;
          background: #cfe1ff;
        }

        &:last-child::before {
          display: none;
        }

        .plan-icon {
          flex-shrink: 0;
          &.ok { color: #16a34a; }
          &.error { color: var(--el-color-danger); }
          &.running { color: var(--el-color-primary); }
        }

        .plan-label {
          color: #344054;
        }

        .plan-agent {
          font-size: 11px;
          color: var(--el-color-primary);
          background: rgba(255, 255, 255, 0.7);
          border: 1px solid var(--el-color-primary-light-7);
          border-radius: 10px;
          padding: 0 6px;
          line-height: 18px;
        }

        &.error .plan-label {
          color: var(--el-color-danger);
        }

        .error-text {
          color: var(--el-color-danger);
          font-size: 12px;
        }
      }
    }
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  @keyframes statusPulse {
    0% { opacity: 0.45; }
    50% { opacity: 1; }
    100% { opacity: 0.45; }
  }

  /* tools */
  .tools-block {
    background: #fafbfc;
    border: 1px solid var(--border-color-light);

    .tools-body {
      padding: 0 12px 10px 32px;
      display: flex;
      flex-direction: column;
      gap: 8px;

      .tool-group {
        display: flex;
        flex-direction: column;
        gap: 4px;

        .tool-group-title {
          font-size: 11px;
          color: #98a2b3;
          padding: 1px 0;
        }

        .tool-item {
          border-left: 2px solid var(--border-color);
          padding-left: 8px;
          display: flex;
          flex-direction: column;
          gap: 5px;

          .tool-trigger {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
            padding: 6px 8px;
            border: 1px solid var(--border-color-light);
            border-radius: 7px;
            background: #fff;
            cursor: pointer;
            transition: background 0.15s ease;

            &:hover {
              background: #f8fafc;
            }

            .tool-trigger-main {
              display: flex;
              align-items: center;
              gap: 7px;
              min-width: 0;
              flex: 1;
            }

            .tool-type-icon {
              flex-shrink: 0;
              color: #667085;
            }

            .tool-meta {
              min-width: 0;
              flex: 1;
            }

            .tool-title-line {
              display: flex;
              align-items: center;
              gap: 6px;
              min-width: 0;
            }

            .tool-name {
              font-size: 11.5px;
              font-weight: 500;
              color: #344054;
              flex-shrink: 0;
            }

            .tool-subtitle {
              font-size: 11px;
              color: #98a2b3;
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            }

            .tool-args {
              margin-top: 1px;
              font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
              font-size: 10.5px;
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            }

            .tool-trigger-action {
              display: inline-flex;
              align-items: center;
              gap: 6px;
              flex-shrink: 0;
            }

            .caret {
              color: #98a2b3;
              transform: rotate(-90deg);
              transition: transform 0.15s ease;

              &.open {
                transform: rotate(0deg);
              }
            }

            .tool-status {
              height: 16px;
              padding: 0 5px;
              border-radius: 10px;
              display: inline-flex;
              align-items: center;
              gap: 3px;
              font-size: 10px;
              font-weight: 500;
              border: 1px solid transparent;

              .status-dot {
                width: 5px;
                height: 5px;
                border-radius: 50%;
                background: currentColor;
                display: inline-block;
              }

              &.running {
                color: #667085;
                background: #f8fafc;
                border-color: #e4e7ec;

                .status-dot {
                  animation: statusPulse 1.2s ease-in-out infinite;
                }
              }

              &.ok {
                color: #475467;
                background: #f8fafc;
                border-color: #e4e7ec;
              }

              &.error {
                color: #b42318;
                background: #fef6f6;
                border-color: #fecdca;
              }
            }

            .tool-elapsed {
              font-size: 10.5px;
            }

            .caret.small {
              margin-left: -2px;
            }
          }

          .tool-detail {
            margin-left: 8px;
            padding: 0 0 0 8px;
            border-left: 2px solid var(--border-color-light);
            display: flex;
            flex-direction: column;
            gap: 4px;
          }

          .tool-thought, .tool-content {
            font-size: 11px;
            color: #475467;
            padding-left: 2px;
            line-height: 17px;

            .tag {
              display: inline-block;
              padding: 0 4px;
              border-radius: 3px;
              background: rgba(31, 35, 41, 0.08);
              font-size: 10px;
              color: #475467;
              margin-right: 4px;
            }

            pre {
              margin: 2px 0 0 0;
              white-space: pre-wrap;
              word-break: break-word;
              font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
              font-size: 10.5px;
              line-height: 1.5;
              color: #475467;
              max-height: 108px;
              overflow: auto;
            }
          }
        }
      }
    }
  }

  /* summary */
  .summary-block {
    background: linear-gradient(180deg, var(--el-color-primary-light-9) 0%, #fff 100%);
    border-color: var(--el-color-primary-light-7);

    .summary-body {
      padding: 4px 14px 12px;
    }
  }

  .report-block {
    border-color: #d9e2ef;

    .report-body {
      padding: 6px 10px 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .report-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .report-group-title {
      font-size: 11px;
      color: #98a2b3;
      padding: 2px 2px 0;
    }
  }

  /* thinking */
  .thinking-block {
    background: #f5f7fb;
    border: 1px solid var(--border-color-light);

    &.pending {
      display: inline-flex;
      align-self: flex-start;
      align-items: center;
      gap: 8px;
      color: #475467;
      padding: 6px 10px;

      .icon {
        color: var(--el-color-primary);
      }
    }

    &.streaming {
      border-color: var(--el-color-primary-light-7);
      background: var(--el-color-primary-light-9);

      .streaming-icon {
        color: var(--el-color-primary);
      }
    }

    .thinking-body {
      padding: 4px 12px 10px 32px;

      .steps {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;

        .step {
          display: flex;
          align-items: center;
          gap: 8px;
          line-height: 20px;
          font-size: 13px;
          position: relative;

          &::before {
            content: '';
            position: absolute;
            left: -13px;
            top: 16px;
            bottom: -10px;
            width: 1px;
            background: #dce3ec;
          }

          &:last-child::before {
            display: none;
          }

          .step-icon {
            flex-shrink: 0;
            &.ok { color: var(--el-color-primary); }
            &.error { color: var(--el-color-danger); }
          }

          .step-label {
            color: #344054;
            font-weight: 500;
          }

          .step-detail {
            color: #98a2b3;
          }

          .step-elapsed {
            margin-left: auto;
          }
        }
      }

      .reasoning {
        margin-top: 10px;
        padding-top: 10px;
        border-top: 1px dashed var(--border-color-light);

        pre {
          margin: 0;
          padding: 0;
          font-family: inherit;
          font-size: 12.5px;
          line-height: 22px;
          color: #475467;
          white-space: pre-wrap;
          word-break: break-word;
        }
      }
    }
  }

  /* answer */
  .answer {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 0;
  }

  .answer-text {
    color: #1f2329;
    line-height: 22px;
  }

  .result-text-card {
    padding: 10px 12px;
    border-radius: 10px;
  }

  .sql-block pre {
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
    font-size: 12.5px;
    line-height: 1.55;
    margin: 0;
  }

  .result-block {
    background: #fafbfc;
    padding: 12px 14px;
    border-radius: 10px;
    border-color: var(--border-color-light);
    display: flex;
    flex-direction: column;
    gap: 12px;

    .result-switch {
      align-self: flex-end;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 2px;
      border: 1px solid #d9e2ef;
      border-radius: 7px;
      background: #fff;

      .query-picker {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 0 4px 0 6px;

        .picker-label {
          font-size: 11px;
          color: #98a2b3;
          white-space: nowrap;
        }

        .query-select {
          width: 132px;
        }
      }

      .switch-tab {
        height: 22px;
        padding: 0 10px;
        border: 0;
        border-radius: 5px;
        background: transparent;
        color: #667085;
        font-size: 11px;
        line-height: 22px;
        cursor: pointer;

        &.active {
          color: var(--el-color-primary);
          background: var(--el-color-primary-light-9);
          font-weight: 600;
        }
      }
    }

    .result-section {
      background: #fff;
      border: 1px solid var(--border-color-light);
      border-radius: 8px;
      padding: 10px 10px 9px;
    }

    .result-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: -10px -10px 10px;
      padding: 8px 10px;
      background: #f8fafc;
      border-bottom: 1px solid #e9edf3;
      border-radius: 8px 8px 0 0;

      .result-title {
        font-size: 12px;
        font-weight: 600;
        color: #344054;
        display: flex;
        align-items: baseline;
        gap: 6px;
      }
    }

    .chart-toolbar {
      display: flex;
      align-items: center;
      gap: 1px;
      padding: 2px;
      border-radius: 6px;
      border: 1px solid #d9e2ef;
      background: #fff;

      .tool-btn {
        width: 24px;
        height: 24px;
        border-radius: 4px;
        background: transparent;
        border: none;
        color: #475467;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;

        &:hover {
          background: rgba(31, 35, 41, 0.06);
        }

        &.active {
          background: var(--el-color-primary-light-9);
          color: var(--el-color-primary);
        }
      }

      .divider {
        width: 1px;
        height: 12px;
        background: #d9e2ef;
        margin: 0 2px;
      }
    }

    .chart-wrapper {
      width: 100%;
      height: 340px;
      background: #fff;
      border-radius: 6px;
      padding: 8px;
    }

    .pagination {
      margin-top: 12px;
    }

    :deep(.el-table) {
      border-color: #e6eaf0;
      --el-table-border-color: #e6eaf0;
      --el-table-header-bg-color: #f8fafc;
      --el-table-row-hover-bg-color: #f5f8ff;
      --el-table-current-row-bg-color: #edf3ff;
      --el-table-text-color: #344054;
      --el-table-header-text-color: #475467;
      font-size: 12px;
    }

    :deep(.el-table th.el-table__cell) {
      font-weight: 600;
      font-size: 11.5px;
      padding: 7px 0;
    }

    :deep(.el-table td.el-table__cell) {
      padding: 7px 0;
    }
  }

  .error-block pre {
    background: transparent;
    color: var(--el-color-danger);
    padding: 0;
    margin: 0;
    white-space: pre-wrap;
  }

  @media (max-width: 1200px) {
    .assistant-turn {
      .assistant-split {
        grid-template-columns: 1fr;
      }
      .thinking-col {
        border-right: 0;
        border-bottom: 1px dashed var(--border-color-light);
        padding-right: 0;
        padding-bottom: 8px;
      .thinking-panel {
        position: static;
        max-height: none;
        overflow: visible;
      }
      }
      .result-col {
        padding-left: 0;
      }
    }
  }
}
</style>
