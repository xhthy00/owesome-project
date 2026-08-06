import { RightOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import {
  AGENT_STATUS_LABEL,
  describeTeamCollaboration,
  FLOW_AGENT_META,
  FLOW_AGENTS,
  type AgentFlowStatus,
  type FlowAgent
} from "@/utils/agentTeam";

type Props = {
  statusMap: Record<FlowAgent, AgentFlowStatus>;
  actions: Record<FlowAgent, string>;
  focusAgent?: FlowAgent | null;
  onSelectAgent?: (agent: FlowAgent) => void;
  /** 新问题 / 切换会话轮次时变化，用于把各角色计时清零 */
  timerEpoch?: number | string | null;
};

function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${s}s`;
}

export default function AgentTeamStrip({
  statusMap,
  actions,
  focusAgent,
  onSelectAgent,
  timerEpoch
}: Props) {
  const [tick, setTick] = useState(0);
  const startedAtRef = useRef<Partial<Record<FlowAgent, number>>>({});
  const frozenRef = useRef<Partial<Record<FlowAgent, number>>>({});
  const collabLine = describeTeamCollaboration(statusMap);

  useEffect(() => {
    startedAtRef.current = {};
    frozenRef.current = {};
    const now = Date.now();
    // 同一轮 render 里若角色已是 running，立刻重新打点，
    // 否则 statusMap 未变时不会再进下面的 effect，计时会一直空着或沿用旧值。
    FLOW_AGENTS.forEach((agent) => {
      if (statusMap[agent] === "running") {
        startedAtRef.current[agent] = now;
      }
    });
    setTick((n) => n + 1);
    // 仅在新问题 / 切换 run 时重置；故意不把 statusMap 放进依赖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timerEpoch]);

  useEffect(() => {
    FLOW_AGENTS.forEach((agent) => {
      const st = statusMap[agent];
      if (st === "running") {
        // 同轮多 sub_task 的 done→running 保留首次 start，累计本问耗时；
        // 新问题靠 timerEpoch 清零后再进入此处。
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
    <div className="border-b border-[#eceff5] px-4 py-3 dark:border-[#2f3441]">
      <div className="mb-2 flex items-center gap-2 text-[12px] text-[#64748b] dark:text-[#94a3b8]">
        <span className="inline-flex h-1.5 w-1.5 shrink-0 rounded-full bg-[#3b82f6]" />
        <span className="truncate font-medium text-[#475467] dark:text-[#cbd5e1]">{collabLine}</span>
      </div>
      <div className="flex items-stretch gap-1 sm:gap-1.5">
        {FLOW_AGENTS.map((agent, index) => {
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
          const prevDone = index > 0 && statusMap[FLOW_AGENTS[index - 1]] === "done";
          const arrowActive = prevDone || st === "running" || st === "done";

          return (
            <div key={agent} className="flex min-w-0 flex-1 items-stretch">
              {index > 0 ? (
                <div
                  className={`mx-0.5 hidden shrink-0 items-center self-center sm:flex ${
                    arrowActive ? "text-[#3b82f6]" : "text-[#cbd5e1] dark:text-[#475569]"
                  }`}
                  aria-hidden
                >
                  <RightOutlined className="text-[10px]" />
                </div>
              ) : null}
              <button
                type="button"
                onClick={() => onSelectAgent?.(agent)}
                className={`min-w-0 flex-1 rounded-xl border px-2.5 py-2 text-left transition-shadow ${meta.border} ${meta.bg} ${
                  focused ? "shadow-sm ring-2 ring-[#93c5fd]/70 dark:ring-[#1d4ed8]/50" : "opacity-85"
                } ${st === "idle" ? "opacity-60" : ""}`}
              >
                <div className="flex items-center gap-1.5">
                  <img
                    src={`${meta.avatar}?v=1`}
                    alt=""
                    width={30}
                    height={30}
                    className="h-[30px] w-[30px] shrink-0 rounded-full object-cover shadow-sm ring-1 ring-white/80"
                  />
                  <span className={`truncate text-[13px] font-semibold leading-5 ${meta.text}`}>
                    {meta.identity}
                  </span>
                </div>
                <div className={`mt-1.5 flex items-center gap-1.5 text-[12px] font-medium ${statusTone}`}>
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
                <div className="mt-1 truncate text-[12px] leading-5 text-[#64748b] dark:text-[#94a3b8]">
                  {actions[agent] || (st === "idle" ? "等待上场" : "—")}
                </div>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
