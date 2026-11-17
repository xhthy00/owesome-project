import { useEffect, useState } from "react";

/**
 * Agent Thinking —— 移植自 BoardUI 的 agent-thinking（MIT）：
 * 聊天输入框上方的“思考中”指示器，四种形态 + 流光文字 + 计时。
 *
 * - wave     点阵上有一条斜向波前滑过
 * - spin     点阵上有一个亮点绕圈
 * - stars    星光依次闪烁
 * - infinity 彗星沿 8 字轨迹巡回
 */
export type AgentThinkingVariant = "wave" | "spin" | "stars" | "infinity";

export interface AgentThinkingProps {
  variant?: AgentThinkingVariant;
  label?: string;
  /** 已经格式化好的计时文案，例如“已工作 9秒”。 */
  timer?: string;
  /** 文字上的流光，默认开。 */
  shimmer?: boolean;
  className?: string;
}

const DOTS_GRID = 3;
const DOTS_SIZE = 4;
const DOTS_GAP = 2;
const DOTS_TICK_MS = 80;
const DOTS_FADE_MS = 220;
const DOTS_TRAIL = 0.3;
const DOTS_MIN_OPACITY = 0.12;
const DOTS_PHASE_STEP = 1 / 8;

// 首帧固定，避免服务端/客户端渲染不一致；减少动效时也停在这一帧。
const DOTS_SEED = [0.55, 0.3, 0.15, 0.85, 0.55, 0.3, 1, 0.85, 0.55];

function dotScalar(variant: "wave" | "spin", col: number, row: number) {
  const max = DOTS_GRID - 1;
  if (variant === "wave") {
    return ((col + row) / (2 * max)) * (DOTS_GRID / (DOTS_GRID + 1));
  }
  const center = max / 2;
  return (Math.atan2(row - center, col - center) / (2 * Math.PI) + 1) % 1;
}

function dotOpacities(variant: "wave" | "spin", phase: number) {
  return Array.from({ length: DOTS_GRID * DOTS_GRID }, (_, index) => {
    const scalar = dotScalar(variant, index % DOTS_GRID, Math.floor(index / DOTS_GRID));
    const behind = (phase - scalar + 1) % 1;
    const lit = Math.max(0, 1 - behind / DOTS_TRAIL) ** 1.5;
    return DOTS_MIN_OPACITY + (1 - DOTS_MIN_OPACITY) * lit;
  });
}

function DotsIndicator({ variant }: { variant: "wave" | "spin" }) {
  const [opacities, setOpacities] = useState<number[]>(DOTS_SEED);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let phase = 0;
    const timer = window.setInterval(() => {
      phase = (phase + DOTS_PHASE_STEP) % 1;
      setOpacities(dotOpacities(variant, phase));
    }, DOTS_TICK_MS);
    return () => window.clearInterval(timer);
  }, [variant]);

  return (
    <span
      aria-hidden="true"
      className="cowork-thinking-dots"
      style={{ gridTemplateColumns: `repeat(${DOTS_GRID}, ${DOTS_SIZE}px)`, gap: DOTS_GAP }}
    >
      {opacities.map((opacity, index) => (
        <span
          key={index}
          className="cowork-thinking-dot"
          style={{ width: DOTS_SIZE, height: DOTS_SIZE, opacity, transition: `opacity ${DOTS_FADE_MS}ms ease` }}
        />
      ))}
    </span>
  );
}

const STAR_PERIOD_S = 1.4;
const STAR_SIZE = 14;
const STAR_LAYOUT = [
  { x: 50, y: 46, scale: 1 },
  { x: 18, y: 22, scale: 0.55 },
  { x: 82, y: 26, scale: 0.45 },
  { x: 78, y: 76, scale: 0.55 },
  { x: 22, y: 78, scale: 0.4 },
];
const STAR_PATH = "M12 0C13 7 17 11 24 12C17 13 13 17 12 24C11 17 7 13 0 12C7 11 11 7 12 0Z";

function StarsIndicator() {
  const box = STAR_SIZE * 1.5;
  return (
    <span aria-hidden="true" className="cowork-thinking-stars" style={{ width: box, height: box }}>
      {STAR_LAYOUT.map((star, index) => {
        const size = STAR_SIZE * star.scale;
        return (
          <svg
            key={index}
            viewBox="0 0 24 24"
            className="cowork-thinking-star"
            style={{
              width: size,
              height: size,
              left: `${star.x}%`,
              top: `${star.y}%`,
              marginLeft: -size / 2,
              marginTop: -size / 2,
              animationDuration: `${STAR_PERIOD_S}s`,
              animationDelay: `${(index * STAR_PERIOD_S * 0.7) / STAR_LAYOUT.length}s`,
            }}
          >
            <path d={STAR_PATH} fill="currentColor" />
          </svg>
        );
      })}
    </span>
  );
}

const INFINITY_WIDTH = 32;
const INFINITY_TRAIL = 11;
const INFINITY_STROKE = 2.75;
const INFINITY_DURATION_S = 1.2;
const INFINITY_PATH = "M28 14C33 5 47 5 47 14C47 23 33 23 28 14C23 5 9 5 9 14C9 23 23 23 28 14Z";

function InfinityIndicator() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 56 28"
      className="cowork-thinking-infinity"
      style={{ width: INFINITY_WIDTH, height: INFINITY_WIDTH / 2, margin: "0 -4px" }}
    >
      <path d={INFINITY_PATH} fill="none" stroke="currentColor" strokeWidth={INFINITY_STROKE} opacity={0.15} />
      <path
        d={INFINITY_PATH}
        pathLength={100}
        fill="none"
        stroke="currentColor"
        strokeWidth={INFINITY_STROKE}
        strokeLinecap="round"
        strokeDasharray={`${INFINITY_TRAIL} ${100 - INFINITY_TRAIL}`}
        className="cowork-thinking-comet"
        style={{ animationDuration: `${INFINITY_DURATION_S}s` }}
      />
    </svg>
  );
}

export function AgentThinking({
  variant = "infinity",
  label,
  timer,
  shimmer = true,
  className,
}: AgentThinkingProps) {
  return (
    <div role="status" className={`cowork-thinking${className ? ` ${className}` : ""}`}>
      {(variant === "wave" || variant === "spin") && <DotsIndicator variant={variant} />}
      {variant === "stars" && <StarsIndicator />}
      {variant === "infinity" && <InfinityIndicator />}
      {label ? (
        <span className={`cowork-thinking-label${shimmer ? " cowork-shimmer" : ""}`}>{label}</span>
      ) : null}
      {timer ? <span className="cowork-thinking-time">{timer}</span> : null}
    </div>
  );
}

/** 只需要一个紧凑指示器时（例如执行记录那一行）。 */
export { DotsIndicator as AgentDots };
