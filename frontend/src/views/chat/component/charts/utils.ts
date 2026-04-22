import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import { endsWith, filter, replace } from 'lodash-es'

interface CheckedData {
  isPercent: boolean
  data: Array<ChartData>
}

export function getAxesWithFilter(axes: ChartAxis[]): {
  x: ChartAxis[]
  y: ChartAxis[]
  series: ChartAxis[]
  multiQuota: string[]
  multiQuotaName?: string
} {
  const groups = {
    x: [] as ChartAxis[],
    y: [] as ChartAxis[],
    series: [] as ChartAxis[],
    multiQuota: [] as string[],
    multiQuotaName: undefined as string | undefined,
  }

  axes.forEach((axis) => {
    if (axis.type === 'x') groups.x.push(axis)
    else if (axis.type === 'y') groups.y.push(axis)
    else if (axis.type === 'series') groups.series.push(axis)
    else if (axis.type === 'other-info') groups.multiQuotaName = axis.value
  })

  if (groups.series.length > 0) {
    groups.y = groups.y.slice(0, 1)
  } else {
    const multiQuotaY = groups.y.filter((item) => item['multi-quota'] === true)
    groups.multiQuota = multiQuotaY.map((item) => item.value)
    if (multiQuotaY.length > 0) {
      groups.y = multiQuotaY
    }
  }

  return groups
}

export function checkIsPercent(valueAxes: Array<ChartAxis>, data: Array<ChartData>): CheckedData {
  const result: CheckedData = { isPercent: false, data: [] }
  for (let i = 0; i < data.length; i++) result.data.push({ ...data[i] })

  for (const valueAxis of valueAxes) {
    const notEmpty = filter(
      data,
      (d) =>
        d &&
        d[valueAxis.value] !== null &&
        d[valueAxis.value] !== undefined &&
        d[valueAxis.value] !== '' &&
        d[valueAxis.value] !== 0 &&
        d[valueAxis.value] !== '0'
    )
    if (notEmpty.length > 0) {
      const v = notEmpty[0][valueAxis.value] + ''
      if (endsWith(v.trim(), '%')) {
        result.isPercent = true
        break
      }
    }
  }

  if (result.isPercent) {
    for (let i = 0; i < data.length; i++) {
      for (const valueAxis of valueAxes) {
        const value = data[i][valueAxis.value]
        if (value !== null && value !== undefined && value !== '') {
          const strValue = String(value).trim()
          if (endsWith(strValue, '%')) {
            const num = Number(replace(strValue, '%', ''))
            result.data[i][valueAxis.value] = isNaN(num) ? 0 : num
          }
        }
      }
    }
  }

  return result
}

/**
 * Infer (x/y/series) axes from a raw {columns, rows} query result.
 * Heuristic:
 *   - First non-numeric column      → x
 *   - Remaining numeric columns     → y (stacked if multiple)
 *   - Second non-numeric column     → series (if present)
 * For pie chart, the x-axis acts as the `series` (category).
 */
export function inferAxes(
  columns: string[],
  rows: any[][],
  chartType: 'column' | 'bar' | 'line' | 'pie'
): { axes: ChartAxis[]; data: ChartData[] } {
  const data: ChartData[] = rows.map((row) => {
    const obj: ChartData = {}
    columns.forEach((c, i) => (obj[c] = row[i]))
    return obj
  })

  const isNumericColumn = (col: string): boolean => {
    for (const row of data) {
      const v = row[col]
      if (v === null || v === undefined || v === '') continue
      const n = Number(String(v).replace('%', ''))
      if (Number.isNaN(n)) return false
    }
    return data.length > 0
  }

  const numericCols = columns.filter(isNumericColumn)
  const categoricalCols = columns.filter((c) => !isNumericColumn(c))

  const axes: ChartAxis[] = []

  if (chartType === 'pie') {
    const series = categoricalCols[0] ?? columns[0]
    const value = numericCols[0] ?? columns[columns.length - 1]
    axes.push({ name: series, value: series, type: 'series' })
    axes.push({ name: value, value: value, type: 'y' })
    return { axes, data }
  }

  const xCol = categoricalCols[0] ?? columns[0]
  axes.push({ name: xCol, value: xCol, type: 'x' })

  const yCols = numericCols.length ? numericCols : columns.filter((c) => c !== xCol)
  yCols.forEach((c, idx) =>
    axes.push({
      name: c,
      value: c,
      type: 'y',
      'multi-quota': yCols.length > 1 && idx < yCols.length,
    })
  )

  const secondCat = categoricalCols.find((c) => c !== xCol)
  if (secondCat && yCols.length === 1) {
    axes.push({ name: secondCat, value: secondCat, type: 'series' })
  }

  return { axes, data }
}
