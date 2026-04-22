import { BaseG2Chart } from '@/views/chat/component/BaseG2Chart'
import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import type { G2Spec } from '@antv/g2'
import { checkIsPercent, getAxesWithFilter } from '@/views/chat/component/charts/utils'

export class Bar extends BaseG2Chart {
  constructor(id: string) {
    super(id, 'bar')
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    super.init(axis, data)
    const { x, y, series } = getAxesWithFilter(this.axis)
    if (x.length === 0 || y.length === 0) return

    const _data = checkIsPercent(y, data)
    const options: G2Spec = {
      ...this.chart.options(),
      type: 'interval',
      data: _data.data,
      coordinate: { transform: [{ type: 'transpose' }] },
      encode: {
        x: x[0].value,
        y: y[0].value,
        color: series.length > 0 ? series[0].value : undefined,
      },
      style: {
        radiusTopLeft: 4,
        radiusTopRight: 4,
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
        y: {
          title: false,
          labelFontSize: 12,
          labelAutoHide: { type: 'hide', keepHeader: true },
          labelAutoRotate: false,
          labelAutoWrap: true,
          labelAutoEllipsis: true,
        },
      },
      scale: {
        x: { nice: true },
        y: { nice: true, type: 'linear' },
      },
      interaction: {
        elementHighlight: { background: true, region: true },
        tooltip: { series: series.length > 0, shared: true },
      },
      tooltip: (d: any) => ({
        name: series.length > 0 ? d[series[0].value] : y[0].name,
        value: `${d[y[0].value]}${_data.isPercent ? '%' : ''}`,
      }),
      labels: this.showLabel
        ? [
            {
              text: (d: any) => {
                const v = d[y[0].value]
                if (v === undefined || v === null) return ''
                return `${v}${_data.isPercent ? '%' : ''}`
              },
              position: 'right',
              transform: [
                { type: 'contrastReverse' },
                { type: 'exceedAdjust' },
                { type: 'overlapHide' },
              ],
            },
          ]
        : [],
    } as G2Spec

    if (series.length > 0) {
      options.transform = [{ type: 'stackY' }]
    }

    this.chart.options(options)
  }
}
