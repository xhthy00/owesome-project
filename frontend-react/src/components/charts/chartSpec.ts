import { labelColumn } from "@/utils/columnLabels";

export type ChartSpecType =
  | "kpi"
  | "column"
  | "bar"
  | "groupedColumn"
  | "stackedColumn"
  | "line"
  | "area"
  | "donut"
  | "pie"
  | "table";

export type ChartColumn = {
  name: string;
  label?: string;
  role: "dimension" | "measure" | "time";
  type: "string" | "number" | "percent" | "date" | "time";
  unit?: string;
};

export type ChartSpec = {
  kind?: "spec";
  version: 1;
  type: ChartSpecType;
  title?: string;
  subtitle?: string;
  unit?: string;
  dataset: {
    columns: ChartColumn[];
    rows?: unknown[][];
    rowCount?: number;
    truncated?: boolean;
  };
  encoding: {
    x?: string;
    y?: string[];
    series?: string;
    size?: string;
    color?: string;
  };
  transform?: {
    sort?: { by: string; order: "asc" | "desc" };
    topN?: number;
    otherLabel?: string;
    stack?: boolean;
    normalize?: boolean;
  };
  annotations?: {
    referenceLines?: Array<{ axis: "x" | "y"; value: number | string; label?: string }>;
    target?: { value: number; label?: string };
  };
  options?: {
    showLabel?: boolean;
    showLegend?: boolean;
    smooth?: boolean;
    yStartAtZero?: boolean;
    dualAxis?: boolean;
    palette?: string;
    valueSuffix?: string;
  };
  source?: {
    resultIndex?: number;
    queryKey?: string;
    sql?: string;
    rowCount?: number;
    truncated?: boolean;
    scope?: string;
  };
};

export type ColumnProfile = {
  name: string;
  index: number;
  numeric: boolean;
  cardinality: number;
  hasNull: boolean;
  isTime: boolean;
  isRate: boolean;
  isRank: boolean;
  isId: boolean;
  isCount: boolean;
  isScore: boolean;
  role: "dimension" | "measure" | "time";
  type: "string" | "number" | "percent" | "date" | "time";
};

const CHART_TYPES: ReadonlySet<string> = new Set<ChartSpecType>([
  "kpi",
  "column",
  "bar",
  "groupedColumn",
  "stackedColumn",
  "line",
  "area",
  "donut",
  "pie",
  "table"
]);

const TYPE_ALIASES: Record<string, ChartSpecType> = {
  histogram: "column",
  hbar: "bar",
  horizontal: "bar",
  horizontal_bar: "bar",
  grouped: "groupedColumn",
  grouped_bar: "groupedColumn",
  stacked: "stackedColumn",
  stacked_bar: "stackedColumn",
  pie_chart: "pie",
  donut_chart: "donut",
  line_chart: "line",
  area_chart: "area"
};

const TIME_RE = /(时间|日期|月份|月度|场次|考试|期次|学年|学期|period|date|month|term|year)/i;
const RATE_RE = /(率|占比|比例|百分比|rate|ratio|pct|percent)/i;
const RANK_RE = /(排名|名次|位次|rank|rk)/i;
const ID_RE = /(^|_)(id|code|no|sn)$|(编号|代码|批次|学号|考号|序号)$/i;
const COUNT_RE = /(人数|考生|参考|数量|学校数|班级数|校数|班数|count|num)/i;
const SCORE_RE = /(均分|平均分|得分|分数|成绩|score|avg|mean)/i;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/,/g, "").replace(/，/g, "").replace(/％/g, "%");
  if (!text || ["-", "—", "–", "无", "null", "None"].includes(text)) return null;
  const core = text.endsWith("%") ? text.slice(0, -1).trim() : text;
  if (!/^[+-]?\d+(?:\.\d+)?$/.test(core)) return null;
  const number = Number(core);
  return Number.isFinite(number) ? number : null;
}

