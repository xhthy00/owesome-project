/** 表格数字展示：小数保留两位，整数原样。学号等长数字字符串不改。 */

const DECIMAL_RE = /^-?(?:0|[1-9]\d*)\.\d+$/;

export function formatNumericDisplay(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    const pct = trimmed.endsWith("%");
    const core = (pct ? trimmed.slice(0, -1) : trimmed).replace(/,/g, "").trim();
    if (DECIMAL_RE.test(core)) {
      const n = Number(core);
      if (Number.isFinite(n)) {
        const body = n.toFixed(2);
        return pct ? `${body}%` : body;
      }
    }
    return value;
  }
  return String(value);
}
