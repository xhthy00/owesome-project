<script lang="ts" setup>
import { ref, computed } from 'vue'
import { Promotion, Connection } from '@element-plus/icons-vue'
import type { DatasourceItem } from '@/api/datasource'

const props = defineProps<{
  disabled?: boolean
  placeholder?: string
  datasources: DatasourceItem[]
  modelValue?: number
  datasourceLocked?: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
  'update:modelValue': [v: number | undefined]
}>()

const text = ref('')
const inputRef = ref()

const selected = computed({
  get: () => props.modelValue,
  set: (v: number | undefined) => emit('update:modelValue', v),
})

const selectedDs = computed(() =>
  props.datasources.find((d) => d.id === props.modelValue)
)

const canSend = computed(() => !!text.value.trim() && !props.disabled && !!selected.value)

const onSend = () => {
  if (!canSend.value) return
  emit('send', text.value.trim())
  text.value = ''
}

const onKeydown = (evt: Event) => {
  const e = evt as KeyboardEvent
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.isComposing) {
    e.preventDefault()
    onSend()
  }
}

const focus = () => inputRef.value?.focus?.()
</script>

<template>
  <div class="chat-footer">
    <div class="input-wrapper" @click="focus">
      <div class="datasource-bar">
        <template v-if="selectedDs">
          <span class="label">{{ $t('chat.selected_datasource') }}：</span>
          <el-icon :size="14" class="ds-icon"><Connection /></el-icon>
          <span class="name ellipsis">{{ selectedDs.name }}</span>
        </template>
        <template v-else>
          <el-select
            v-model="selected"
            :placeholder="$t('chat.select_datasource')"
            :disabled="datasourceLocked"
            size="small"
            class="ds-select"
          >
            <el-option
              v-for="ds in datasources"
              :key="ds.id"
              :label="ds.name"
              :value="ds.id"
            />
          </el-select>
        </template>
      </div>

      <el-input
        ref="inputRef"
        v-model="text"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 8 }"
        resize="none"
        class="input-area"
        :placeholder="placeholder || $t('chat.input_placeholder')"
        :disabled="disabled"
        @keydown="onKeydown"
      />

      <el-button
        circle
        type="primary"
        class="input-icon"
        :disabled="!canSend"
        @click.stop="onSend"
      >
        <el-icon :size="16"><Promotion /></el-icon>
      </el-button>
    </div>
  </div>
</template>

<style lang="less" scoped>
.chat-footer {
  min-height: calc(120px + 16px);
  max-height: calc(300px + 16px);
  height: fit-content;
  padding: 0 56px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;

  .input-wrapper {
    width: 100%;
    max-width: 800px;
    position: relative;
    background: #f8f9fa;
    border: 1px solid #d9dcdf;
    border-radius: 16px;
    padding: 12px 12px 52px 12px;

    &:focus-within {
      border-color: var(--el-color-primary);
    }

    .datasource-bar {
      display: flex;
      align-items: center;
      line-height: 22px;
      font-size: 13px;
      font-weight: 400;
      color: #646a73;
      margin-bottom: 8px;
      min-height: 22px;

      .label {
        color: #646a73;
      }

      .ds-icon {
        margin-right: 4px;
        color: var(--el-color-primary);
      }

      .name {
        color: #1f2329;
        font-weight: 500;
        max-width: 240px;
      }

      .ds-select {
        width: 220px;

        :deep(.el-input__wrapper) {
          background: transparent;
          box-shadow: none !important;
          padding-left: 0;
        }
      }
    }

    .input-area {
      :deep(.el-textarea__inner) {
        background: transparent;
        border: none;
        box-shadow: none !important;
        padding: 0;
        font-size: 14px;
        line-height: 22px;
        color: #1f2329;
        resize: none;

        &::placeholder {
          color: #8f959e;
        }
      }
    }

    .input-icon {
      position: absolute;
      bottom: 12px;
      right: 12px;
      width: 32px;
      height: 32px;
      min-width: unset;
    }
  }
}
</style>