export function isRateName(name: string): boolean {
  return RATE_RE.test(name);
}

export function isRankName(name: string): boolean {
  return RANK_RE.test(name) && !isRateName(name);
}

export function isCountName(name: string): boolean {
  return COUNT_RE.test(name);
}

export function isScoreName(name: string): boolean {
  return SCORE_RE.test(name);
}

export function isTimeName(name: string): boolean {
  return TIME_RE.test(name);
}

export function unitForColumn(name: string): string {
  if (isRateName(name)) return "%";
  if (isScoreName(name)) return "分";
  if (isCountName(name)) return "人";
  return "";
}

export function profileColumns(columns: string[], rows: unknown[][]): ColumnProfile[] {
  return columns.map((rawName, index) => {
    const name = String(rawName ?? "").trim();
    const values = rows.map((row) => row[index]);
    const nonEmpty = values.filter((value) => value != null && value !== "");
    const numbers = nonEmpty.map(toNumber);
    const numeric = nonEmpty.length > 0 && numbers.every((value) => value !== null);
    const rate = isRateName(name);
    const rank = isRankName(name);
    const id = ID_RE.test(name);
    const time = isTimeName(name) && !id;
    return {
      name,
      index,
      numeric,
      cardinality: new Set(nonEmpty.map((value) => String(value))).size,
      hasNull: nonEmpty.length < values.length,
      isTime: time,
      isRate: rate,
      isRank: rank,
      isId: id,
      isCount: isCountName(name),
      isScore: isScoreName(name),
      role: id ? "dimension" : time ? "time" : numeric && !rank ? "measure" : "dimension",
      type: id ? "string" : time ? "time" : rate && numeric ? "percent" : numeric ? "number" : "string"
    };
  });
}

export function chartTypeOf(value: unknown): ChartSpecType | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;
  const alias = TYPE_ALIASES[raw.toLowerCase()];
  if (alias) return alias;
  return CHART_TYPES.has(raw) ? (raw as ChartSpecType) : null;
}

export function isChartSpec(value: unknown): value is ChartSpec {
  if (!isRecord(value)) return false;
  if (value.version !== 1 || !CHART_TYPES.has(String(value.type))) return false;
  if (!isRecord(value.encoding)) return false;
  if (!isRecord(value.dataset) || !Array.isArray(value.dataset.columns)) return false;
  return true;
}

export function parseChartSpec(raw: unknown): ChartSpec | null {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  return isChartSpec(value) ? value : null;
}

export function specColumnNames(spec: ChartSpec): string[] {
  return spec.dataset.columns.map((column) => column.name);
}

function nonNegative(profile: ColumnProfile, rows: unknown[][]): boolean {
  return rows.every((row) => {
    const value = toNumber(row[profile.index]);
    return value == null || value >= 0;
  });
}

export function validateChartSpec(
  spec: ChartSpec,
  columns: string[],
  rows: unknown[][]
): string[] {
  const errors: string[] = [];
  if (spec.version !== 1) errors.push("不支持的图表版本");
  if (!CHART_TYPES.has(spec.type)) errors.push(`不支持的图表类型：${spec.type}`);
  if (spec.type === "kpi" || spec.type === "table") return errors;

  const encoding = spec.encoding || {};
  const x = encoding.x;
  const y = Array.isArray(encoding.y) ? encoding.y : [];
  if (!y.length) errors.push("缺少 Y 轴字段");
  if (x && !columns.includes(x)) errors.push(`X 轴字段不存在：${x}`);
  y.forEach((name) => {
    if (!columns.includes(name)) errors.push(`Y 轴字段不存在：${name}`);
  });
  const profiles = profileColumns(columns, rows);
  const byName = new Map(profiles.map((profile) => [profile.name, profile]));
  y.forEach((name) => {
    if (!columns.includes(name)) return;
    const profile = byName.get(name);
    if (!profile?.numeric) errors.push(`Y 轴字段不是数值：${name}`);
    else if (profile.isId) errors.push(`Y 轴字段是标识列：${name}`);
  });
  if (encoding.series) {
    const series = byName.get(encoding.series);
    if (!series) errors.push(`系列字段不存在：${encoding.series}`);
    else if (series.cardinality > 8) errors.push("系列字段基数超过 8");
  }
  if (spec.type === "donut" || spec.type === "pie") {
    if (y.length !== 1) errors.push("环形图只允许一个 Y 轴字段");
    const profile = byName.get(y[0]);
    if (profile && !nonNegative(profile, rows)) errors.push("环形图不允许负值");
  }
  return errors;
}

