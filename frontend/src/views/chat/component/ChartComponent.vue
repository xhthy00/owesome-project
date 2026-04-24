<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { getChartInstance } from '@/views/chat/component/index'
import type { BaseChart, ChartAxis, ChartTypes } from '@/views/chat/component/BaseChart'
import { inferAxes } from '@/views/chat/component/charts/utils'

const props = withDefaults(
  defineProps<{
    id: string | number
    type: Exclude<ChartTypes, 'table'>
    columns: string[]
    rows: any[][]
    showLabel?: boolean
    /**
     * 可选：后端 Charter 推荐的图表轴配置。传了就**优先**用它，
     * 没传或转换结果为空再 fall back 到启发式 inferAxes。
     * 原始契约：`{x: string, y: string[], title?: string}`
     */
    axesOverride?: ChartAxis[]
  }>(),
  { showLabel: false }
)

const chartId = computed(() => `chart-component-${props.id}-${props.type}`)

let instance: BaseChart | undefined

function render() {
  destroy()
  if (!props.columns?.length || !props.rows?.length) return

  // 先用后端 axes（若传且 columns 里都存在），否则前端启发式推断。
  const overrideRaw = (props.axesOverride || []).filter((a) =>
    a && a.value && props.columns.includes(a.value)
  )
  const { axes: inferred, data } = inferAxes(props.columns, props.rows, props.type)
  const hasX = overrideRaw.some((a) => a.type === 'x')
  const hasY = overrideRaw.some((a) => a.type === 'y')
  const hasSeries = overrideRaw.some((a) => a.type === 'series')
  const overrideUsable =
    props.type === 'pie' ? hasSeries && hasY : hasX && hasY
  const axes = overrideUsable ? overrideRaw : inferred

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
  () => [props.type, props.columns, props.rows, props.showLabel, props.axesOverride],
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
