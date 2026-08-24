import { useEffect, useMemo, useRef } from "react";
import { Chart } from "@antv/g2";

export type G2ChartType = "column" | "bar" | "line" | "pie";

type Props = {
  type: G2ChartType;
  columns: string[];
  rows: unknown[][];
  showLabel?: boolean;
  /** Charter / 调用方指定的 X 轴列；列存在时优先于启发式 */
  xField?: string;
  /** Charter / 调用方指定的 Y 轴列；列存在时优先于启发式 */
  yField?: string;
  /** 单色柱/条，关闭按类目着色与图例 */
  accentColor?: string;
  /** 数值标签后缀，如 % */
  valueSuffix?: string;
  height?: number;
};

/** 数值列打分：优先画率/占比，避免默认落到「参考人数」。 */
export function scoreYColumn(name: string): number {
  const n = name.toLowerCase();
  if (/率|%|占比|比例|reach_rate/.test(n)) return 3;
  if (/达线人数|reached/.test(n)) return 2;
  if (/参考人数|candidates/.test(n)) return 0;
  if (/人数|count/.test(n) && !/达线/.test(n)) return 0;
  return 1;
}

export function pickYField(candidates: string[]): string | undefined {
  if (!candidates.length) return undefined;
  return [...candidates].sort((a, b) => {
    const diff = scoreYColumn(b) - scoreYColumn(a);
    if (diff !== 0) return diff;
    return candidates.indexOf(a) - candidates.indexOf(b);
  })[0];
}

const isNumericColumn = (col: string, data: Record<string, unknown>[]): boolean => {
  if (!data.length) return false;
  for (const row of data) {
    const v = row[col];
    if (v === null || v === undefined || v === "") continue;
    const n = Number(String(v).replace("%", ""));
    if (Number.isNaN(n)) return false;
  }
  return true;
};

export function inferChartFields(
  columns: string[],
  rows: unknown[][],
  overrides?: { xField?: string; yField?: string }
): { xField: string; yField: string } {
  if (!columns.length) return { xField: "", yField: "" };
  const data = rows.map((row) =>
    Object.fromEntries(columns.map((col, idx) => [col, (row as unknown[])[idx]]))
  ) as Record<string, unknown>[];
  const numericCols = columns.filter((col) => isNumericColumn(col, data));
  const categoricalCols = columns.filter((col) => !isNumericColumn(col, data));
  const hasOnlyNumericCols = categoricalCols.length === 0 && numericCols.length > 0;
  if (hasOnlyNumericCols && data.length === 1) {
    return { xField: "指标", yField: "数值" };
  }
  const overrideX =
    overrides?.xField && columns.includes(overrides.xField) ? overrides.xField : undefined;
  const overrideY =
    overrides?.yField && columns.includes(overrides.yField) ? overrides.yField : undefined;
  const xField = overrideX ?? categoricalCols[0] ?? columns[0];
  const yCandidates = (numericCols.length ? numericCols : columns).filter((col) => col !== xField);
  const yField = overrideY ?? pickYField(yCandidates) ?? columns[1] ?? columns[0];
  return { xField, yField };
}

/** 查询集下拉：用「达线率（按选科方向）」这类可读名，避免堆列名。 */
export function formatQuerySetLabel(opts: {
  xField?: string;
  yField?: string;
  chartTitle?: string;
  isFinal: boolean;
  index: number;
  total: number;
}): string {
  const title = opts.chartTitle?.trim();
  const chartName =
    title ||
    (opts.yField && opts.xField && opts.xField !== opts.yField
      ? `${opts.yField}（按${opts.xField}）`
      : opts.yField) ||
    "数据图";
  if (opts.total <= 1) return chartName;
  const prefix = opts.isFinal ? "最终" : `查询 ${opts.index + 1}`;
  return `${prefix} · ${chartName}`;
}

const toNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

