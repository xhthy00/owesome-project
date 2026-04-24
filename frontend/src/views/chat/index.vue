<script lang="ts" setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Top, Bottom } from '@element-plus/icons-vue'
import ChatSider from './ChatSider.vue'
import ChatHeader from './ChatHeader.vue'
import ChatRecord from './ChatRecord.vue'
import ChatInput from './ChatInput.vue'
import ChatDefault from './ChatDefault.vue'
import { chatApi } from '@/api/chat'
import { datasourceApi, type DatasourceItem } from '@/api/datasource'
import {
  Chat,
  ChatInfo,
  ChatRecord as ChatRecordModel,
  type AgentMode,
  type ChatMessage,
  type PlanState,
  type ToolCallRecord,
} from './typed'

const conversations = ref<Chat[]>([])
const datasources = ref<DatasourceItem[]>([])
const active = ref<ChatInfo | null>(null)
const messages = ref<ChatMessage[]>([])
const selectedDsId = ref<number | undefined>(undefined)
const agentMode = ref<AgentMode>('team')
const enableToolAgent = ref(true)
const loading = ref(false)
const scrollRef = ref()
const inputRef = ref<InstanceType<typeof ChatInput>>()
const siderCollapsed = ref(false)
const showScrollButtons = ref(false)
const isAtTop = ref(true)
const isAtBottom = ref(false)
const recordViewKey = ref(0)

const activeId = computed(() => active.value?.id)
const datasourceLocked = computed(() => messages.value.length > 0)
const hasMessages = computed(() => messages.value.length > 0)
const canSend = computed(() => !!selectedDsId.value)

const loadConversations = async () => {
  try {
    const res = await chatApi.list(50)
    conversations.value = res.items
  } catch (e: any) {
    ElMessage.error(e?.message || '获取对话列表失败')
  }
}

const loadDatasources = async () => {
  try {
    const res = await datasourceApi.list({ limit: 100 })
    datasources.value = res.items
    if (!selectedDsId.value && res.items.length) {
      selectedDsId.value = res.items[0].id
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '获取数据源列表失败')
  }
}

const onCreate = () => {
  active.value = null
  messages.value = []
  recordViewKey.value += 1
}

const onSelect = async (c: Chat) => {
  if (!c.id) return
  try {
    const detail = await chatApi.get(c.id)
    active.value = detail
    selectedDsId.value = detail.datasource_id
    messages.value = []
    detail.records.forEach((r) => {
      messages.value.push({ role: 'user', question: r.question, create_time: r.create_time })
      messages.value.push({ role: 'assistant', record: r, create_time: r.create_time })
    })
    recordViewKey.value += 1
    scrollToBottom()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载对话失败')
  }
}

const onRemove = (c: Chat) => {
  const currentList = [...conversations.value]
  const idx = currentList.findIndex((it) => it.id === c.id)
  const fallbackNext =
    idx >= 0
      ? currentList[idx + 1] || currentList[idx - 1] || null
      : null

  ElMessageBox.confirm('确定删除该会话？删除后不可恢复。', '提示', { type: 'warning' })
    .then(async () => {
      if (!c.id) return
      await chatApi.delete(c.id)
      ElMessage.success('已删除')
      await loadConversations()
      if (active.value?.id === c.id) {
        const next =
          (fallbackNext?.id
            ? conversations.value.find((it) => it.id === fallbackNext.id)
            : null) || null
        if (next) {
          await onSelect(next)
        } else {
          active.value = null
          messages.value = []
          recordViewKey.value += 1
        }
      }
    })
    .catch(() => {})
}

const _isTextInputLike = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = (el.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if (el.isContentEditable) return true
  return !!el.closest('input, textarea, select, [contenteditable="true"]')
}

const onGlobalKeydown = (e: KeyboardEvent) => {
  if (e.key !== 'Delete' && e.key !== 'Backspace') return
  if (_isTextInputLike(e.target)) return
  const current = conversations.value.find((it) => it.id === active.value?.id)
  if (!current) return
  e.preventDefault()
  onRemove(current)
}

const ensureConversation = async (question: string): Promise<ChatInfo> => {
  if (active.value?.id) return active.value
  const created = await chatApi.create({
    title: question.slice(0, 32),
    datasource_id: selectedDsId.value,
  })
  active.value = new ChatInfo({ ...created, records: [] })
  await loadConversations()
  return active.value
}

