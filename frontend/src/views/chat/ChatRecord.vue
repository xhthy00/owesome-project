<script lang="ts" setup>
import { ref, computed } from 'vue'
import {
  ChatDotRound,
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
} from '@element-plus/icons-vue'
import type { ChatMessage } from './typed'
import type { ChartTypes } from './component/BaseChart'
import ChartComponent from './component/ChartComponent.vue'
import { datetimeFormat } from '@/utils/utils'

const props = defineProps<{ messages: ChatMessage[] }>()

const PAGE_SIZE = 20

const pageMap = ref<Record<number, number>>({})
const expandThinking = ref<Record<number, boolean>>({})
const chartTypeMap = ref<Record<number, ChartTypes>>({})
const showLabelMap = ref<Record<number, boolean>>({})

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

// pending 时默认展开（让用户看到流式过程），完成后默认收起
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

const slicedRows = (i: number, rows: any[][] | undefined) => {
  if (!rows) return []
  const p = getPage(i)
  const start = (p - 1) * PAGE_SIZE
  return rows.slice(start, start + PAGE_SIZE)
}

// 占位消息(pending)滚动时显示动态思考点
const dotInterval = ref(0)
setInterval(() => (dotInterval.value = (dotInterval.value + 1) % 4), 500)
const dots = computed(() => '.'.repeat(dotInterval.value))
void props
</script>

