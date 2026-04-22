import { BaseG2Chart } from '@/views/chat/component/BaseG2Chart'
import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import type { G2Spec } from '@antv/g2'
import { checkIsPercent, getAxesWithFilter } from '@/views/chat/component/charts/utils'

export class Line extends BaseG2Chart {
  constructor(id: string) {
    super(id, 'line')
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    super.init(axis, data)
    const { x, y, series } = getAxesWithFilter(this.axis)
    if (x.length === 0 || y.length === 0) return

    const _data = checkIsPercent(y, data)
    const options: G2Spec = {
      ...this.chart.options(),
      type: 'view',
      data: _data.data,
      encode: {
        x: x[0].value,
        y: y[0].value,
        color: series.length > 0 ? series[0].value : undefined,
      },
      axis: {
        x: {
          title: false,
          labelFontSize: 12,
          labelAutoHide: { type: 'hide', keepHeader: true, keepTail: true },
          labelAutoRotate: false,
          labelAutoWrap: true,
          labelAutoEllipsis: true,
        },
        y: { title: false },
      },
      scale: {
        x: { nice: true },
        y: { nice: true, type: 'linear' },
      },
      children: [
        {
          type: 'line',
          encode: { shape: 'smooth' },
          labels: this.showLabel
            ? [
                {
                  text: (d: any) => {
                    const v = d[y[0].value]
                    if (v === undefined || v === null) return ''
                    return `${v}${_data.isPercent ? '%' : ''}`
                  },
                  style: { dx: -10, dy: -12 },
                  transform: [
                    { type: 'contrastReverse' },
                    { type: 'exceedAdjust' },
                    { type: 'overlapHide' },
                  ],
                },
              ]
            : [],
          tooltip: (d: any) => ({
            name: series.length > 0 ? d[series[0].value] : y[0].name,
            value: `${d[y[0].value]}${_data.isPercent ? '%' : ''}`,
          }),
        },
        {
          type: 'point',
          style: { fill: 'white' },
          encode: { size: 1.5 },
          tooltip: false,
        },
      ],
    } as G2Spec

    this.chart.options(options)
  }
}
