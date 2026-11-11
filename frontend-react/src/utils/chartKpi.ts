import { labelColumn } from "@/utils/columnLabels";
import { formatNumericDisplay } from "@/utils/formatNumericDisplay";
import {
  profileColumns,
  toNumber,
  unitForColumn,
  type ColumnProfile
} from "@/components/charts/chartSpec";

export type KpiTone = "up" | "down" | "neutral";

export type KpiItem = {
  label: string;
  rawName?: string;
  value: string;
  unit?: string;
  delta?: string;
  deltaTone?: KpiTone;
  hint?: string;
};

function formatValue(value: unknown, profile: ColumnProfile): { value: string; unit?: string } {
  const raw = toNumber(value);
  const text = raw == null ? formatNumericDisplay(value) : formatNumericDisplay(raw);
  if (profile.isRate) {
    return { value: text.endsWith("%") ? text : `${text}%` };
  }
  const unit = unitForColumn(profile.name);
  return { value: text, ...(unit ? { unit } : {}) };
}

function signed(value: number, profile: ColumnProfile): string {
  const text = Math.abs(value) < 0.05 ? "0" : formatNumericDisplay(Math.abs(value));
  const sign = value > 0.05 ? "+" : value < -0.05 ? "-" : "";
  return `${sign}${text}${profile.isRate ? "pp" : ""}`;
}

function toneFor(name: string, delta: number): KpiTone {
  if (Math.abs(delta) < 0.05) return "neutral";
  if (/(差距|极差|分差|退步|下降|波动|标准差)/.test(name)) {
    return delta > 0 ? "down" : "up";
  }
  return delta > 0 ? "up" : "down";
}

function valuesOf(rows: unknown[][], profile: ColumnProfile): number[] {
  return rows
    .map((row) => toNumber(row[profile.index]))
    .filter((value): value is number => value != null);
}

function aggregate(profile: ColumnProfile, rows: unknown[][]): { value: number; prefix: string } {
  const values = valuesOf(rows, profile);
  if (!values.length) return { value: 0, prefix: "平均" };
  if (profile.isCount) {
    return { value: values.reduce((sum, value) => sum + value, 0), prefix: "" };
  }
  const name = profile.name.toLowerCase();
  const label = labelColumn(profile.name);
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  if (/^(average|avg|mean)(_|$)/.test(name) || /^(平均|均分)/.test(label)) {
    return { value: average, prefix: "" };
  }
  if (/^(max|min)(_|$)/.test(name) || /^(最高|最低)/.test(label)) {
    return { value: name.startsWith("min") || label.startsWith("最低") ? Math.min(...values) : Math.max(...values), prefix: "" };
  }
  if (profile.isRate || profile.isScore) {
    return { value: average, prefix: "平均" };
  }
  return { value: Math.max(...values), prefix: "最高" };
}

function measurePriority(profile: ColumnProfile): number {
  const name = profile.name.toLowerCase();
  if (profile.isRate) return 6;
  if (/^(average|avg|mean)(_|$)/.test(name)) return 5;
  if (profile.isCount) return 4;
  if (name === "total_score") return 3;
  if (profile.isScore) return 2;
  return 1;
}

/** 从查询结果派生 2~4 个可解释的 KPI；无可用数值列时返回空。 */
export function buildKpis(columns: string[], rows: unknown[][]): KpiItem[] {
  if (!columns.length || !rows.length) return [];
  const profiles = profileColumns(columns, rows);
  const candidates = profiles.filter(
    (profile) => profile.role === "measure" && !profile.isRank
  );
  const hasAverage = candidates.some((profile) =>
    /^(average|avg|mean)(_|$)/.test(profile.name.toLowerCase())
  );
  const measures =
    rows.length === 1
      ? candidates.slice(0, 4)
      : candidates
          .filter((profile) => !(hasAverage && profile.name.toLowerCase() === "total_score"))
          .sort((left, right) => measurePriority(right) - measurePriority(left))
          .slice(0, 4);
  if (!measures.length) return [];
  const dimension = profiles.find((profile) => profile.role === "dimension");
  const time = profiles.find((profile) => profile.isTime);

  if (rows.length === 1) {
    const row = rows[0];
    return measures.map((profile) => {
      const formatted = formatValue(row[profile.index], profile);
      const context = dimension ? String(row[dimension.index] ?? "").trim() : "";
      return {
        label: labelColumn(profile.name),
        rawName: profile.name,
        value: formatted.value,
        ...(formatted.unit ? { unit: formatted.unit } : {}),
        ...(context ? { hint: context } : {})
      };
    });
  }

  const markIndex = columns.findIndex((name) => /标记|本班|本校/.test(name));
  const markedRow =
    markIndex >= 0 ? rows.find((row) => String(row[markIndex] ?? "").includes("本班")) : undefined;
  if (markedRow) {
    return measures.map((profile) => {
      const formatted = formatValue(markedRow[profile.index], profile);
      return {
        label: `本班${labelColumn(profile.name)}`,
        rawName: profile.name,
        value: formatted.value,
        ...(formatted.unit ? { unit: formatted.unit } : {}),
        hint: "本班口径"
      };
    });
  }

  if (rows.length === 2 && time) {
    const first = rows[0];
    const latest = rows[1];
    const period = String(latest[time.index] ?? "").trim();
    return measures.map((profile) => {
      const formatted = formatValue(latest[profile.index], profile);
      const previous = toNumber(first[profile.index]);
      const current = toNumber(latest[profile.index]);
      const delta = previous != null && current != null ? current - previous : null;
      return {
        label: `最新${labelColumn(profile.name)}`,
        rawName: profile.name,
        value: formatted.value,
        ...(formatted.unit ? { unit: formatted.unit } : {}),
        ...(delta != null
          ? {
              delta: signed(delta, profile),
              deltaTone: toneFor(profile.name, delta),
              hint: `较${String(first[time.index] ?? "上期")}${period ? ` · ${period}` : ""}`
            }
          : { hint: period })
      };
    });
  }

  const items: KpiItem[] = measures.map((profile) => {
    const result = aggregate(profile, rows);
    const formatted = formatValue(result.value, profile);
    return {
      label: `${result.prefix}${labelColumn(profile.name)}`,
      rawName: profile.name,
      value: formatted.value,
      ...(formatted.unit ? { unit: formatted.unit } : {}),
      hint: `共 ${rows.length} 行`
    };
  });
  if (items.length < 3 && dimension) {
    items.push({
      label: `${labelColumn(dimension.name)}数`,
      value: String(dimension.cardinality),
      unit: "个",
      hint: `覆盖 ${rows.length} 行`
    });
  }
  return items.slice(0, 4);
}
