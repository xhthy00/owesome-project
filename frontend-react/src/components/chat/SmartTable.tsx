import { CopyOutlined, DownOutlined, FullscreenOutlined, UpOutlined } from "@ant-design/icons";
import { Modal, message } from "antd";
import { useMemo, useState } from "react";
import { labelColumn } from "@/utils/columnLabels";
import { formatNumericDisplay } from "@/utils/formatNumericDisplay";
import { profileColumns, type ColumnProfile } from "@/components/charts/chartSpec";

type Props = {
  columns: string[];
  rows: unknown[][];
  rowCount?: number;
  title?: string;
  maxRows?: number;
};

type SortState = { index: number; order: "asc" | "desc" } | null;

function numericValue(profile: ColumnProfile, value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/,/g, "").replace(/％/g, "%");
  const core = text.endsWith("%") ? text.slice(0, -1) : text;
  const number = Number(core);
  return Number.isFinite(number) ? number : null;
}

function valueText(profile: ColumnProfile, value: unknown): string {
  if (value == null || value === "") return "—";
  const text = formatNumericDisplay(value);
  if (profile.isRate && !text.endsWith("%") && text !== "—") return `${text}%`;
  return text;
}

function cellClass(profile: ColumnProfile, text: string): string {
  const classes = [profile.numeric ? "is-num" : ""];
  if (/^[+-]\d/.test(text) && /(差|变化|进退步|提升|下降)/.test(profile.name)) {
    classes.push(text.startsWith("+") ? "is-up" : "is-down");
  }
  return classes.filter(Boolean).join(" ");
}

export default function SmartTable({
  columns,
  rows,
  rowCount,
  title = "明细数据",
  maxRows = 6
}: Props) {
  const [sort, setSort] = useState<SortState>(null);
  const [open, setOpen] = useState(false);
  const profiles = useMemo(() => profileColumns(columns, rows), [columns, rows]);
  const total = rowCount ?? rows.length;

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const index = sort.index;
    const profile = profiles[index];
    return [...rows].sort((left, right) => {
      const a = numericValue(profile, left[index]);
      const b = numericValue(profile, right[index]);
      if (profile.numeric && a != null && b != null) {
        return sort.order === "asc" ? a - b : b - a;
      }
      const result = String(left[index] ?? "").localeCompare(String(right[index] ?? ""), "zh-CN");
      return sort.order === "asc" ? result : -result;
    });
  }, [rows, sort, profiles]);

  const visibleRows = maxRows ? sortedRows.slice(0, maxRows) : sortedRows;
  const ranges = useMemo(() => {
    return profiles.map((profile) => {
      if (!profile.numeric) return 0;
      const values = rows
        .map((row) => numericValue(profile, row[profile.index]))
        .filter((value): value is number => value != null);
      return Math.max(0, ...values.map((value) => Math.abs(value)));
    });
  }, [profiles, rows]);

  const toggleSort = (index: number) => {
    setSort((previous) => {
      if (previous?.index !== index) return { index, order: "desc" };
      return { index, order: previous.order === "desc" ? "asc" : "desc" };
    });
  };

  const copyTable = async () => {
    const lines = [
      columns.map((column) => labelColumn(column)).join("\t"),
      ...rows.map((row) => row.map((value) => String(value ?? "")).join("\t"))
    ];
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      message.success("已复制表格");
    } catch {
      message.error("复制失败");
    }
  };

  const renderTable = (data: unknown[][], full: boolean) => (
    <div className={`smart-table-scroll ${full ? "is-full" : ""}`}>
      <table className="smart-table">
        <thead>
          <tr>
            {columns.map((column, index) => {
              const active = sort?.index === index;
              return (
                <th
                  key={column}
                  title={column}
                  className={profiles[index]?.numeric ? "is-num" : undefined}
                  aria-sort={active ? (sort?.order === "asc" ? "ascending" : "descending") : "none"}
                >
                  <button type="button" onClick={() => toggleSort(index)} title="点击排序">
                    <span>{labelColumn(column)}</span>
                    {active ? (
                      sort?.order === "asc" ? <UpOutlined /> : <DownOutlined />
                    ) : null}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {data.map((row, rowIndex) => {
            const marked = row.some((value) => String(value ?? "").includes("本班"));
            return (
              <tr key={rowIndex} className={marked ? "is-marked" : undefined}>
                {columns.map((column, index) => {
                  const profile = profiles[index];
                  const text = valueText(profile, row[index]);
                  const numeric = profile?.numeric;
                  const raw = numeric ? numericValue(profile, row[index]) : null;
                  const range = ranges[index] || 0;
                  const width =
                    raw != null && range > 0
                      ? Math.max(4, Math.min(100, (Math.abs(raw) / range) * 100))
                      : 0;
                  return (
                    <td key={column} className={cellClass(profile, text)}>
                      {numeric && !profile?.isRank && !profile?.isId && width > 0 ? (
                        <span className="smart-table-bar" style={{ width: `${width}%` }} />
                      ) : null}
                      <span className="smart-table-value">{text}</span>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  return (
    <div className="smart-table-card">
      <div className="smart-table-head">
        <strong>{title}</strong>
        <span>共 {total} 行{total > rows.length ? ` · 已返回 ${rows.length} 行` : ""}</span>
        <div className="smart-table-actions">
          <button type="button" onClick={() => void copyTable()} title="复制表格">
            <CopyOutlined />
            <span>复制</span>
          </button>
          <button type="button" onClick={() => setOpen(true)} title="全屏查看">
            <FullscreenOutlined />
            <span>全屏</span>
          </button>
        </div>
      </div>
      {renderTable(visibleRows, false)}
      {total > visibleRows.length ? (
        <button type="button" className="smart-table-more" onClick={() => setOpen(true)}>
          查看全部 {rows.length} 行
        </button>
      ) : null}
      <Modal
        title={title}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width="min(94vw, 1680px)"
        style={{ top: 16 }}
        styles={{ body: { padding: 0, maxHeight: "82vh", overflow: "hidden" } }}
        destroyOnHidden
      >
        <div className="smart-table-modal-body">
          <div className="smart-table-modal-meta">
            <span>共 {total} 行 · {columns.length} 列</span>
            <button type="button" onClick={() => void copyTable()}>
              <CopyOutlined />
              <span>复制表格</span>
            </button>
          </div>
          {renderTable(sortedRows, true)}
        </div>
      </Modal>
    </div>
  );
}
