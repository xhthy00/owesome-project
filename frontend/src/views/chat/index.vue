<script lang="ts" setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ChatDotRound } from '@element-plus/icons-vue'
import ChatList from './ChatList.vue'
import ChatRecord from './ChatRecord.vue'
import ChatInput from './ChatInput.vue'
import { chatApi } from '@/api/chat'
import { datasourceApi, type DatasourceItem } from '@/api/datasource'
import { Chat, ChatInfo, ChatRecord as ChatRecordModel, type ChatMessage } from './typed'

const conversations = ref<Chat[]>([])
const datasources = ref<DatasourceItem[]>([])
const active = ref<ChatInfo | null>(null)
const messages = ref<ChatMessage[]>([])
const selectedDsId = ref<number | undefined>(undefined)
const loading = ref(false)
const scrollRef = ref()

const activeId = computed(() => active.value?.id)
const datasourceLocked = computed(() => messages.value.length > 0)
const hasMessages = computed(() => messages.value.length > 0)

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
  } catch (e: any) {
    ElMessage.error(e?.message || '获取数据源列表失败')
  }
}

const onCreate = () => {
  active.value = null
  messages.value = []
  selectedDsId.value = undefined
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
    scrollToBottom()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载对话失败')
  }
}

const onRemove = (c: Chat) => {
  ElMessageBox.confirm('确定删除该对话？', '提示', { type: 'warning' })
    .then(async () => {
      if (!c.id) return
      await chatApi.delete(c.id)
      ElMessage.success('已删除')
      if (active.value?.id === c.id) {
        active.value = null
        messages.value = []
      }
      await loadConversations()
    })
    .catch(() => {})
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
    if (wrap) wrap.scrollTop = wrap.scrollHeight
  })
}

const onSend = async (text: string) => {
  if (!selectedDsId.value) {
    ElMessage.warning('请先选择数据源')
    return
  }
  loading.value = true
  const now = new Date()
  messages.value.push({ role: 'user', question: text, create_time: now })

  // Pending 消息直接挂一个空的 ChatRecord，后续按流式事件原地更新
  const liveRecord = new ChatRecordModel({
    question: text,
    create_time: now,
    reasoning: '',
    steps: [],
  })
  const placeholderIdx = messages.value.length
  messages.value.push({
    role: 'assistant',
    pending: true,
    record: liveRecord,
    create_time: now,
  })
  scrollToBottom()

  try {
    const conv = await ensureConversation(text)
    let lastError: string | null = null

    await chatApi.executeStream(
      {
        question: text,
        datasource_id: selectedDsId.value,
        conversation_id: conv.id,
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
          liveRecord.chart_type = chartType
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

const onClickStart = () => {
  if (!datasources.value.length) {
    ElMessage.warning('请先到“数据源”创建一个连接')
    return
  }
  if (!selectedDsId.value) {
    selectedDsId.value = datasources.value[0].id
  }
}

onMounted(async () => {
  await Promise.all([loadConversations(), loadDatasources()])
})
</script>

<template>
  <div class="chat-page">
    <ChatList
      :list="conversations"
      :active-id="activeId"
      @create="onCreate"
      @select="onSelect"
      @remove="onRemove"
    />

    <section class="chat-main">
      <el-scrollbar v-if="hasMessages" ref="scrollRef" class="chat-scroll">
        <div class="chat-scroll-inner">
          <ChatRecord :messages="messages" />
        </div>
      </el-scrollbar>

      <div v-else class="welcome-block">
        <div class="welcome-content">
          <div class="logo-circle">
            <el-icon :size="20"><ChatDotRound /></el-icon>
          </div>
          <div class="greeting">{{ $t('chat.welcome_title') }}</div>
          <div class="sub">{{ $t('chat.welcome_subtitle') }}</div>
          <el-button
            size="large"
            type="primary"
            class="greeting-btn"
            @click="onClickStart"
          >
            <el-icon class="inner-icon"><Plus /></el-icon>
            {{ $t('chat.start_chat') }}
          </el-button>
        </div>
      </div>

      <ChatInput
        v-model="selectedDsId"
        :datasources="datasources"
        :datasource-locked="datasourceLocked"
        :disabled="loading"
        @send="onSend"
      />
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

  .chat-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: #fff;
    border-radius: 0 12px 12px 0;

    .chat-scroll {
      flex: 1;
      min-height: 0;

      .chat-scroll-inner {
        width: 100%;
        padding: 24px 56px 12px;
      }
    }
  }
}

.welcome-block {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;

  .welcome-content {
    width: 100%;
    max-width: 800px;
    padding: 0 16px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;

    .logo-circle {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: var(--el-color-primary);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .greeting {
      font-weight: 600;
      font-size: 24px;
      line-height: 32px;
      color: #1f2329;
    }

    .sub {
      color: #8f959e;
      font-size: 14px;
      line-height: 22px;
      text-align: center;
      max-width: 520px;
    }

    .greeting-btn {
      width: 100%;
      height: 88px;
      border-radius: 16px;
      border-style: dashed;
      font-size: 16px;
      font-weight: 500;
      margin-top: 8px;

      .inner-icon {
        margin-right: 6px;
      }

      --el-button-bg-color: #f8f9fa;
      --el-button-text-color: var(--el-color-primary);
      --el-button-border-color: #d9dcdf;
      --el-button-hover-bg-color: var(--el-color-primary-light-9);
      --el-button-hover-border-color: var(--el-color-primary);
      --el-button-hover-text-color: var(--el-color-primary);
      --el-button-active-bg-color: var(--el-color-primary-light-8);
      --el-button-active-border-color: var(--el-color-primary);
      --el-button-active-text-color: var(--el-color-primary);
    }
  }
}
</style>