export default function G2Chart({
  type,
  columns,
  rows,
  showLabel = false,
  xField: xFieldOverride,
  yField: yFieldOverride,
  accentColor,
  valueSuffix = "",
  height = 320
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

  const data = useMemo(() => {
    if (!columns.length || !rows.length) return [];
    return rows.map((row) =>
      Object.fromEntries(columns.map((col, idx) => [col, row[idx]]))
    ) as Record<string, unknown>[];
  }, [columns, rows]);
  const inferred = useMemo(() => {
    if (!columns.length || !data.length) {
      return { xField: "", yField: "", chartData: [] as Record<string, unknown>[] };
    }
    const { xField, yField } = inferChartFields(columns, rows, {
      xField: xFieldOverride,
      yField: yFieldOverride
    });
    const numericCols = columns.filter((col) => isNumericColumn(col, data));
    const categoricalCols = columns.filter((col) => !isNumericColumn(col, data));
    if (categoricalCols.length === 0 && numericCols.length > 0 && data.length === 1) {
      const chartData: Record<string, unknown>[] = numericCols.map((col) => ({
        [xField]: col,
        [yField]: data[0][col]
      }));
      return { xField, yField, chartData };
    }
    return { xField, yField, chartData: data };
  }, [columns, data, rows, xFieldOverride, yFieldOverride]);

  useEffect(() => {
    if (!containerRef.current) return;
    if (!inferred.chartData.length) return;
    const { xField, yField, chartData } = inferred;
    const dashboard = Boolean(accentColor) && type !== "line" && type !== "pie";

    const yValues = chartData.map((item) => toNumber(item[yField]) ?? 0);
    const yMax = Math.max(0, ...yValues);
    const domainMax =
      yMax <= 0 ? 10 : Math.min(valueSuffix === "%" ? 100 : Number.POSITIVE_INFINITY, Math.ceil((yMax + 4) / 5) * 5);

    const chart = new Chart({
      container: containerRef.current,
      autoFit: true,
      height,
      ...(dashboard
        ? {
            marginTop: 0,
            paddingTop: 0,
            paddingBottom: 8,
            insetTop: showLabel ? 24 : 4
          }
        : {})
    });
    chartRef.current = chart;
    chart.theme({
      type: "classic",
      axis: {
        labelFill: dashboard ? "#475569" : "#9ca3af",
        titleFill: dashboard ? "#94a3b8" : "#9ca3af",
        labelFontSize: 12
      }
    });

    if (type === "pie") {
      const pieData = chartData
        .map((item) => ({
          category: String(item[xField] ?? ""),
          value: toNumber(item[yField]) ?? 0
        }))
        .filter((item) => item.category);
      chart.coordinate({ type: "theta" });
      chart
        .interval()
        .data(pieData)
        .transform({ type: "stackY" })
        .encode("y", "value")
        .encode("color", "category")
        .label({ text: "value", style: { fill: "#111827", fontSize: 11 } });
    } else {
      const mark = type === "line" ? chart.line() : chart.interval();
      mark.data(chartData).encode("x", xField).encode("y", yField);
      mark.tooltip({ title: xField, items: [yField] });
      if (dashboard) {
        chart.legend(false);
        mark.legend(false);
        mark.style("fill", accentColor);
        mark.scale("y", { domainMin: 0, domainMax, nice: false });
        mark.scale("x", { padding: 0.12 });
        mark.axis("x", { title: false, labelFill: "#475569", labelFontSize: 12 });
        mark.axis("y", {
          title: false,
          labelFill: "#64748b",
          labelFontSize: 11,
          grid: true,
          gridStroke: "#e8eef5"
        });
        mark.style("maxWidth", 120);
        mark.style("minWidth", 48);
        mark.style("radiusTopLeft", 6);
        mark.style("radiusTopRight", 6);
      } else {
        mark.encode("color", xField);
        mark.axis("y", { title: yField });
        mark.legend("color", { title: false });
      }
      if (showLabel) {
        mark.label({
          text: (d: Record<string, unknown>) => {
            const n = toNumber(d[yField]);
            return n == null ? "" : `${n}${valueSuffix}`;
          },
          dy: dashboard ? -12 : 0,
          style: dashboard
            ? {
                fill: "#0f172a",
                fontSize: 16,
                fontWeight: 700,
                stroke: "#ffffff",
                lineWidth: 4,
                paintOrder: "stroke"
              }
            : { fill: "#111827", fontSize: 11 }
        });
      }
    }

    chart.render();

    return () => {
      chart.destroy();
      chartRef.current = null;
    };
  }, [inferred, type, showLabel, accentColor, valueSuffix, height]);

  useEffect(() => {
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, []);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
