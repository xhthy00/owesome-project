<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { getChartInstance } from '@/views/chat/component/index'
import type { BaseChart, ChartTypes } from '@/views/chat/component/BaseChart'
import { inferAxes } from '@/views/chat/component/charts/utils'

const props = withDefaults(
  defineProps<{
    id: string | number
    type: Exclude<ChartTypes, 'table'>
    columns: string[]
    rows: any[][]
    showLabel?: boolean
  }>(),
  { showLabel: false }
)

const chartId = computed(() => `chart-component-${props.id}-${props.type}`)

let instance: BaseChart | undefined

function render() {
  destroy()
  if (!props.columns?.length || !props.rows?.length) return

  const { axes, data } = inferAxes(props.columns, props.rows, props.type)
  instance = getChartInstance(props.type, chartId.value)
  if (!instance) return
  instance.showLabel = props.showLabel
  instance.init(axes, data)
  instance.render()
}

function destroy() {
  try {
    instance?.destroy()
  } catch {
    // g2 may throw if container already unmounted; safe to ignore
  }
  instance = undefined
}

watch(
  () => [props.type, props.columns, props.rows, props.showLabel],
  () => nextTick(render),
  { deep: true }
)

onMounted(() => {
  nextTick(render)
})

onBeforeUnmount(destroy)
</script>

<template>
  <div :id="chartId" class="chart-container" />
</template>

<style scoped lang="less">
.chart-container {
  width: 100%;
  height: 100%;
  min-height: 320px;
}
</style>
