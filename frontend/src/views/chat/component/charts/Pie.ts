import { BaseG2Chart } from '@/views/chat/component/BaseG2Chart'
import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import type { G2Spec } from '@antv/g2'
import { checkIsPercent, getAxesWithFilter } from '@/views/chat/component/charts/utils'

export class Pie extends BaseG2Chart {
  constructor(id: string) {
    super(id, 'pie')
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    super.init(axis, data)
    const { y, series } = getAxesWithFilter(this.axis)
    if (series.length === 0 || y.length === 0) return

    const _data = checkIsPercent(y, data)
    const options: G2Spec = {
      ...this.chart.options(),
      type: 'interval',
      coordinate: { type: 'theta', outerRadius: 0.8 },
      transform: [{ type: 'stackY' }],
      data: _data.data,
      encode: {
        y: y[0].value,
        color: series[0].value,
      },
      scale: {
        x: { nice: true },
        y: { type: 'linear' },
      },
      legend: {
        color: { position: 'bottom', layout: { justifyContent: 'center' } },
      },
      animate: { enter: { type: 'waveIn' } },
      labels: this.showLabel
        ? [
            {
              position: 'spider',
              text: (d: any) =>
                `${d[series[0].value]}: ${d[y[0].value]}${_data.isPercent ? '%' : ''}`,
            },
          ]
        : [],
      tooltip: {
        title: (d: any) => d[series[0].value],
        items: [
          (d: any) => ({
            name: y[0].name,
            value: `${d[y[0].value]}${_data.isPercent ? '%' : ''}`,
          }),
        ],
      },
    }

    this.chart.options(options)
  }
}
