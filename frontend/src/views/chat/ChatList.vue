<script lang="ts" setup>
import { computed, ref } from 'vue'
import { Plus, Search, Delete, ArrowDown } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { Chat } from './typed'

const props = defineProps<{
  list: Chat[]
  activeId?: number
}>()

const emit = defineEmits<{
  select: [chat: Chat]
  create: []
  remove: [chat: Chat]
  toggleSidebar: []
}>()

const keyword = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.list || []
  return (props.list || []).filter((c) => (c.title || '').toLowerCase().includes(kw))
})

function getDate(v: any): Date | undefined {
  if (!v) return undefined
  return v instanceof Date ? v : new Date(v)
}

function groupKey(chat: Chat): string {
  const todayStart = dayjs().startOf('day').toDate()
  const todayEnd = dayjs().endOf('day').toDate()
  const weekStart = dayjs().subtract(7, 'day').startOf('day').toDate()
  const t = getDate(chat.update_time || chat.create_time)
  if (!t) return '未分组'
  if (t >= todayStart && t <= todayEnd) return '今天'
  if (t < todayStart && t >= weekStart) return '7天内'
  return '更早'
}

const expandMap = ref<Record<string, boolean>>({
  今天: true,
  '7天内': true,
  更早: true,
  未分组: true,
})

const groups = computed(() => {
  const order = ['今天', '7天内', '更早', '未分组']
  const map: Record<string, Chat[]> = {}
  for (const c of filtered.value) {
    const k = groupKey(c)
    if (!map[k]) map[k] = []
    map[k].push(c)
  }
  return order.filter((k) => map[k]?.length).map((k) => ({ key: k, list: map[k] }))
})
</script>

<template>
  <div class="chat-list">
    <div class="header">
      <span class="title">{{ $t('menu.Data Q&A') }}</span>
    </div>

    <div class="actions">
      <el-button
        class="new-conv-btn"
        type="primary"
        plain
        :icon="Plus"
        @click="emit('create')"
      >
        {{ $t('chat.new_conversation') }}
      </el-button>
    </div>

    <div class="search-wrapper">
      <el-input
        v-model="keyword"
        :placeholder="$t('chat.search_placeholder')"
        :prefix-icon="Search"
        clearable
      />
    </div>

    <el-scrollbar class="list-body">
      <div v-if="!filtered.length" class="empty">
        <el-empty :description="$t('chat.no_conversation')" :image-size="60" />
      </div>
      <div v-for="g in groups" :key="g.key" class="group">
        <div class="group-title" @click="expandMap[g.key] = !expandMap[g.key]">
          <el-icon :class="!expandMap[g.key] && 'collapsed'" :size="10">
            <ArrowDown />
          </el-icon>
          <span>{{ g.key }}</span>
        </div>
        <template v-for="item in g.list" :key="item.id">
          <div
            v-show="expandMap[g.key]"
            class="chat-item"
            :class="{ active: item.id === activeId }"
            @click="emit('select', item)"
          >
            <span class="title-text ellipsis">{{ item.title || '未命名对话' }}</span>
            <el-icon class="delete-icon" :size="14" @click.stop="emit('remove', item)">
              <Delete />
            </el-icon>
          </div>
        </template>
      </div>
    </el-scrollbar>
  </div>
</template>

<style lang="less" scoped>
.chat-list {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #f5f6f7;
  border-radius: 12px 0 0 12px;
  height: 100%;

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 16px 12px;

    .title {
      font-weight: 600;
      font-size: 16px;
      color: #1f2329;
    }
  }

  .actions {
    padding: 0 16px 12px;

    .new-conv-btn {
      width: 100%;
      height: 40px;
      border-radius: 8px;
      font-weight: 500;

      --el-button-bg-color: var(--el-color-primary-light-9);
      --el-button-hover-bg-color: var(--el-color-primary-light-8);
      --el-button-active-bg-color: var(--el-color-primary-light-8);
      --el-button-border-color: transparent;
      --el-button-hover-border-color: transparent;
      --el-button-active-border-color: transparent;
      --el-button-text-color: var(--el-color-primary);
      --el-button-hover-text-color: var(--el-color-primary);
      --el-button-active-text-color: var(--el-color-primary);
    }
  }

  .search-wrapper {
    padding: 0 16px 12px;

    :deep(.el-input__wrapper) {
      background: #fff;
      border-radius: 6px;
      box-shadow: 0 0 0 1px transparent inset;
    }
  }

  .list-body {
    flex: 1;
    min-height: 0;

    .empty {
      padding-top: 32px;
    }

    .group {
      padding: 0 16px;
      margin-bottom: 12px;

      .group-title {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 4px 8px;
        color: #646a73;
        font-size: 12px;
        font-weight: 500;
        line-height: 20px;
        cursor: pointer;
        user-select: none;

        .el-icon {
          transition: transform 0.15s ease;
        }

        .collapsed {
          transform: rotate(-90deg);
        }
      }

      .chat-item {
        height: 40px;
        padding: 0 8px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        font-size: 14px;
        line-height: 22px;
        color: #1f2329;
        margin-bottom: 2px;

        .title-text {
          flex: 1;
          min-width: 0;
        }

        .delete-icon {
          opacity: 0;
          color: #646a73;
          cursor: pointer;

          &:hover {
            color: var(--el-color-danger);
          }
        }

        &:hover {
          background: rgba(31, 35, 41, 0.06);

          .delete-icon {
            opacity: 1;
          }
        }

        &.active {
          background: #ffffff;
          font-weight: 500;
        }
      }
    }
  }
}
</style>
