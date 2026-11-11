import { labelColumn } from "@/utils/columnLabels";
import {
  adaptLegacyChartConfig,
  profileColumns,
  toNumber,
  unitForColumn,
  validateChartSpec,
  type ChartColumn,
  type ChartSpec,
  type ChartSpecType,
  type ColumnProfile
} from "./chartSpec";

export type ChartIntent =
  | "trend"
  | "compare"
  | "composition"
  | "distribution"
  | "correlation"
  | "ranking"
  | "target"
  | "detail"
  | "auto";

export function classifyIntent(question: string): ChartIntent {
  const text = (question || "").trim().toLowerCase();
  if (!text) return "auto";
  if (/(趋势|走势|变化|近\s*\d*\s*次|历次|两场|同比|环比|上升|下滑|下降)/.test(text)) {
    return "trend";
  }
  if (/(占比|比例|构成|结构|组成|份额)/.test(text)) return "composition";
  if (/(分布|分数段|分段|集中度|波动|离散|标准差)/.test(text)) return "distribution";
  if (/(相关|关系|影响|关联)/.test(text)) return "correlation";
  if (/(前\s*\d+|后\s*\d+|top\s*\d+|排名|名次|位次|最好|最差|最高|最低)/.test(text)) {
    return "ranking";
  }
  if (/(名单|清单|逐条|明细|列出|哪些)/.test(text)) return "detail";
  if (/(达标|完成率|目标|临界|距离.*还差|差多少)/.test(text)) return "target";
  if (/(对比|比较|差距|谁高|谁低|横向)/.test(text)) return "compare";
  return "auto";
}

function columnsFor(profiles: ColumnProfile[]): ChartColumn[] {
  return profiles.map((profile) => ({
    name: profile.name,
    label: labelColumn(profile.name),
    role: profile.role,
    type: profile.type,
    ...(unitForColumn(profile.name) ? { unit: unitForColumn(profile.name) } : {})
  }));
}

function titleFor(x: string | undefined, y: string[], profiles: ColumnProfile[]): string {
  const names = y.map((name) => labelColumn(name));
  if (names.length === 1) {
    return x ? `${names[0]}（按${labelColumn(x)}）` : names[0];
  }
  if (names.length > 1) {
    return `${names.slice(0, 3).join("、")}${x ? `（按${labelColumn(x)}）` : ""}`;
  }
  return profiles.find((profile) => profile.role === "measure")?.name || "查询结果";
}

function subtitleFor(y: string[], rowCount: number): string {
  const unit = y.map(unitForColumn).find(Boolean) || "";
  return [unit ? `单位：${unit}` : "", `共 ${rowCount} 行`].filter(Boolean).join(" · ");
}

function makeSpec(
  type: ChartSpecType,
  profiles: ColumnProfile[],
  rows: unknown[][],
  options: {
    x?: string;
    y?: string[];
    title?: string;
    sort?: { by: string; order: "asc" | "desc" };
    topN?: number;
    showLabel?: boolean;
  } = {}
): ChartSpec {
  const x = options.x;
  const y = (options.y || []).filter(Boolean);
  const xProfile = profiles.find((profile) => profile.name === x);
  const showLabel =
    options.showLabel ??
    (["column", "bar", "groupedColumn", "stackedColumn", "donut", "pie"].includes(type) &&
      (!xProfile || xProfile.cardinality <= 12));
  const spec: ChartSpec = {
    kind: "spec",
    version: 1,
    type,
    title: options.title || titleFor(x, y, profiles),
    subtitle: subtitleFor(y, rows.length),
    dataset: { columns: columnsFor(profiles), rowCount: rows.length },
    encoding: { ...(x ? { x } : {}), y },
    options: {
      showLabel: Boolean(showLabel),
      yStartAtZero: ["column", "bar", "groupedColumn", "stackedColumn", "area"].includes(type)
    },
    source: { resultIndex: 0, rowCount: rows.length }
  };
  if (options.sort) spec.transform = { sort: options.sort };
  if (options.topN) spec.transform = { ...(spec.transform || {}), topN: options.topN };
  return spec;
}

function tableSpec(profiles: ColumnProfile[], rows: unknown[][]): ChartSpec {
  return makeSpec("table", profiles, rows, { showLabel: false });
}

function kpiSpec(profiles: ColumnProfile[], rows: unknown[][]): ChartSpec {
  return makeSpec("kpi", profiles, rows, { showLabel: false });
}

function nonNegative(profile: ColumnProfile, rows: unknown[][]): boolean {
  return rows.every((row) => {
    const value = Number(row[profile.index]);
    return !Number.isFinite(value) || value >= 0;
  });
}

/**
 * 前端兜底选型。与后端 `chart_spec.recommend_chart_spec` 保持同一套规则；
 * 正常情况下优先使用后端 spec，只有历史数据或旧后端才走这里。
 */
