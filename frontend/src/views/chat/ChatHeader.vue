<script lang="ts" setup>
import { computed } from 'vue'
import { Connection, Plus, ArrowDown, Bottom } from '@element-plus/icons-vue'
import type { DatasourceItem } from '@/api/datasource'
import type { AgentMode } from './typed'

const props = defineProps<{
  datasources: DatasourceItem[]
  selectedDsId?: number
  datasourceLocked?: boolean
  agentMode: AgentMode
  enableToolAgent: boolean
  hasMessages: boolean
}>()

const emit = defineEmits<{
  'update:selectedDsId': [v: number | undefined]
  'update:agentMode': [v: AgentMode]
  'update:enableToolAgent': [v: boolean]
  create: []
  scrollBottom: []
}>()

const dsModel = computed({
  get: () => props.selectedDsId,
  set: (v) => emit('update:selectedDsId', v),
})

const modeModel = computed({
  get: () => props.agentMode,
  set: (v: AgentMode) => emit('update:agentMode', v),
})

const toolAgentModel = computed({
  get: () => props.enableToolAgent,
  set: (v: boolean) => emit('update:enableToolAgent', v),
})

const selectedDs = computed(() =>
  props.datasources.find((d) => d.id === props.selectedDsId)
)
</script>

<template>
  <header class="chat-header">
    <div class="left-block">
      <el-button class="new-btn" :icon="Plus" round @click="emit('create')">
        {{ $t('chat.new_conversation') }}
      </el-button>
    </div>

    <div class="center-block">
      <div class="ds-selector" :class="{ disabled: datasourceLocked }">
        <el-icon :size="14" class="ds-icon"><Connection /></el-icon>
        <el-select
          v-model="dsModel"
          :placeholder="$t('chat.select_datasource')"
          :disabled="datasourceLocked"
          size="default"
          filterable
          class="ds-select"
        >
          <el-option
            v-for="ds in datasources"
            :key="ds.id"
            :label="ds.name"
            :value="ds.id"
          >
            <span style="float: left">{{ ds.name }}</span>
            <span class="ds-option-type">{{ ds.type }}</span>
          </el-option>
        </el-select>
        <el-icon v-if="!datasourceLocked" :size="12" class="caret"><ArrowDown /></el-icon>
      </div>

      <el-segmented
        v-model="modeModel"
        :options="[
          { label: $t('chat.agent_mode.team'), value: 'team' },
          { label: $t('chat.agent_mode.agent'), value: 'agent' },
        ]"
        class="mode-tab"
      />

      <div class="tool-toggle">
        <span class="toggle-label">{{ $t('chat.tool_agent_toggle') }}</span>
        <el-switch
          v-model="toolAgentModel"
          size="small"
          :disabled="modeModel !== 'team'"
          inline-prompt
          :active-text="$t('chat.enabled')"
          :inactive-text="$t('chat.disabled')"
        />
      </div>
    </div>

    <div class="right-block">
      <el-tooltip v-if="hasMessages" content="滚动到底部" placement="bottom">
        <button class="scroll-btn" @click="emit('scrollBottom')">
          <el-icon :size="14"><Bottom /></el-icon>
        </button>
      </el-tooltip>
      <span v-if="selectedDs" class="ds-pill">
        <i class="dot" />
        {{ selectedDs.type }} · {{ selectedDs.name }}
      </span>
    </div>
  </header>
</template>

<style lang="less" scoped>
.chat-header {
  height: 54px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 16px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: saturate(180%) blur(8px);
  border-bottom: 1px solid var(--border-color);
  box-shadow: 0 1px 0 rgba(16, 24, 40, 0.03);
  z-index: 5;

  .left-block {
    display: flex;
    align-items: center;
    gap: 8px;

    .new-btn {
      height: 34px;
      border-radius: 16px;
      font-size: 13px;
      font-weight: 500;
      --el-button-bg-color: var(--el-color-primary-light-9);
      --el-button-border-color: var(--el-color-primary-light-7);
      --el-button-text-color: var(--el-color-primary);
      --el-button-hover-bg-color: var(--el-color-primary-light-8);
      --el-button-hover-border-color: var(--el-color-primary-light-5);
      --el-button-hover-text-color: var(--el-color-primary);
    }
  }

  .center-block {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
    justify-content: center;

    .ds-selector {
      display: flex;
      align-items: center;
      gap: 4px;
      height: 34px;
      padding: 0 8px;
      background: #fff;
      border: 1px solid var(--border-color);
      border-radius: 16px;
      transition: border-color 0.15s ease;
      min-width: 0;
      max-width: 360px;

      &:hover:not(.disabled) {
        border-color: var(--el-color-primary-light-5);
      }

      &.disabled {
        background: #f5f7fb;
      }

      .ds-icon {
        color: var(--el-color-primary);
      }

      .ds-select {
        width: clamp(120px, 16vw, 186px);

        :deep(.el-input__wrapper) {
          background: transparent;
          box-shadow: none !important;
          padding: 0 4px;
          font-weight: 500;
        }

        :deep(.el-input__suffix) {
          display: none;
        }
      }

      .caret {
        color: #98a2b3;
      }
    }

    .mode-tab {
      flex-shrink: 0;
      min-width: 170px;
      :deep(.el-segmented) {
        background: linear-gradient(180deg, #f5f7fb 0%, #eef2f7 100%);
        border: 1px solid #e4e7ec;
        border-radius: 16px;
        padding: 2px;
      }
      :deep(.el-segmented__item-selected) {
        z-index: 0;
        border-radius: 14px;
        background: linear-gradient(180deg, #ffffff 0%, #f8faff 100%);
        box-shadow:
          0 1px 2px rgba(16, 24, 40, 0.06),
          0 0 0 1px rgba(59, 130, 246, 0.12) inset;
      }
      :deep(.el-segmented__item) {
        position: relative;
        z-index: 1;
        border-radius: 14px;
        height: 30px;
        padding: 0 12px;
        font-size: 12.5px;
        color: #667085;
        transition: color 0.15s ease;
      }
      :deep(.el-segmented__item:hover) {
        color: #344054;
      }
      :deep(.el-segmented__item-label) {
        position: relative;
        z-index: 2;
      }
      :deep(.el-segmented__item.is-selected) {
        background: transparent;
        color: #175cd3;
        font-weight: 600;
        text-shadow: 0 0 0 rgba(0, 0, 0, 0);
      }
    }

    .tool-toggle {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 34px;
      padding: 0 10px;
      background: #fff;
      border: 1px solid var(--border-color);
      border-radius: 16px;
      flex-shrink: 0;

      .toggle-label {
        font-size: 12px;
        color: #475467;
      }
    }
  }

  .right-block {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 140px;
    justify-content: flex-end;

    .scroll-btn {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
      background: #fff;
      color: #475467;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;

      &:hover {
        border-color: var(--el-color-primary-light-5);
        color: var(--el-color-primary);
        background: var(--el-color-primary-light-9);
      }
    }

    .ds-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 28px;
      padding: 0 10px;
      font-size: 12px;
      color: #475467;
      background: #f5f7fb;
      border-radius: 12px;

      .dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #16a34a;
      }
    }
  }
}

.ds-option-type {
  float: right;
  font-size: 12px;
  color: #98a2b3;
  text-transform: uppercase;
}
</style>
