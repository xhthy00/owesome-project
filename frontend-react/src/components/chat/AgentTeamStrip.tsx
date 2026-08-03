import {
  BarChartOutlined,
  CompassOutlined,
  FileTextOutlined,
  LineChartOutlined
} from "@ant-design/icons";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  AGENT_STATUS_LABEL,
  FLOW_AGENT_META,
  FLOW_AGENTS,
  type AgentFlowStatus,
  type FlowAgent
} from "@/utils/agentTeam";

const AGENT_ICON: Record<FlowAgent, ReactNode> = {
  Planner: <CompassOutlined />,
  DataAnalyst: <BarChartOutlined />,
  Charter: <LineChartOutlined />,
  Summarizer: <FileTextOutlined />
};

type Props = {
  statusMap: Record<FlowAgent, AgentFlowStatus>;
  actions: Record<FlowAgent, string>;
  focusAgent?: FlowAgent | null;
  onSelectAgent?: (agent: FlowAgent) => void;
};

function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${s}s`;
}

export default function AgentTeamStrip({ statusMap, actions, focusAgent, onSelectAgent }: Props) {
  const [tick, setTick] = useState(0);
  const startedAtRef = useRef<Partial<Record<FlowAgent, number>>>({});
  const frozenRef = useRef<Partial<Record<FlowAgent, number>>>({});

  useEffect(() => {
    FLOW_AGENTS.forEach((agent) => {
      const st = statusMap[agent];
      if (st === "running") {
        if (startedAtRef.current[agent] == null) {
          startedAtRef.current[agent] = Date.now();
        }
        delete frozenRef.current[agent];
      } else if (st === "done" || st === "error") {
        if (startedAtRef.current[agent] != null && frozenRef.current[agent] == null) {
          frozenRef.current[agent] = Date.now() - startedAtRef.current[agent]!;
        }
      } else {
        delete startedAtRef.current[agent];
        delete frozenRef.current[agent];
      }
    });
  }, [statusMap]);

  useEffect(() => {
    const hasRunning = FLOW_AGENTS.some((a) => statusMap[a] === "running");
    if (!hasRunning) return;
    const id = setInterval(() => setTick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, [statusMap]);

  void tick;

  return (
    <div className="grid grid-cols-4 gap-2 border-b border-[#eceff5] px-4 py-3 dark:border-[#2f3441]">
      {FLOW_AGENTS.map((agent) => {
        const st = statusMap[agent];
        const meta = FLOW_AGENT_META[agent];
        const focused = focusAgent === agent || st === "running";
        const elapsedMs =
          st === "running" && startedAtRef.current[agent] != null
            ? Date.now() - startedAtRef.current[agent]!
            : frozenRef.current[agent];
        const statusTone =
          st === "error"
            ? "text-[#b91c1c]"
            : st === "running"
              ? "text-[#b45309]"
              : st === "done"
                ? "text-[#166534]"
                : "text-[#94a3b8]";

        return (
          <button
            key={agent}
            type="button"
            onClick={() => onSelectAgent?.(agent)}
            className={`min-w-0 rounded-xl border px-2.5 py-2 text-left transition-shadow ${meta.border} ${meta.bg} ${
              focused ? "shadow-sm ring-2 ring-[#93c5fd]/70 dark:ring-[#1d4ed8]/50" : "opacity-85"
            } ${st === "idle" ? "opacity-60" : ""}`}
          >
            <div className="flex items-center gap-1.5">
              <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] text-white ${meta.accent}`}>
                {AGENT_ICON[agent]}
              </span>
              <span className={`truncate text-[11px] font-semibold leading-4 ${meta.text}`}>{meta.identity}</span>
            </div>
            <div className={`mt-1.5 flex items-center gap-1.5 text-[10px] font-medium ${statusTone}`}>
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  st === "error"
                    ? "bg-[#ef4444]"
                    : st === "running"
                      ? "bg-[#f59e0b]"
                      : st === "done"
                        ? "bg-[#22c55e]"
                        : "bg-[#cbd5e1]"
                }`}
              />
              <span>{AGENT_STATUS_LABEL[st]}</span>
              {elapsedMs != null && (st === "running" || st === "done" || st === "error") ? (
                <span className="tabular-nums text-[#94a3b8]">{formatElapsed(elapsedMs)}</span>
              ) : null}
            </div>
            <div className="mt-1 truncate text-[10px] leading-4 text-[#64748b] dark:text-[#94a3b8]">
              {actions[agent] || (st === "idle" ? "等待上场" : "—")}
            </div>
          </button>
        );
      })}
    </div>
  );
}