function titleText(value: unknown): string {
  if (isRecord(value) && typeof value.text === "string") return value.text;
  return "";
}

/**
 * 兼容历史落库的 ECharts option。
 * 只有 option 自带 series 数据时才转换；否则返回 null，让上层走启发式选型。
 */
export function adaptLegacyChartConfig(
  chartType: string | undefined,
  config: unknown
): ChartSpec | null {
  const parsed = parseChartSpec(config);
  if (parsed) return parsed;
  if (!isRecord(config)) return null;
  const series = Array.isArray(config.series) ? config.series : [];
  if (!series.length || !isRecord(series[0])) return null;
  const first = series[0];
  const title = titleText(config.title);

  if (Array.isArray(first.data) && isRecord(first.data[0])) {
    const rows = first.data
      .filter(isRecord)
      .map((item) => [String(item.name ?? ""), item.value ?? null]);
    return {
      kind: "spec",
      version: 1,
      type: chartType === "pie" ? "pie" : "donut",
      title,
      dataset: {
        columns: [
          { name: "维度", label: "维度", role: "dimension", type: "string" },
          { name: "数值", label: "数值", role: "measure", type: "number" }
        ],
        rows,
        rowCount: rows.length
      },
      encoding: { x: "维度", y: ["数值"] },
      options: { showLabel: true }
    };
  }

  const xAxis = Array.isArray(config.xAxis) ? config.xAxis[0] : config.xAxis;
  const xLabels = isRecord(xAxis) && Array.isArray(xAxis.data) ? xAxis.data : [];
  if (!xLabels.length) return null;
  const names = ["维度"];
  const rows: unknown[][] = xLabels.map((label) => [label]);
  series.forEach((item, index) => {
    if (!isRecord(item)) return;
    const name = String(item.name ?? "").trim() || `系列 ${index + 1}`;
    names.push(name);
    const values = Array.isArray(item.data) ? item.data : [];
    rows.forEach((row, rowIndex) => row.push(values[rowIndex] ?? null));
  });
  const type: ChartSpecType = first.type === "line" ? "line" : "column";
  return {
    kind: "spec",
    version: 1,
    type,
    title,
    dataset: {
      columns: names.map((name, index) => ({
        name,
        label: name,
        role: index === 0 ? "dimension" : "measure",
        type: index === 0 ? "string" : "number"
      })),
      rows,
      rowCount: rows.length
    },
    encoding: { x: "维度", y: names.slice(1) },
    options: { showLabel: false }
  };
}

export function specTitle(spec: ChartSpec): string {
  const raw = (spec.title || "").trim();
  const yLabels = (spec.encoding.y || []).map(labelColumn).filter(Boolean);
  const xLabel = spec.encoding.x ? labelColumn(spec.encoding.x) : "";
  const derived =
    yLabels.length > 1
      ? `${yLabels.join("、")}${xLabel ? `（按${xLabel}）` : ""}`
      : yLabels[0]
        ? `${yLabels[0]}${xLabel ? `（按${xLabel}）` : ""}`
        : "";
  // 模型生成的中文标题保留；标题里混入原始英文列名时，用编码字段重建中文标题。
  if (raw && !/[a-z_]{2,}/i.test(raw)) return raw;
  return derived || (raw ? labelColumn(raw) : "图表");
}