export function recommendChartSpec(
  question: string,
  columns: string[],
  rows: unknown[][]
): ChartSpec | null {
  if (!columns.length || !rows.length) return null;
  const profiles = profileColumns(columns, rows);
  const measures = profiles.filter((profile) => profile.role === "measure" && !profile.isRank);
  const allDimensions = profiles.filter(
    (profile) => profile.role === "dimension" || profile.role === "time"
  );
  const dimensions = allDimensions.filter((profile) => !profile.isId);
  const timeColumns = profiles.filter((profile) => profile.isTime);
  const intent = classifyIntent(question);

  if (!measures.length || intent === "detail") return tableSpec(profiles, rows);
  if (rows.length === 1) return kpiSpec(profiles, rows);

  const xProfile =
    intent === "trend" && timeColumns.length
      ? timeColumns[0]
      : dimensions[0] || allDimensions[0] || timeColumns[0] || null;
  if (!xProfile) return tableSpec(profiles, rows);

  const x = xProfile.name;
  const cardinality = xProfile.cardinality;
  const primary = measures[0];

  if (intent === "composition") {
    if (measures.length === 1 && cardinality >= 3 && cardinality <= 6 && nonNegative(primary, rows)) {
      return makeSpec("donut", profiles, rows, {
        x,
        y: [primary.name],
        sort: { by: primary.name, order: "desc" }
      });
    }
    if (measures.length >= 2 && cardinality <= 12) {
      return makeSpec("stackedColumn", profiles, rows, {
        x,
        y: measures.slice(0, 3).map((profile) => profile.name)
      });
    }
    return makeSpec("bar", profiles, rows, {
      x,
      y: [primary.name],
      sort: { by: primary.name, order: "desc" }
    });
  }

  if (intent === "trend" && xProfile.isTime) {
    return makeSpec("line", profiles, rows, {
      x,
      y: measures.slice(0, 4).map((profile) => profile.name),
      showLabel: false
    });
  }

  if (intent === "ranking") {
    return makeSpec("bar", profiles, rows, {
      x,
      y: [primary.name],
      sort: { by: primary.name, order: "desc" },
      topN: rows.length > 20 ? 20 : undefined
    });
  }

  if (intent === "distribution") {
    return makeSpec("column", profiles, rows, {
      x,
      y: measures.slice(0, 2).map((profile) => profile.name)
    });
  }

  if (intent === "correlation") return tableSpec(profiles, rows);

  if (measures.length >= 2 && cardinality <= 12) {
    return makeSpec("groupedColumn", profiles, rows, {
      x,
      y: measures.slice(0, 3).map((profile) => profile.name)
    });
  }

  if (cardinality > 12) {
    return makeSpec("bar", profiles, rows, {
      x,
      y: [primary.name],
      sort: { by: primary.name, order: "desc" },
      topN: rows.length > 20 ? 20 : undefined
    });
  }

  return makeSpec("column", profiles, rows, {
    x,
    y: measures.slice(0, 2).map((profile) => profile.name),
    ...(intent === "compare" ? { sort: { by: primary.name, order: "desc" as const } } : {})
  });
}

function fmtBin(value: number, unit: string): string {
  const text = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return unit === "%" ? `${text}%` : text;
}

/**
 * 同一份数据的第二个视角：数值分布。
 * 不复用主图类型，避免「主图 + 辅图」看起来是同一张图；趋势图不再切碎。
 */
export function buildSecondarySpec(
  main: ChartSpec | null,
  columns: string[],
  rows: unknown[][]
): ChartSpec | null {
  if (!main || main.type === "kpi" || main.type === "table") return null;
  if (rows.length <= 6) return null;
  if (main.type === "line" || main.type === "area") return null;
  const y = main.encoding.y?.[0];
  if (!y) return null;
  const yIndex = columns.indexOf(y);
  if (yIndex < 0) return null;
  const values = rows
    .map((row) => toNumber(row[yIndex]))
    .filter((value): value is number => value != null);
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (max - min < 1e-9) return null;
  const binCount = Math.min(5, Math.max(3, Math.ceil(Math.sqrt(values.length))));
  const step = (max - min) / binCount;
  const counts = Array.from({ length: binCount }, () => 0);
  values.forEach((value) => {
    const index = Math.min(binCount - 1, Math.max(0, Math.floor((value - min) / step)));
    counts[index] += 1;
  });
  const unit = unitForColumn(y);
  const binRows: unknown[][] = counts.map((count, index) => {
    const start = min + index * step;
    const end = index === binCount - 1 ? max : start + step;
    return [
      `${fmtBin(start, unit)}–${fmtBin(end, unit)}`,
      count
    ];
  });
  return {
    kind: "spec",
    version: 1,
    type: "column",
    title: `「${labelColumn(y)}」分布`,
    subtitle: `共 ${values.length} 行 · 分 ${binCount} 段`,
    dataset: {
      columns: [
        { name: "区间", label: "区间", role: "dimension", type: "string" },
        { name: "行数", label: "行数", role: "measure", type: "number" }
      ],
      rows: binRows,
      rowCount: binRows.length
    },
    encoding: { x: "区间", y: ["行数"] },
    options: { showLabel: true, yStartAtZero: true },
    source: { resultIndex: main.source?.resultIndex ?? 0, rowCount: rows.length }
  };
}

export function resolveChartSpec(options: {
  spec?: ChartSpec | null;
  legacyConfig?: unknown;
  chartType?: string;
  columns: string[];
  rows: unknown[][];
  question: string;
}): ChartSpec | null {
  const candidates = [
    options.spec || null,
    adaptLegacyChartConfig(options.chartType, options.legacyConfig)
  ];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const ownRows = candidate.dataset.rows?.length ? candidate.dataset.rows : options.rows;
    const ownColumns = candidate.dataset.rows?.length
      ? candidate.dataset.columns.map((column) => column.name)
      : options.columns;
    if (!validateChartSpec(candidate, ownColumns, ownRows).length) return candidate;
  }
  return recommendChartSpec(options.question, options.columns, options.rows);
}
