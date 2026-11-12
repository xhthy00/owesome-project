import type { EChartsOption } from "echarts";
import { labelColumn } from "@/utils/columnLabels";
import { formatNumericDisplay } from "@/utils/formatNumericDisplay";
import { axisOption, baseChartOption, chartTheme, valueAxisOption } from "./chartTheme";
import { toNumber, type ChartSpec } from "./chartSpec";

function toLabel(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

function dataFor(spec: ChartSpec, rows: unknown[][], columns: string[]) {
  if (spec.dataset.rows?.length) {
    return {
      rows: spec.dataset.rows,
      columns: spec.dataset.columns.map((column) => column.name)
    };
  }
  return { rows, columns };
}

function transformRows(spec: ChartSpec, columns: string[], rows: unknown[][]): unknown[][] {
  const transform = spec.transform;
  let next = [...rows];
  if (transform?.sort) {
    const index = columns.indexOf(transform.sort.by);
    if (index >= 0) {
      const numeric = next.every((row) => toNumber(row[index]) != null);
      next.sort((left, right) => {
        const a = numeric ? toNumber(left[index]) ?? 0 : toLabel(left[index]);
        const b = numeric ? toNumber(right[index]) ?? 0 : toLabel(right[index]);
        const result = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "zh-CN");
        return transform.sort?.order === "asc" ? result : -result;
      });
    }
  }
  if (transform?.topN && transform.topN > 0) next = next.slice(0, transform.topN);
  return next;
}

function formatValue(value: unknown, unit: string): string {
  const text = formatNumericDisplay(value);
  if (!text) return text;
  if (unit === "%" && !text.endsWith("%")) return `${text}%`;
  return text;
}

function markLineFor(spec: ChartSpec) {
  const lines = spec.annotations?.referenceLines || [];
  if (!lines.length) return undefined;
  return {
    silent: true,
    symbol: "none",
    label: { color: chartTheme().muted, fontSize: 10 },
    lineStyle: { type: "dashed", color: "#d97706" },
    data: lines
      .filter((line) => line.value != null)
      .map((line) => ({
        [line.axis === "x" ? "xAxis" : "yAxis"]: line.value,
        label: { formatter: line.label || String(line.value) }
      }))
  };
}

/** ChartSpec → ECharts option。P0 只处理基础图形，未知类型返回 null。 */
export function buildChartOption(
  spec: ChartSpec,
  rows: unknown[][],
  columns: string[]
): EChartsOption | null {
  if (spec.type === "kpi" || spec.type === "table") return null;
  const data = dataFor(spec, rows, columns);
  if (!data.rows.length || !data.columns.length) return null;
  const prepared = transformRows(spec, data.columns, data.rows);
  const base = baseChartOption();
  const theme = chartTheme();
  const xName = spec.encoding.x;
  const yNames = (spec.encoding.y || []).filter((name) => data.columns.includes(name));
  if (!yNames.length) return null;
  const unit = spec.unit || "";

  if (spec.type === "donut" || spec.type === "pie") {
    const yName = yNames[0];
    const yIndex = data.columns.indexOf(yName);
    const xIndex = xName ? data.columns.indexOf(xName) : -1;
    const values = prepared
      .map((row) => ({
        name: xIndex >= 0 ? toLabel(row[xIndex]) : "",
        value: toNumber(row[yIndex]) ?? 0
      }))
      .filter((item) => item.value != null);
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: "item" },
      legend: { ...base.legend, type: "scroll", orient: "horizontal" },
      series: [
        {
          type: "pie",
          radius: spec.type === "donut" ? ["42%", "68%"] : "62%",
          center: ["50%", "54%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: theme.tooltipBg, borderWidth: 2, borderRadius: 4 },
          label: {
            show: spec.options?.showLabel !== false,
            formatter: `{b}\n{d}%`,
            color: theme.muted,
            fontSize: 10
          },
          data: values
        }
      ]
    };
  }

  if (!xName || !data.columns.includes(xName)) return null;
  const xIndex = data.columns.indexOf(xName);
  const labels = prepared.map((row) => toLabel(row[xIndex]));
  const isBar = spec.type === "bar";
  const isLine = spec.type === "line" || spec.type === "area";
  // 类别过多时逐个标数值会糊成一片(86 根柱子的数字会互相压住),此时只保留悬浮提示
  const dense = prepared.length > 12;
  const showLabel = Boolean(spec.options?.showLabel) && !dense;
  const series = yNames.map((yName, index) => {
    const yIndex = data.columns.indexOf(yName);
    const values = prepared.map((row) => toNumber(row[yIndex]));
    const item: Record<string, unknown> = {
      name: labelColumn(yName),
      type: isLine ? "line" : "bar",
      data: values,
      barMaxWidth: 42,
      symbol: isLine ? "circle" : undefined,
      symbolSize: isLine ? 6 : undefined,
      smooth: Boolean(spec.options?.smooth),
      lineStyle: isLine ? { width: 2 } : undefined,
      areaStyle:
        spec.type === "area"
          ? { opacity: 0.16 }
          : undefined,
      stack: spec.type === "stackedColumn" ? "total" : undefined,
      label: showLabel
        ? {
            show: true,
            position: isBar ? "right" : "top",
            color: theme.muted,
            fontSize: 10,
            formatter: (params: { value?: unknown }) => formatValue(params.value, unit)
          }
        : { show: false }
    };
    if (index === 0) {
      const markLine = markLineFor(spec);
      if (markLine) item.markLine = markLine;
    }
    return item;
  });

  const yAxis = valueAxisOption() as Record<string, unknown>;
  if (spec.options?.yStartAtZero) yAxis.min = 0;
  const allValues = series.flatMap((item) =>
    Array.isArray(item.data) ? (item.data as unknown[]) : []
  );
  if (allValues.length && allValues.every((value) => Number.isInteger(value))) {
    yAxis.minInterval = 1;
  }
  const xAxis = axisOption(true) as Record<string, unknown>;
  if (labels.length > 8) xAxis.axisLabel = { ...(xAxis.axisLabel as object), rotate: 30 };
  xAxis.data = labels;

  return {
    ...base,
    ...(isLine && labels.length > 20
      ? { dataZoom: [{ type: "inside", start: 0, end: 100 }] }
      : {}),
    tooltip: {
      ...base.tooltip,
      trigger: "axis",
      valueFormatter: (value: unknown) => formatValue(value, unit)
    },
    legend: { ...base.legend, show: yNames.length > 1 },
    grid: { ...(base.grid as object), top: yNames.length > 1 ? 38 : 18 },
    xAxis: isBar ? valueAxisOption() : xAxis,
    yAxis: isBar
      ? { ...axisOption(true), inverse: true, data: labels }
      : yAxis,
    series: (isBar
      ? series.map((item) => ({ ...item, label: { ...(item.label as object), position: "right" } }))
      : series) as EChartsOption["series"]
  };
}
