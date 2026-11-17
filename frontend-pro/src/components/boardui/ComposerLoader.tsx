import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

/**
 * Composer Loader —— 移植自 BoardUI 的 composer-loader（MIT）：
 * 一条彩虹光带沿着输入框边缘绕圈，向内渗出一层辉光，随任务开始/结束淡入淡出。
 *
 * 光带由 pathLength 归一化的 SVG 虚线段组成，画三遍：清晰的线、紧辉光、宽 bloom；
 * 每层的头部按像素后撤，让尖端始终领先；taper 用多层更短的虚线叠出两端渐隐。
 * 外层只负责裁剪，所以 bloom 只向输入框内部渗。
 */
export interface ComposerLoaderProps {
  /** 是否显示光带；切换时整体淡入淡出。 */
  active?: boolean;
  /** 四段渐变颜色，左 → 右分布。 */
  colors?: [string, string, string, string];
  /** 每圈秒数。 */
  speed?: number;
  /** 整体不透明度。 */
  intensity?: number;
  /** bloom 向内渗透的像素。 */
  bloom?: number;
  /** bloom 层不透明度。 */
  bloomStrength?: number;
  /** 光带占圆周的角度（0-360）。 */
  arc?: number;
  /** 反向绕圈。 */
  reverse?: boolean;
  /** 圆角像素；不传按胶囊计算。 */
  radius?: number;
  /** 清晰线的宽度。 */
  line?: number;
  /** 两端渐隐的比例（0-1）。 */
  taper?: number;
  /** 光带沿圆周的起点偏移（0-1），用于两条光带对齐。 */
  offset?: number;
  className?: string;
  children?: ReactNode;
}

export const COMPOSER_LOADER_COLORS: [string, string, string, string] = [
  "#55e8dc",
  "#70c9fa",
  "#a8a3f5",
  "#e98ed8",
];

function hexTriplet(hex: string) {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

export default function ComposerLoader({
  active = true,
  colors = COMPOSER_LOADER_COLORS,
  speed = 4.5,
  intensity = 0.7,
  bloom = 16,
  bloomStrength = 0.3,
  arc = 120,
  reverse = false,
  radius,
  line = 2.5,
  taper = 0,
  offset = 0,
  className,
  children,
}: ComposerLoaderProps) {
  const rawId = useId();
  const gradientId = `cowork-loader-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const clipRef = useRef<HTMLSpanElement>(null);
  const [box, setBox] = useState({ w: 640, h: 52 });

  useEffect(() => {
    const element = clipRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setBox({
      w: Math.max(1, element.clientWidth),
      h: Math.max(1, element.clientHeight),
    }));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const [c0, c1, c2, c3] = colors;
  // 渐变中点取中间两色的均值，避免整条光带被冲淡成白色。
  const mid = `rgb(${hexTriplet(c1).map((value, index) => Math.round((value + hexTriplet(c2)[index]) / 2)).join(",")})`;

  const band = (arc / 360) * 100;
  const rx = radius ?? box.h / 2;
  const cornerRadius = radius ?? 9999;
  const direction = reverse ? "reverse" : "normal";

  // pathLength 归一化后，1px 对应的路径单位数；用来把每层辉光的头部按像素往后拉。
  const cornerR = Math.min(rx, box.w / 2, box.h / 2);
  const perimeter = Math.max(1, 2 * (box.w + box.h) - 8 * cornerR + 2 * Math.PI * cornerR);
  const pxUnits = (px: number) => (px * 100) / perimeter;
  const dashPhase = (len: number, backPx: number) =>
    (reverse ? pxUnits(backPx) : len - band + pxUnits(backPx)) - offset * 100;

  const taperSteps = taper > 0 ? 14 : 1;
  const crispBlur = taper > 0 ? Math.max(0.5, line * 0.8) : 0.5;

  const stroke = (width: number, blur: number, opacity: number, dashLen: number, backPx: number) =>
    Array.from({ length: taperSteps }, (_, index) => {
      const len = dashLen * (1 - (taper * index) / taperSteps);
      const phase = dashPhase(dashLen, backPx) - (dashLen - len) / 2;
      return (
        <rect
          key={`${width}-${blur}-${dashLen}-${index}`}
          x={0}
          y={0}
          width={box.w}
          height={box.h}
          rx={rx}
          pathLength={100}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={width}
          strokeLinecap="round"
          strokeDasharray={`${len} ${100 - len}`}
          className="cowork-loader-rect"
          // SVG 原生模糊，不用 CSS filter：WebKit 会忽略单个 SVG 元素上的 CSS filter。
          filter={blur > 0 ? `url(#${gradientId}-b${String(blur).replace(".", "_")})` : undefined}
          style={{
            opacity: opacity / taperSteps,
            animation: `cowork-loader-dash ${speed}s linear ${(phase * speed) / 100}s infinite ${direction}`,
          }}
        />
      );
    });

  const blurFilter = (blurRadius: number) => (
    <filter
      key={blurRadius}
      id={`${gradientId}-b${String(blurRadius).replace(".", "_")}`}
      x="-50%"
      y="-50%"
      width="200%"
      height="200%"
    >
      <feGaussianBlur stdDeviation={blurRadius} />
    </filter>
  );

  return (
    <span
      ref={clipRef}
      aria-hidden="true"
      className={`cowork-composer-light${active ? " is-active" : ""}${className ? ` ${className}` : ""}`}
      style={{ borderRadius: cornerRadius, transition: "opacity 450ms ease", mixBlendMode: "normal" as CSSProperties["mixBlendMode"] }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${box.w} ${box.h}`}
        preserveAspectRatio="none"
        style={{ opacity: intensity }}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={c0} />
            <stop offset="30%" stopColor={c1} />
            <stop offset="50%" stopColor={mid} />
            <stop offset="70%" stopColor={c2} />
            <stop offset="100%" stopColor={c3} />
          </linearGradient>
          {Array.from(new Set([14, 6, crispBlur])).map(blurFilter)}
        </defs>
        {/* 向内渗透的宽辉光（外半边被输入框裁掉） */}
        {bloom > 0 && stroke(bloom * 2, 14, bloomStrength, band * 0.9, bloom + 16)}
        {/* 紧辉光 */}
        {stroke(line * 3.2, 6, 0.8, band * 0.95, 10)}
        {/* 清晰的折射线，尖端领先 */}
        {stroke(line, crispBlur, 1, band, 0)}
      </svg>
      {children ? <span className="cowork-loader-content">{children}</span> : null}
    </span>
  );
}