<template>
  <div class="chat-record">
    <template v-for="(msg, i) in messages" :key="i">
      <!-- 用户消息：右对齐气泡 + 时间戳 -->
      <div v-if="msg.role === 'user'" class="row user-row">
        <div class="user-bubble">{{ msg.question }}</div>
        <div class="meta">{{ datetimeFormat(msg.create_time) }}</div>
      </div>

      <!-- AI 消息：左对齐 logo + 内容 -->
      <div v-else class="row assistant-row">
        <div class="avatar">
          <el-icon :size="16"><ChatDotRound /></el-icon>
        </div>
        <div class="assistant-body">
            <!-- 思考过程：pending 时如已有 steps 流入则实时展示，否则显示思考中动画 -->
              <template v-if="msg.pending">
                <div
                  v-if="hasThinking(msg)"
                  class="thinking-card streaming"
                >
                  <div class="thinking-header" @click="toggleThinking(i, true)">
                    <el-icon
                      class="caret"
                      :class="{ open: isThinkingExpanded(i, true) }"
                      :size="12"
                    >
                      <ArrowDown />
                    </el-icon>
                    <el-icon class="is-loading streaming-icon" :size="14">
                      <Loading />
                    </el-icon>
                    <span class="title">{{ $t('chat.thinking_now') }}{{ dots }}</span>
                    <span class="elapsed muted">
                      {{ formatElapsed(totalElapsedMs(msg.record!.steps)) }}
                    </span>
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
                        <span v-if="step.detail" class="step-detail muted">
                          {{ step.detail }}
                        </span>
                        <span class="step-elapsed muted">
                          {{ formatElapsed(step.elapsed_ms || 0) }}
                        </span>
                      </li>
                    </ol>
                    <div v-if="msg.record!.reasoning" class="reasoning">
                      <pre>{{ msg.record!.reasoning }}</pre>
                    </div>
                  </div>
                </div>
                <div v-else class="thinking-card pending">
                  <el-icon class="is-loading icon"><Loading /></el-icon>
                  <span class="title">{{ $t('chat.thinking_now') }}{{ dots }}</span>
                </div>
              </template>

              <div v-else-if="hasThinking(msg)" class="thinking-card">
                <div class="thinking-header" @click="toggleThinking(i)">
                  <el-icon
                    class="caret"
                    :class="{ open: isThinkingExpanded(i) }"
                    :size="12"
                  >
                    <ArrowDown />
                  </el-icon>
                  <span class="title">{{ $t('chat.thinking') }}</span>
                  <span class="elapsed muted">
                    {{ formatElapsed(totalElapsedMs(msg.record!.steps)) }}
                  </span>
                </div>
                <div v-show="isThinkingExpanded(i)" class="thinking-body">
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
                      <span v-if="step.detail" class="step-detail muted">
                        {{ step.detail }}
                      </span>
                      <span class="step-elapsed muted">
                        {{ formatElapsed(step.elapsed_ms || 0) }}
                      </span>
                    </li>
                  </ol>
                  <div v-if="msg.record!.reasoning" class="reasoning">
                    <pre>{{ msg.record!.reasoning }}</pre>
                  </div>
                </div>
              </div>

          <!-- 错误 -->
          <div v-if="!msg.pending && msg.error" class="error-block">
            <el-alert type="error" :title="$t('chat.execution_failed')" :closable="false">
              <pre>{{ msg.error }}</pre>
            </el-alert>
          </div>

              <!-- 答案：SQL + 结果 / SQL 错误（pending 期间也支持渐进展示） -->
              <div v-if="msg.record && (msg.record.sql || msg.record.exec_result || msg.record.sql_error)" class="answer">
            <div v-if="msg.record.sql" class="sql-block">
              <div class="block-title">SQL</div>
              <pre><code>{{ msg.record.sql }}</code></pre>
            </div>
            <div v-if="msg.record.sql_error" class="error-block">
              <el-alert type="error" :title="$t('chat.execution_failed')" :closable="false">
                <pre>{{ msg.record.sql_error }}</pre>
              </el-alert>
            </div>
            <div v-else-if="msg.record.exec_result" class="result-block">
              <div class="result-header">
                <div class="block-title">
                  {{ $t('chat.result') }}
                  <span class="muted">
                    {{ $t('chat.rows', { n: msg.record.exec_result.row_count ?? 0 }) }}
                  </span>
                </div>
                <div class="chart-toolbar">
                  <el-tooltip
                    v-for="opt in CHART_OPTIONS"
                    :key="opt.value"
                    :content="$t(opt.label)"
                    placement="top"
                    :show-after="300"
                  >
                    <el-button
                      text
                      class="tool-btn"
                      :class="{
                        active:
                          getChartType(i, msg.record!.chart_type) === opt.value,
                      }"
                      @click="setChartType(i, opt.value)"
                    >
                      <el-icon :size="16">
                        <component :is="opt.icon" />
                      </el-icon>
                    </el-button>
                  </el-tooltip>
                  <span
                    v-if="getChartType(i, msg.record!.chart_type) !== 'table'"
                    class="divider"
                  />
                  <el-tooltip
                    v-if="getChartType(i, msg.record!.chart_type) !== 'table'"
                    :content="getShowLabel(i) ? $t('chat.hide_label') : $t('chat.show_label')"
                    placement="top"
                    :show-after="300"
                  >
                    <el-button
                      text
                      class="tool-btn"
                      :class="{ active: getShowLabel(i) }"
                      @click="toggleShowLabel(i)"
                    >
                      <el-icon :size="16">
                        <View v-if="getShowLabel(i)" />
                        <Hide v-else />
                      </el-icon>
                    </el-button>
                  </el-tooltip>
                </div>
              </div>

              <template v-if="getChartType(i, msg.record!.chart_type) === 'table'">
                <el-table
                  :data="
                    slicedRows(i, msg.record.exec_result.rows)?.map((row) =>
                      Object.fromEntries(
                        (msg.record!.exec_result!.columns || []).map((c, idx) => [c, row[idx]])
                      )
                    )
                  "
                  stripe
                  size="small"
                  border
                >
                  <el-table-column
                    v-for="col in msg.record.exec_result.columns || []"
                    :key="col"
                    :prop="col"
                    :label="col"
                  />
                </el-table>
                <el-pagination
                  v-if="(msg.record.exec_result.row_count ?? 0) > PAGE_SIZE"
                  :current-page="getPage(i)"
                  :page-size="PAGE_SIZE"
                  :total="msg.record.exec_result.row_count ?? 0"
                  layout="prev, pager, next, total"
                  small
                  class="pagination"
                  @current-change="(p: number) => setPage(i, p)"
                />
              </template>
              <div v-else class="chart-wrapper">
                <ChartComponent
                  :id="msg.record!.id || i"
                  :type="
                    getChartType(i, msg.record!.chart_type) as
                      | 'column'
                      | 'bar'
                      | 'line'
                      | 'pie'
                  "
                  :columns="msg.record.exec_result.columns || []"
                  :rows="msg.record.exec_result.rows || []"
                  :show-label="getShowLabel(i)"
                />
              </div>
            </div>
          </div>

          <div v-if="!msg.pending" class="meta">{{ datetimeFormat(msg.create_time) }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style lang="less" scoped>
.chat-record {
  display: flex;
  flex-direction: column;
  gap: 24px;

  .row {
    display: flex;
  }

  .meta {
    font-size: 12px;
    color: #8f959e;
    margin-top: 6px;
  }

  // 用户消息（右）
  .user-row {
    flex-direction: column;
    align-items: flex-end;

    .user-bubble {
      max-width: 70%;
      padding: 10px 14px;
      background: var(--el-color-primary-light-9);
      border-radius: 12px;
      font-size: 14px;
      line-height: 22px;
      white-space: pre-wrap;
      word-break: break-word;
      color: #1f2329;
    }
  }

  // AI 消息（左）
  .assistant-row {
    gap: 12px;
    align-items: flex-start;

    .avatar {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: var(--el-color-primary);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .assistant-body {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 12px;

      // 思考过程卡片
      .thinking-card {
        align-self: flex-start;
        max-width: 100%;
        background: #f5f6f7;
        border: 1px solid #eef0f2;
        border-radius: 8px;
        font-size: 13px;
        color: #1f2329;
        padding: 6px 12px;

          &.pending {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            color: #646a73;

            .icon {
              color: var(--el-color-primary);
            }
          }

          &.streaming {
            border-color: var(--el-color-primary-light-7);
            background: var(--el-color-primary-light-9);

            .streaming-icon {
              color: var(--el-color-primary);
              margin-right: 2px;
            }
          }

        .thinking-header {
          display: flex;
          align-items: center;
          gap: 6px;
          height: 28px;
          cursor: pointer;
          user-select: none;

          .caret {
            transition: transform 0.15s ease;
            color: #646a73;
            transform: rotate(-90deg);

            &.open {
              transform: rotate(0deg);
            }
          }

          .title {
            font-weight: 500;
          }

          .elapsed {
            margin-left: 6px;
            font-size: 12px;
          }
        }

        .thinking-body {
          padding: 8px 4px 4px 18px;
          border-top: 1px dashed #e1e3e6;
          margin-top: 4px;

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

              .step-icon {
                flex-shrink: 0;

                &.ok {
                  color: var(--el-color-primary);
                }

                &.error {
                  color: var(--el-color-danger);
                }
              }

              .step-label {
                color: #1f2329;
                font-weight: 500;
              }

              .step-detail {
                font-size: 12px;
                color: #646a73;
              }

              .step-elapsed {
                margin-left: auto;
                font-size: 12px;
                color: #8f959e;
              }
            }
          }

          .reasoning {
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px dashed #e1e3e6;

            pre {
              margin: 0;
              padding: 0;
              font-family: inherit;
              font-size: 13px;
              line-height: 22px;
              color: #1f2329;
              white-space: pre-wrap;
              word-break: break-word;
            }
          }
        }
      }

      .answer {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }

      .block-title {
        font-weight: 500;
        font-size: 13px;
        margin-bottom: 8px;
        display: flex;
        gap: 8px;
        align-items: baseline;
        color: #1f2329;
      }

      .sql-block pre {
        background: #1e1e1e;
        color: #d4d4d4;
        padding: 12px;
        border-radius: 8px;
        overflow-x: auto;
        font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
        font-size: 12px;
        line-height: 1.5;
        margin: 0;
      }

          .result-block {
        background: #fafbfc;
        padding: 12px;
        border-radius: 8px;
        border: 1px solid #eef0f2;

        .result-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 8px;

          .block-title {
            margin-bottom: 0;
          }

          .chart-toolbar {
            display: flex;
            align-items: center;
            gap: 2px;
            padding: 2px;
            border-radius: 6px;
            border: 1px solid #dee0e3;
            background: #fff;

            .tool-btn {
              width: 26px;
              height: 26px;
              min-width: 26px;
              border-radius: 4px;
              color: #646a73;
              padding: 0;

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
              height: 14px;
              background: rgba(31, 35, 41, 0.15);
              margin: 0 2px;
            }
          }
        }

        .chart-wrapper {
          width: 100%;
          height: 360px;
          background: #fff;
          border-radius: 6px;
          padding: 8px;
        }

        .pagination {
          margin-top: 12px;
        }
      }

      .error-block pre {
        background: transparent;
        color: var(--el-color-danger);
        padding: 0;
        margin: 0;
        white-space: pre-wrap;
      }
    }
  }
}
</style>
