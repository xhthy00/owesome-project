import type { ExecutionStep } from "@/hooks/useChat";
import { AGENT_IDENTITY, resolveStepAgent } from "@/utils/toolLabels";

export const FLOW_AGENTS = ["Planner", "DataAnalyst", "Charter", "Summarizer"] as const;
export type FlowAgent = (typeof FLOW_AGENTS)[number];
export type AgentFlowStatus = "idle" | "running" | "done" | "error";

export const FLOW_AGENT_META: Record<
  FlowAgent,
  { identity: string; accent: string; border: string; bg: string; text: string }
> = {
  Planner: {
    identity: AGENT_IDENTITY.Planner,
    accent: "bg-[#3b82f6]",
    border: "border-[#93c5fd]",
    bg: "bg-[#eff6ff]",
    text: "text-[#1d4ed8]"
  },
  DataAnalyst: {
    identity: AGENT_IDENTITY.DataAnalyst,
    accent: "bg-[#22c55e]",
    border: "border-[#86efac]",
    bg: "bg-[#f0fdf4]",
    text: "text-[#166534]"
  },
  Charter: {
    identity: AGENT_IDENTITY.Charter,
    accent: "bg-[#f59e0b]",
    border: "border-[#fcd34d]",
    bg: "bg-[#fffbeb]",
    text: "text-[#92400e]"
  },
  Summarizer: {
    identity: AGENT_IDENTITY.Summarizer,
    accent: "bg-[#8b5cf6]",
    border: "border-[#c4b5fd]",
    bg: "bg-[#f5f3ff]",
    text: "text-[#5b21b6]"
  }
};

export const AGENT_STATUS_LABEL: Record<AgentFlowStatus, string> = {
  idle: "未开始",
  running: "进行中",
  done: "已完成",
  error: "失败"
};

function normalizeAgentKey(raw: string): FlowAgent | null {
  const hit = FLOW_AGENTS.find((a) => a.toLowerCase() === raw.toLowerCase());
  if (hit) return hit;
  // ToolExpert 与 DataAnalyst 同属分析阶段，状态归入数据分析专家
  if (/^ToolExpert$/i.test(raw)) return "DataAnalyst";
  return null;
}

/** 将步骤归属到四角色之一；ToolExpert 并入数据分析专家 */
export function assignStepToAgent(step: ExecutionStep): FlowAgent {
  const title = (step.title || "").trim();
  const detail = (step.detail || "").trim();

  // 计划步骤 detail 常带「执行角色: ToolExpert」，须优先归规划师，避免明细被抽空
  if (step.section === "plan" || /^准备执行计划|^计划\s*\d+/i.test(title)) {
    return "Planner";
  }

  const fromTitle = resolveStepAgent(title);
  if (fromTitle) {
    return normalizeAgentKey(fromTitle) ?? "DataAnalyst";
  }

  const role = detail.match(/执行角色:\s*(\w+)/i)?.[1];
  if (role) {
    return normalizeAgentKey(role) ?? "DataAnalyst";
  }

  if (/^Summarizer\s*:/i.test(title) || /结论整理|学情总结/.test(title)) {
    return "Summarizer";
  }
  if (/^Charter\s*:/i.test(title) || /图表推荐|生成报告/.test(title)) {
    return "Charter";
  }
  return "DataAnalyst";
}

function isLifecycleStep(step: ExecutionStep): boolean {
  return /^(Planner|DataAnalyst|Charter|Summarizer|ToolExpert)\s*:\s*(start|end|done|error)$/i.test(
    (step.title || "").trim()
  );
}

export type AgentStepGroup = {
  agent: FlowAgent;
  status: AgentFlowStatus;
  steps: ExecutionStep[];
};

/** 状态仅由 agent_speak（及收口时的 running→done）决定，不按报告/摘要臆测 */
export function computeFlowStatus(steps: ExecutionStep[]): Record<FlowAgent, AgentFlowStatus> {
  const statusMap = Object.fromEntries(FLOW_AGENTS.map((a) => [a, "idle" as AgentFlowStatus])) as Record<
    FlowAgent,
    AgentFlowStatus
  >;

  steps.forEach((step) => {
    const m = (step.title || "").match(
      /^(Planner|DataAnalyst|Charter|Summarizer|ToolExpert)\s*:\s*(\w+)/i
    );
    if (!m) return;
    const agent = normalizeAgentKey(m[1]);
    if (!agent) return;
    const event = m[2].toLowerCase();
    if (event === "error" || step.status === "error") {
      statusMap[agent] = "error";
      return;
    }
    if (event === "end" || event === "done") {
      if (statusMap[agent] !== "error") statusMap[agent] = "done";
      return;
    }
    if (event === "start" && (statusMap[agent] === "idle" || statusMap[agent] === "done")) {
      // 多 sub_task 时后一次 start 盖过前一次 done → running
      statusMap[agent] = "running";
    }
  });

  const hasActive = steps.some((step) => {
    if (step.status !== "running") return false;
    const m = (step.title || "").match(
      /^(Planner|DataAnalyst|Charter|Summarizer|ToolExpert)\s*:\s*start$/i
    );
    if (m) {
      const agent = m[1];
      return !steps.some((x) =>
        new RegExp(`^${agent}\\s*:\\s*(end|done)$`, "i").test(x.title || "")
      );
    }
    return true;
  });

  if (steps.length > 0 && !hasActive) {
    FLOW_AGENTS.forEach((agent) => {
      if (statusMap[agent] === "running") statusMap[agent] = "done";
    });
  }

  return statusMap;
}

function groupStepsRaw(steps: ExecutionStep[]): Record<FlowAgent, ExecutionStep[]> {
  const map = Object.fromEntries(FLOW_AGENTS.map((a) => [a, [] as ExecutionStep[]])) as Record<
    FlowAgent,
    ExecutionStep[]
  >;
  steps.forEach((step) => {
    map[assignStepToAgent(step)].push(step);
  });
  return map;
}

function visibleStepsForAgent(steps: ExecutionStep[]): ExecutionStep[] {
  const work = steps.filter((s) => !isLifecycleStep(s));
  if (work.length) return work;
  // Charter/Summarizer 往往只有 agent_speak：保留 end/error，避免面板空白
  return steps.filter((s) => {
    const m = (s.title || "").match(/:\s*(\w+)\s*$/i);
    const ev = (m?.[1] || "").toLowerCase();
    return ev === "end" || ev === "done" || ev === "error";
  });
}

/** 按四角色分组；有实质步骤时隐藏纯 start；无实质步骤时展示 end/error */
export function groupStepsByAgent(
  steps: ExecutionStep[],
  statusMap?: Record<FlowAgent, AgentFlowStatus>
): AgentStepGroup[] {
  const status = statusMap ?? computeFlowStatus(steps);
  const raw = groupStepsRaw(steps);
  return FLOW_AGENTS.map((agent) => ({
    agent,
    status: status[agent],
    steps: visibleStepsForAgent(raw[agent])
  }));
}
