import type { ThemeConfig } from "antd";
import { theme } from "antd";

/**
 * AntD 主题的取值来源。
 * 必须与 styles/tokens.css 中的 --color-* / --radius-* / --font-* 保持一致
 * （tokens.test.ts 会做校验，改这里记得同步改 CSS）。
 */
export const brand = {
  50: "#eff6ff",
  100: "#dbeafe",
  200: "#bfdbfe",
  500: "#3b82f6",
  600: "#2563eb",
  700: "#1d4ed8"
} as const;

export const fontFamily =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif';

export const radius = {
  xs: 4,
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16
} as const;

export const shadow = {
  1: "0 1px 2px rgba(15, 23, 42, 0.04)",
  2: "0 4px 20px rgba(15, 23, 42, 0.06)",
  3: "0 16px 48px rgba(15, 23, 42, 0.12)"
} as const;

export type AppMode = "light" | "dark";

/**
 * 只设置「种子 token + 尺寸」，中性色交给 AntD 算法按模式派生，
 * 这样暗色模式不需要为每个组件再写一遍颜色。
 */
export function buildAntdTheme(mode: AppMode): ThemeConfig {
  return {
    token: {
      colorPrimary: brand[600],
      colorInfo: brand[600],
      colorLink: mode === "dark" ? "#93c5fd" : brand[700],
      colorLinkHover: mode === "dark" ? "#bfdbfe" : brand[600],
      fontFamily,
      fontSize: 14,
      fontSizeSM: 12,
      fontSizeLG: 16,
      borderRadius: radius.md,
      borderRadiusSM: radius.sm,
      borderRadiusLG: radius.lg,
      borderRadiusXS: radius.xs,
      controlHeight: 36,
      controlHeightLG: 44,
      controlHeightSM: 28,
      boxShadow: shadow[2],
      boxShadowSecondary: shadow[3],
      wireframe: false
    },
    components: {
      Button: { fontWeight: 500, primaryShadow: "none", defaultShadow: "none" },
      Card: { paddingLG: 20, borderRadiusLG: radius.lg },
      Modal: { borderRadiusLG: radius.xl },
      Drawer: { borderRadiusLG: radius.xl },
      Table: { headerSplitColor: "transparent", cellPaddingBlock: 12 },
      Tabs: { horizontalItemGutter: 24, titleFontSize: 14 },
      Select: { optionSelectedFontWeight: 500 },
      Form: { labelFontSize: 13, verticalLabelPadding: "0 0 6px" }
    },
    algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm
  };
}
