import { ArrowDownOutlined, ArrowUpOutlined, MinusOutlined } from "@ant-design/icons";
import { useMemo } from "react";
import { buildKpis } from "@/utils/chartKpi";

type Props = {
  columns: string[];
  rows: unknown[][];
};

function DeltaIcon({ tone }: { tone: "up" | "down" | "neutral" }) {
  if (tone === "up") return <ArrowUpOutlined aria-hidden="true" />;
  if (tone === "down") return <ArrowDownOutlined aria-hidden="true" />;
  return <MinusOutlined aria-hidden="true" />;
}

export default function KpiStrip({ columns, rows }: Props) {
  const kpis = useMemo(() => buildKpis(columns, rows), [columns, rows]);
  if (!kpis.length) return null;
  return (
    <div className="answer-kpis" role="group" aria-label="关键指标">
      {kpis.map((kpi) => (
        <div className="answer-kpi" key={`${kpi.label}-${kpi.value}`} title={kpi.rawName}>
          <div className="answer-kpi-label">{kpi.label}</div>
          <div className="answer-kpi-value">
            {kpi.value}
            {kpi.unit ? <span className="answer-kpi-unit">{kpi.unit}</span> : null}
          </div>
          {kpi.delta ? (
            <div className={`answer-kpi-delta is-${kpi.deltaTone || "neutral"}`}>
              <DeltaIcon tone={kpi.deltaTone || "neutral"} />
              <span>{kpi.delta}</span>
            </div>
          ) : null}
          {kpi.hint ? <div className="answer-kpi-hint">{kpi.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}