const scrollToBottom = () => {
  nextTick(() => {
    const wrap = scrollRef.value?.wrapRef
    if (wrap) {
      wrap.scrollTop = wrap.scrollHeight
      updateScrollState()
    }
  })
}

const scrollToTop = () => {
  const wrap = scrollRef.value?.wrapRef
  if (!wrap) return
  wrap.scrollTo({ top: 0, behavior: 'smooth' })
}

const updateScrollState = () => {
  const wrap = scrollRef.value?.wrapRef
  if (!wrap) return
  const { scrollTop, scrollHeight, clientHeight } = wrap
  const buffer = 20
  isAtTop.value = scrollTop <= buffer
  isAtBottom.value = scrollTop + clientHeight >= scrollHeight - buffer
  showScrollButtons.value = scrollHeight > clientHeight + 8
}

const onScroll = () => {
  updateScrollState()
}

const onPickQuestion = (q: string) => {
  inputRef.value?.setText(q)
}

const onRetry = async (question: string) => {
  if (loading.value) return
  await onSend(question)
}

const onSend = async (text: string) => {
  if (!selectedDsId.value) {
    ElMessage.warning('请先选择数据源')
    return
  }
  loading.value = true
  const now = new Date()
  messages.value.push({ role: 'user', question: text, create_time: now })

  const liveRecord = new ChatRecordModel({
    question: text,
    create_time: now,
    reasoning: '',
    steps: [],
    agent_mode: agentMode.value,
    plans: undefined,
    plan_states: [],
    tool_calls: [],
    reports: [],
  })
  const placeholderIdx = messages.value.length
  messages.value.push({
    role: 'assistant',
    pending: true,
    record: liveRecord,
    create_time: now,
  })
  scrollToBottom()

  const findOrCreateToolCall = (
    sub_task_index: number | undefined,
    round: number
  ): ToolCallRecord => {
    const list = liveRecord.tool_calls || (liveRecord.tool_calls = [])
    let rec = list.find(
      (r) => r.round === round && (r.sub_task_index ?? -1) === (sub_task_index ?? -1)
    )
    if (!rec) {
      rec = { round, sub_task_index, tool: '' }
      list.push(rec)
    }
    return rec
  }

  try {
    const conv = await ensureConversation(text)
    let lastError: string | null = null

    await chatApi.executeStream(
      {
        question: text,
        datasource_id: selectedDsId.value,
        conversation_id: conv.id,
        agent_mode: agentMode.value,
        enable_tool_agent: enableToolAgent.value,
      },
      {
        onStep: (step) => {
          liveRecord.steps = [...(liveRecord.steps || []), step]
          scrollToBottom()
        },
        onReasoning: (textVal) => {
          liveRecord.reasoning = textVal
        },
        onSql: (sql, chartType) => {
          liveRecord.sql = sql
          if (!liveRecord.chart_config) liveRecord.chart_type = chartType
        },
        onResult: (resultPayload) => {
          liveRecord.exec_result = resultPayload
        },
        onError: (msg) => {
          lastError = msg
          liveRecord.sql_error = liveRecord.sql ? msg : liveRecord.sql_error
        },
        onDone: (recordId) => {
          if (recordId) liveRecord.id = recordId
        },
        onToolCall: (p) => {
          const rec = findOrCreateToolCall(p.sub_task_index, p.round)
          rec.tool = p.tool
          rec.args = p.args
          rec.thought = p.thought
          liveRecord.tool_calls = [...(liveRecord.tool_calls || [])]
          scrollToBottom()
        },
        onToolResult: (p) => {
          const rec = findOrCreateToolCall(p.sub_task_index, p.round)
          if (!rec.tool) rec.tool = p.tool
          rec.success = p.success
          rec.content = p.content
          rec.data = p.data
          rec.elapsed_ms = p.elapsed_ms
          liveRecord.tool_calls = [...(liveRecord.tool_calls || [])]
          scrollToBottom()
        },
        onPlan: ({ plans, sub_task_agents }) => {
          liveRecord.plans = plans
          liveRecord.sub_task_agents = sub_task_agents
          liveRecord.plan_states = plans.map((st, idx) => ({
            index: idx,
            sub_task: st,
            sub_task_agent: sub_task_agents?.[idx],
            state: 'running',
          }))
          scrollToBottom()
        },
        onPlanUpdate: (state: PlanState) => {
          const list = liveRecord.plan_states || (liveRecord.plan_states = [])
          const existing = list.find((s) => s.index === state.index)
          if (existing) Object.assign(existing, state)
          else list.push(state)
          liveRecord.plan_states = [...list]
          scrollToBottom()
        },
        onChart: ({ chart_type, chart_config }) => {
          liveRecord.chart_type = chart_type
          liveRecord.chart_config = chart_config
        },
        onSummary: (content) => {
          liveRecord.summary = content
        },
        onReport: (payload) => {
          const list = liveRecord.reports || (liveRecord.reports = [])
          list.push(payload)
          liveRecord.reports = [...list]
          scrollToBottom()
        },
      }
    )

    const finishedAt = new Date()
    if (lastError && !liveRecord.sql) {
      messages.value.splice(placeholderIdx, 1, {
        role: 'assistant',
        error: lastError,
        create_time: finishedAt,
      })
    } else {
      messages.value.splice(placeholderIdx, 1, {
        role: 'assistant',
        record: liveRecord,
        create_time: finishedAt,
      })
    }
  } catch (e: any) {
    messages.value.splice(placeholderIdx, 1, {
      role: 'assistant',
      error: e?.message || '请求失败',
      create_time: new Date(),
    })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

onMounted(async () => {
  await Promise.all([loadConversations(), loadDatasources()])
  window.addEventListener('keydown', onGlobalKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<template>
  <div class="chat-page">
    <ChatSider
      v-model:collapsed="siderCollapsed"
      :list="conversations"
      :active-id="activeId"
      @create="onCreate"
      @select="onSelect"
      @remove="onRemove"
    />

    <section class="chat-main">
      <ChatHeader
        v-model:selected-ds-id="selectedDsId"
        v-model:agent-mode="agentMode"
        v-model:enable-tool-agent="enableToolAgent"
        :datasources="datasources"
        :datasource-locked="datasourceLocked"
        :has-messages="hasMessages"
        @create="onCreate"
        @scroll-bottom="scrollToBottom"
      />

      <div class="chat-body">
        <el-scrollbar v-if="hasMessages" ref="scrollRef" class="chat-scroll" @scroll="onScroll">
          <div class="chat-scroll-inner">
            <ChatRecord :key="recordViewKey" :messages="messages" @retry="onRetry" />
          </div>
        </el-scrollbar>

        <ChatDefault
          v-else
          :has-datasource="!!datasources.length"
          @pick-question="onPickQuestion"
          @send="onSend"
        />
      </div>

      <div class="input-sticky">
        <ChatInput
          ref="inputRef"
          :disabled="loading"
          :can-send-extra-check="canSend"
          @send="onSend"
        />
      </div>

      <div v-if="hasMessages && showScrollButtons" class="scroll-buttons">
        <button v-if="!isAtTop" class="scroll-btn" @click="scrollToTop">
          <el-icon :size="14"><Top /></el-icon>
        </button>
        <button v-if="!isAtBottom" class="scroll-btn" @click="scrollToBottom">
          <el-icon :size="14"><Bottom /></el-icon>
        </button>
      </div>
    </section>
  </div>
</template>

<style lang="less" scoped>
.chat-page {
  display: flex;
  height: 100%;
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  background: #f5f7fb;

  .chat-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: linear-gradient(180deg, #f8f9fc 0%, #f3f6fb 100%);
    position: relative;

    .chat-body {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
    }

    .chat-scroll {
      flex: 1;
      min-height: 0;

      .chat-scroll-inner {
        width: 100%;
        padding: 20px 24px 12px;
        display: flex;
        justify-content: center;
      }
    }

    .input-sticky {
      position: relative;
      z-index: 10;
      background: linear-gradient(180deg, rgba(247, 249, 252, 0) 0%, rgba(247, 249, 252, 0.95) 24%, #f7f9fc 100%);
      border-top: 1px solid rgba(228, 231, 236, 0.7);
    }

    .scroll-buttons {
      position: absolute;
      right: 18px;
      bottom: 110px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      z-index: 20;

      .scroll-btn {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        border: 1px solid var(--border-color);
        background: #fff;
        color: #525964;
        box-shadow: 0 4px 10px rgba(16, 24, 40, 0.12);
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        transition: all 0.15s ease;

        &:hover {
          color: var(--el-color-primary);
          border-color: var(--el-color-primary-light-5);
          transform: translateY(-1px);
          box-shadow: 0 6px 14px rgba(16, 24, 40, 0.16);
        }
      }
    }
  }
}
</style>
