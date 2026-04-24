<script lang="ts" setup>
import { computed, ref } from 'vue'
import {
  Plus,
  Search,
  Delete,
  ArrowDown,
  Fold,
  Expand,
  ChatDotRound,
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { Chat } from './typed'

const props = defineProps<{
  list: Chat[]
  activeId?: number
  collapsed: boolean
}>()

const emit = defineEmits<{
  select: [chat: Chat]
  create: []
  remove: [chat: Chat]
  'update:collapsed': [v: boolean]
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

const toggleCollapse = () => emit('update:collapsed', !props.collapsed)
</script>

<template>
  <aside class="chat-sider" :class="{ collapsed }">
    <!-- 折叠态：极简竖排 -->
    <template v-if="collapsed">
      <div class="collapsed-body">
        <el-tooltip :content="$t('chat.new_conversation')" placement="right">
          <button class="icon-btn primary" @click="emit('create')">
            <el-icon :size="18"><Plus /></el-icon>
          </button>
        </el-tooltip>
        <div class="divider" />
        <el-tooltip
          v-for="item in (filtered || []).slice(0, 12)"
          :key="item.id"
          :content="item.title || '未命名对话'"
          placement="right"
        >
          <button
            class="icon-btn"
            :class="{ active: item.id === activeId }"
            @click="emit('select', item)"
          >
            <el-icon :size="16"><ChatDotRound /></el-icon>
          </button>
        </el-tooltip>
      </div>
      <div class="collapsed-footer">
        <el-tooltip content="展开" placement="right">
          <button class="icon-btn" @click="toggleCollapse">
            <el-icon :size="18"><Expand /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </template>

    <!-- 展开态 -->
    <template v-else>
      <div class="header">
        <span class="title">{{ $t('menu.Data Q&A') }}</span>
        <el-tooltip content="折叠" placement="bottom">
          <button class="header-btn" @click="toggleCollapse">
            <el-icon :size="16"><Fold /></el-icon>
          </button>
        </el-tooltip>
      </div>

      <div class="actions">
        <el-button class="new-conv-btn" :icon="Plus" @click="emit('create')">
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
            <span class="group-count">{{ g.list.length }}</span>
          </div>
          <template v-for="item in g.list" :key="item.id">
            <div
              v-show="expandMap[g.key]"
              class="chat-item"
              :class="{ active: item.id === activeId }"
              @click="emit('select', item)"
            >
              <el-icon class="item-icon" :size="14"><ChatDotRound /></el-icon>
              <span class="title-text ellipsis">{{ item.title || '未命名对话' }}</span>
              <button class="delete-btn" @click.stop="emit('remove', item)" title="删除会话">
                <el-icon class="delete-icon" :size="17">
                  <Delete />
                </el-icon>
              </button>
            </div>
          </template>
        </div>
      </el-scrollbar>
    </template>
  </aside>
</template>

<style lang="less" scoped>
.chat-sider {
  width: 296px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #f7f8fb;
  border-right: 1px solid var(--border-color);
  height: 100%;
  transition: width 0.2s ease;

  &.collapsed {
    width: 64px;
    align-items: center;
    padding: 12px 0;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 14px 8px;

    .title {
      font-weight: 600;
      font-size: 15px;
      color: #101828;
    }

    .header-btn {
      width: 28px;
      height: 28px;
      border-radius: 6px;
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
    }
  }

  .actions {
    padding: 2px 14px 12px;

    .new-conv-btn {
      width: 100%;
      height: 40px;
      border-radius: 10px;
      font-weight: 500;
      --el-button-bg-color: var(--el-color-primary);
      --el-button-hover-bg-color: var(--el-color-primary-dark-2);
      --el-button-active-bg-color: var(--el-color-primary-dark-2);
      --el-button-border-color: var(--el-color-primary);
      --el-button-hover-border-color: var(--el-color-primary-dark-2);
      --el-button-active-border-color: var(--el-color-primary-dark-2);
      --el-button-text-color: #fff;
      --el-button-hover-text-color: #fff;
      --el-button-active-text-color: #fff;
    }
  }

  .search-wrapper {
    padding: 0 14px 12px;

    :deep(.el-input__wrapper) {
      background: #fff;
      border-radius: 10px;
      box-shadow: 0 0 0 1px var(--border-color) inset;

      &:hover {
        box-shadow: 0 0 0 1px var(--el-color-primary-light-5) inset;
      }

      &.is-focus {
        box-shadow: 0 0 0 1px var(--el-color-primary) inset;
      }
    }
  }

  .list-body {
    flex: 1;
    min-height: 0;

    .empty {
      padding-top: 32px;
    }

    .group {
      padding: 0 10px;
      margin-bottom: 12px;

      .group-title {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        color: #98a2b3;
        font-size: 11px;
        font-weight: 600;
        line-height: 20px;
        cursor: pointer;
        user-select: none;
        text-transform: uppercase;
        letter-spacing: 0.4px;

        .el-icon {
          transition: transform 0.15s ease;
        }

        .collapsed {
          transform: rotate(-90deg);
        }

        .group-count {
          margin-left: auto;
          font-size: 11px;
          color: #d0d5dd;
          letter-spacing: 0;
        }
      }

      .chat-item {
        height: 38px;
        padding: 0 10px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        font-size: 13.5px;
        line-height: 22px;
        color: #344054;
        margin-bottom: 3px;

        .item-icon {
          color: #98a2b3;
          flex-shrink: 0;
        }

        .title-text {
          flex: 1;
          min-width: 0;
        }

        .delete-btn {
          margin-left: 6px;
          width: 28px;
          height: 28px;
          border: 1px solid #fecdca;
          border-radius: 7px;
          background: #fef3f2;
          color: #d92d20;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          flex-shrink: 0;
          opacity: 0;
          pointer-events: none;
          transition: all 0.15s ease;

          .delete-icon {
            color: currentColor;
          }

          &:hover {
            background: #fee4e2;
            border-color: #fda29b;
            transform: scale(1.08);
          }
        }

        &:hover {
          background: rgba(31, 35, 41, 0.05);

          .delete-btn {
            opacity: 0.92;
            pointer-events: auto;
          }
        }

        &.active {
          background: #e9f0ff;
          color: var(--el-color-primary);
          font-weight: 500;
          border-left: 3px solid var(--el-color-primary);
          border-radius: 0 8px 8px 0;

          .item-icon {
            color: var(--el-color-primary);
          }

          .delete-btn {
            opacity: 0.92;
            pointer-events: auto;
            border-color: #fda29b;
            background: #fee4e2;
          }
        }
      }
    }
  }

  .collapsed-body {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    flex: 1;
    width: 100%;
    overflow-y: auto;
    padding-bottom: 12px;

    .divider {
      width: 28px;
      height: 1px;
      background: var(--border-color);
      margin: 6px 0;
    }
  }

  .collapsed-footer {
    margin-top: auto;
  }

  .icon-btn {
    width: 38px;
    height: 38px;
    border-radius: 8px;
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
      background: #fff;
      color: var(--el-color-primary);
      box-shadow: 0 1px 3px rgba(16, 24, 40, 0.06);
    }

    &.primary {
      background: var(--el-color-primary);
      color: #fff;

      &:hover {
        background: var(--el-color-primary-dark-2);
      }
    }
  }
}
</style>
