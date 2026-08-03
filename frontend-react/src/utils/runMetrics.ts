export type RunMetrics = {
  totalTokens: number;
  tokenKnown: boolean;
  elapsedMs: number;
  /** false：历史会话未落库耗时，界面显示 — */
  elapsedKnown: boolean;
  progressPct: number;
  runStartedAt: number | null;
};

export const EMPTY_RUN_METRICS: RunMetrics = {
  totalTokens: 0,
  tokenKnown: false,
  elapsedMs: 0,
  elapsedKnown: false,
  progressPct: 0,
  runStartedAt: null
};

/** 从会话 record 回放左侧指标条 */
export function metricsFromPersisted(
  totalTokens?: number | null,
  elapsedMs?: number | null
): RunMetrics {
  return {
    totalTokens: typeof totalTokens === "number" ? totalTokens : 0,
    tokenKnown: typeof totalTokens === "number",
    elapsedMs: typeof elapsedMs === "number" ? elapsedMs : 0,
    elapsedKnown: typeof elapsedMs === "number",
    progressPct: 100,
    runStartedAt: null
  };
}

export type ProgressInput = {
  /** team 子任务总数；0 表示单 Agent / 尚未出 plan */
  planCount: number;
  donePlans: number;
  runningPlans: number;
  plannerDone: boolean;
  charterDone: boolean;
  hasChartOrReport: boolean;
  summarizerDone: boolean;
  hasSummary: boolean;
  toolDoneCount: number;
  finished: boolean;
};

/** team / agent 混合进度：进行中封顶 99，finished 为 100 */
export function computeProgressPct(input: ProgressInput): number {
  if (input.finished) return 100;

  if (input.planCount > 0) {
    let pct = 0;
    if (input.plannerDone || input.planCount > 0) pct += 15;
    const n = input.planCount;
    const planScore = (input.donePlans + (input.runningPlans > 0 ? 0.5 : 0)) / n;
    pct += 55 * Math.min(1, planScore);
    if (input.charterDone || input.hasChartOrReport) pct += 10;
    if (input.summarizerDone || input.hasSummary) pct += 15;
    return Math.min(99, Math.round(pct));
  }

  // 单 Agent：按工具完成轮次近似
  const approx = 20 + input.toolDoneCount * 15;
  return Math.min(90, Math.max(5, approx));
}

export function formatElapsed(ms: number, known = true): string {
  if (!known) return "—";
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function formatTokenCount(n: number, known: boolean): string {
  if (!known) return "—";
  return n.toLocaleString("en-US");
}
