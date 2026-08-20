import { useEffect, useMemo, useRef } from "react";
import { Chart } from "@antv/g2";

export type G2ChartType = "column" | "bar" | "line" | "pie";

type Props = {
  type: G2ChartType;
  columns: string[];
  rows: unknown[][];
  showLabel?: boolean;
  /** 单色柱/条，关闭按类目着色与图例 */
  accentColor?: string;
  /** 数值标签后缀，如 % */
  valueSuffix?: string;
  height?: number;
};

const toNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

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

export default function G2Chart({
  type,
  columns,
  rows,
  showLabel = false,
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
    const numericCols = columns.filter((col) => isNumericColumn(col, data));
    const categoricalCols = columns.filter((col) => !isNumericColumn(col, data));
    const hasOnlyNumericCols = categoricalCols.length === 0 && numericCols.length > 0;
    if (hasOnlyNumericCols && data.length === 1) {
      const metricCol = "指标";
      const valueCol = "数值";
      const chartData: Record<string, unknown>[] = numericCols.map((col) => ({
        [metricCol]: col,
        [valueCol]: data[0][col]
      }));
      return { xField: metricCol, yField: valueCol, chartData };
    }
    const xField = categoricalCols[0] ?? columns[0];
    const yCandidates = (numericCols.length ? numericCols : columns).filter((col) => col !== xField);
    const yField = yCandidates[0] ?? columns[1] ?? columns[0];
    return { xField, yField, chartData: data };
  }, [columns, data]);

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
