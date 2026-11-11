import { ExpandOutlined } from "@ant-design/icons";
import { useMemo } from "react";
import ChartView from "@/components/charts/ChartView";
import { buildSecondarySpec, resolveChartSpec } from "@/components/charts/chartRecommend";
import { specTitle, type ChartSpec } from "@/components/charts/chartSpec";
import KpiStrip from "@/components/chat/KpiStrip";
import SmartTable from "@/components/chat/SmartTable";

type Props = {
  question: string;
  columns: string[];
  rows: unknown[][];
  rowCount?: number;
  spec?: ChartSpec | null;
  legacyConfig?: unknown;
  legacyChartType?: string;
  onOpenPreview?: () => void;
};

export default function AnswerVisual({
  question,
  columns,
  rows,
  rowCount,
  spec,
  legacyConfig,
  legacyChartType,
  onOpenPreview
}: Props) {
  const resolved = useMemo(
    () =>
      resolveChartSpec({
        spec: spec ?? null,
        legacyConfig,
        chartType: legacyChartType,
        columns,
        rows,
        question
      }),
    [spec, legacyConfig, legacyChartType, columns, rows, question]
  );
  const primary = useMemo(() => {
    if (!resolved || resolved.type === "kpi" || resolved.type === "table") return null;
    return resolved;
  }, [resolved]);
  const secondary = useMemo(
    () => buildSecondarySpec(primary, columns, rows),
    [primary, columns, rows]
  );
  if (!rows.length) return null;

  return (
    <section className="answer-visual" aria-label="数据可视化">
      <div className="answer-visual-head">
        <span className="answer-visual-kicker">数据洞察</span>
        <span className="answer-visual-meta">
          {rows.length} 行结果
          {rowCount && rowCount > rows.length ? ` · 共 ${rowCount} 行` : ""}
        </span>
      </div>
      <KpiStrip columns={columns} rows={rows} />
      {primary ? (
        <div className="answer-chart-card">
          <div className="answer-chart-head">
            <div>
              <h3>{specTitle(primary)}</h3>
              {primary.subtitle ? <p>{primary.subtitle}</p> : null}
            </div>
            <span className="answer-chart-tag">主图</span>
          </div>
          <ChartView spec={primary} rows={rows} columns={columns} height={280} />
        </div>
      ) : null}
      {secondary ? (
        <div className="answer-chart-card is-secondary">
          <div className="answer-chart-head">
            <div>
              <h3>{specTitle(secondary)}</h3>
              {secondary.subtitle ? <p>{secondary.subtitle}</p> : null}
            </div>
            <span className="answer-chart-tag is-muted">辅图</span>
          </div>
          <ChartView spec={secondary} rows={rows} columns={columns} height={180} />
        </div>
      ) : null}
      <SmartTable columns={columns} rows={rows} rowCount={rowCount} title="明细数据" maxRows={6} />
      {onOpenPreview ? (
        <div className="answer-actions">
          <button type="button" onClick={onOpenPreview}>
            <ExpandOutlined />
            <span>展开完整结果</span>
          </button>
        </div>
      ) : null}
    </section>
  );
}
