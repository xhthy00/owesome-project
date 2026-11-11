import { useEffect, useRef, useState } from "react";
import type { EChartsType } from "echarts/core";
import { buildChartOption } from "./chartOption";
import { specTitle, type ChartSpec } from "./chartSpec";

type Props = {
  spec: ChartSpec;
  rows: unknown[][];
  columns: string[];
  height?: number;
  className?: string;
};

export default function ChartView({
  spec,
  rows,
  columns,
  height = 280,
  className = ""
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const renderable = spec.type !== "kpi" && spec.type !== "table";

  useEffect(() => {
    if (!renderable) return;
    const element = containerRef.current;
    if (!element) return;
    let disposed = false;
    let chart: EChartsType | null = null;
    setFailed(false);
    void import("./echartsRuntime")
      .then(({ echarts }) => {
        if (disposed) return;
        chart = echarts.init(element, undefined, { renderer: "canvas" });
        chartRef.current = chart;
        setReady(true);
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });
    return () => {
      disposed = true;
      chart?.dispose();
      chartRef.current = null;
      setReady(false);
    };
  }, [renderable]);

  useEffect(() => {
    if (!ready || !chartRef.current) return;
    const option = buildChartOption(spec, rows, columns);
    if (!option) {
      setFailed(true);
      return;
    }
    setFailed(false);
    chartRef.current.setOption(option, true);
  }, [ready, spec, rows, columns]);

  useEffect(() => {
    if (!ready) return;
    const element = containerRef.current;
    if (!element) return;
    const resize = () => chartRef.current?.resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    window.addEventListener("resize", resize);
    const themeObserver = new MutationObserver(() => {
      const option = buildChartOption(spec, rows, columns);
      if (option) chartRef.current?.setOption(option, true);
    });
    themeObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    return () => {
      observer.disconnect();
      themeObserver.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [ready, spec, rows, columns]);

  if (!renderable) return null;
  if (failed) {
    return (
      <div className={`chart-view chart-view-empty ${className}`} style={{ height }}>
        <span>图表加载失败，请查看下方数据表</span>
      </div>
    );
  }
  return (
    <div
      ref={containerRef}
      className={`chart-view ${className}`}
      style={{ height }}
      role="img"
      aria-label={specTitle(spec)}
    />
  );
}
