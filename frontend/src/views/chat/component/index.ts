import { BaseChart } from '@/views/chat/component/BaseChart'
import { Bar } from '@/views/chat/component/charts/Bar'
import { Column } from '@/views/chat/component/charts/Column'
import { Line } from '@/views/chat/component/charts/Line'
import { Pie } from '@/views/chat/component/charts/Pie'

type ChartCtor = new (id: string) => BaseChart

const CHART_TYPE_MAP: Record<string, ChartCtor> = {
  column: Column,
  bar: Bar,
  line: Line,
  pie: Pie,
}

export function getChartInstance(type: string, id: string): BaseChart | undefined {
  const Ctor = CHART_TYPE_MAP[type]
  return Ctor ? new Ctor(id) : undefined
}
