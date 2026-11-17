import type { EChartsOption } from "echarts";

export const CHART_PALETTE = [
  "#2563eb",
  "#60a5fa",
  "#7c3aed",
  "#d97706",
  "#059669",
  "#dc2626",
  "#0891b2",
  "#94a3b8"
];

export function isDarkMode(): boolean {
  if (typeof document === "undefined") return false;
  return document.body.classList.contains("dark");
}

export function chartTheme(): {
  text: string;
  muted: string;
  axis: string;
  grid: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
} {
  const dark = isDarkMode();
  return {
    text: dark ? "#e2e8f0" : "#334155",
    muted: dark ? "#94a3b8" : "#64748b",
    axis: dark ? "#334155" : "#e2e8f0",
    grid: dark ? "#1e293b" : "#eef2f7",
    tooltipBg: dark ? "#111827" : "#ffffff",
    tooltipBorder: dark ? "#334155" : "#e2e8f0",
    tooltipText: dark ? "#e2e8f0" : "#0f172a"
  };
}

export function baseChartOption(): EChartsOption {
  const theme = chartTheme();
  return {
    color: CHART_PALETTE,
    backgroundColor: "transparent",
    textStyle: {
      fontFamily:
        '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      color: theme.text,
      fontSize: 12
    },
    animationDuration: 300,
    grid: { left: 6, right: 12, top: 34, bottom: 6, containLabel: true },
    legend: {
      top: 0,
      left: 0,
      type: "scroll",
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 12,
      textStyle: { color: theme.muted, fontSize: 11 }
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontSize: 12 },
      axisPointer: { type: "shadow" }
    }
  };
}

export function axisOption(category = false): Record<string, unknown> {
  const theme = chartTheme();
  return {
    type: category ? "category" : "value",
    axisLine: { lineStyle: { color: theme.axis } },
    axisTick: { show: false },
    axisLabel: { color: theme.muted, fontSize: 11, hideOverlap: true },
    splitLine: { show: false }
  };
}

export function valueAxisOption(): Record<string, unknown> {
  const theme = chartTheme();
  return {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: theme.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: theme.grid } }
  };
}
