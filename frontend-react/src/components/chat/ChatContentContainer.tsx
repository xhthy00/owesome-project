import {
  CheckCircleOutlined,
  DownOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  LoadingOutlined,
  VerticalAlignBottomOutlined,
  VerticalAlignTopOutlined
} from "@ant-design/icons";
import { Empty } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { ExecutionStep, Message } from "@/hooks/useChat";
import OpenCodeChatCompletion from "@/new-components/chat/content/OpenCodeChatCompletion";

type Props = {
  messages: Message[];
  steps?: ExecutionStep[];
  selectedStepId?: string;
  onSelectStep?: (stepId: string) => void;
};

function toText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default function ChatContentContainer({ messages, steps = [], selectedStepId, onSelectStep }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [isAtTop, setIsAtTop] = useState(true);
  const [isAtBottom, setIsAtBottom] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    plan: true,
    step: true,
    result: true
  });

  useEffect(() => {
    if (!ref.current) return;
    ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages, steps]);

  useEffect(() => {
    const ele = ref.current;
    if (!ele) return;
    const onScroll = () => {
      const buffer = 16;
      setIsAtTop(ele.scrollTop <= buffer);
      setIsAtBottom(ele.scrollTop + ele.clientHeight >= ele.scrollHeight - buffer);
    };
    onScroll();
    ele.addEventListener("scroll", onScroll);
    return () => ele.removeEventListener("scroll", onScroll);
  }, []);

  const showButtons = useMemo(() => {
    if (!ref.current) return false;
    return ref.current.scrollHeight > ref.current.clientHeight;
  }, [messages.length, steps.length]);

  const scrollToBottom = () => {
    const ele = ref.current;
    if (!ele) return;
    ele.scrollTo({ top: ele.scrollHeight, behavior: "smooth" });
  };

  const isEmpty = !messages.length;
  const userMessages = messages.filter((m) => m.role === "user" && toText(m.content).trim());
  const hasSteps = steps.length > 0;
  const realPlanSteps = steps.filter((s) => s.section === "plan" && s.id !== "plan-bootstrap");
  const hasRealProgress = steps.some((s) => s.section !== "plan" || s.id.startsWith("tool-"));
  const planSteps = hasRealProgress ? realPlanSteps : steps.filter((s) => s.section === "plan");
  const toolSteps = steps.filter((s) => s.id.startsWith("tool-"));
  const derivedPlanSteps = (() => {
    if (planSteps.length > 0) return planSteps;
    const subTaskSet = new Set<number>();
    steps.forEach((s) => {
      if (typeof s.subTaskIndex === "number") subTaskSet.add(s.subTaskIndex);
    });
    const bySubTask = Array.from(subTaskSet)
      .sort((a, b) => a - b)
      .map((idx) => ({
        id: `derived-plan-${idx}`,
        title: `计划 ${idx + 1}: 子任务 ${idx + 1}`,
        detail: "根据执行过程自动推断的子任务计划",
        status: "running" as const,
        section: "plan" as const,
        subTaskIndex: idx,
        progressPct: 0
      }));
    if (bySubTask.length > 0) return bySubTask;
    if (steps.length > 0) {
      const latestUserQuestion = toText([...messages].reverse().find((m) => m.role === "user")?.content).trim() || "当前任务";
      return [
        {
          id: "derived-plan-fallback",
          title: `计划 1: ${latestUserQuestion}`,
          detail: "执行计划未显式返回，已按当前任务兜底展示",
          status: "running" as const,
          section: "plan" as const,
          subTaskIndex: 0,
          progressPct: 0
        }
      ];
    }
    return [];
  })();
  const groupBySubTask = (list: ExecutionStep[]) => {
    const map = new Map<number | "none", ExecutionStep[]>();
    list.forEach((item) => {
      const key = typeof item.subTaskIndex === "number" ? item.subTaskIndex : "none";
      const arr = map.get(key) ?? [];
      arr.push(item);
      map.set(key, arr);
    });
    return Array.from(map.entries()).sort((a, b) => {
      if (a[0] === "none") return -1;
      if (b[0] === "none") return 1;
      return Number(a[0]) - Number(b[0]);
    });
  };
  const toolGroups = groupBySubTask(toolSteps);
  const toolCount = toolSteps.length;

  const statusStyle = (status: ExecutionStep["status"]) => {
    if (status === "error") {
      return "bg-[#ef4444]";
    }
    if (status === "running") {
      return "bg-[#f59e0b]";
    }
    return "bg-[#22c55e]";
  };

  const doneCount = (list: ExecutionStep[]) => list.filter((s) => s.status === "done").length;
  const planProgress = derivedPlanSteps.length ? Math.round((doneCount(derivedPlanSteps) / derivedPlanSteps.length) * 100) : 0;
  const toggleGroup = (key: "plan" | "step" | "result") =>
    setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="relative h-full min-h-0">
      <div ref={ref} className="h-full min-h-0 overflow-y-auto overflow-x-hidden pr-2">
        {hasSteps ? (
          <div className="mx-auto flex w-[95%] flex-col gap-3 py-3">
            {userMessages.length ? (
              <div className="space-y-2">
                {userMessages.map((msg) => (
                  <div key={msg.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl bg-white px-4 py-3 dark:bg-[#2a2b2f]">
                      <div className="dbgpt-ui-font whitespace-pre-wrap text-sm font-semibold leading-relaxed text-black dark:text-white">
                        {toText(msg.content)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="rounded-2xl border border-[#e7eaf0] bg-white p-4 shadow-[0_1px_3px_rgba(16,24,40,0.04)] dark:border-[#2f3441] dark:bg-[#111723]">
              <div className="mb-3 text-sm font-semibold text-[#344054] dark:text-[#cbd5e1]">执行情况</div>

              <section className="rounded-xl border border-[#cbe2ff] bg-[#eaf4ff] dark:border-[#2f4b75] dark:bg-[#17263b]">
                <button onClick={() => toggleGroup("plan")} className="flex w-full items-center gap-2 px-3 py-2 text-left">
                  <DownOutlined className={`text-[10px] text-[#98a2b3] transition-transform ${expandedGroups.plan ? "rotate-0" : "-rotate-90"}`} />
                  <span className="text-sm font-semibold text-[#1f2937] dark:text-[#e2e8f0]">执行计划</span>
                  <span className="ml-auto text-xs font-semibold text-[#1677ff]">{planProgress}%</span>
                  <span className="text-xs text-[#667085]">{derivedPlanSteps.length} 步</span>
                </button>
                <div className="px-3 pb-2">
                  <div className="h-1.5 overflow-hidden rounded-full bg-[#dbe7ff] dark:bg-[#2a3c61]">
                    <div className="h-full rounded-full bg-[#3b82f6] transition-all" style={{ width: `${planProgress}%` }} />
                  </div>
                </div>
                {expandedGroups.plan ? (
                  <div className="space-y-1 px-3 pb-3">
                    {derivedPlanSteps.map((step) => (
                      <button
                        key={step.id}
                        onClick={() => onSelectStep?.(step.id)}
                        className={`w-full rounded-md border px-2 py-1.5 text-left ${
                          selectedStepId === step.id
                            ? "border-[#91caff] bg-[#f0f7ff] dark:border-[#3b82f6] dark:bg-[#1e293b]"
                            : "border-[#d9e7f7] bg-white/90 dark:border-[#36506e] dark:bg-[#0f1a29]"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex min-w-0 items-center gap-2">
                            <span
                              className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full border border-white shadow-[0_0_0_1px_rgba(148,163,184,0.25)] ${statusStyle(step.status)}`}
                            />
                            <span className="truncate text-xs font-medium text-[#1f2937] dark:text-[#e2e8f0]">{toText(step.title).replace(/^计划 \d+:\s*/, "")}</span>
                          </div>
                          <div className="flex shrink-0 items-center gap-2 text-[11px] text-[#667085] dark:text-[#94a3b8]">
                            <span className="font-semibold text-[#1677ff]">{step.progressPct ?? (step.status === "done" ? 100 : 0)}%</span>
                            <span>{typeof step.rowCount === "number" ? `共 ${step.rowCount} 行` : "--"}</span>
                          </div>
                        </div>
                        {step.detail ? (
                          <div className="mt-1 pl-4 text-[11px] text-[#98a2b3] dark:text-[#64748b]">{toText(step.detail)}</div>
                        ) : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </section>

              <section className="mt-3 rounded-xl border border-[#e7eaf0] bg-[#f8fafc] dark:border-[#2f3441] dark:bg-[#0f141f]">
                <button onClick={() => toggleGroup("step")} className="flex w-full items-center gap-2 px-3 py-2 text-left">
                  <DownOutlined className={`text-[10px] text-[#98a2b3] transition-transform ${expandedGroups.step ? "rotate-0" : "-rotate-90"}`} />
                  <span className="text-sm font-semibold text-[#1f2937] dark:text-[#e2e8f0]">工具调用</span>
                  <span className="ml-auto text-xs text-[#667085]">{toolCount} 次</span>
                </button>
                {expandedGroups.step ? (
                  <div className="space-y-2 px-3 pb-3">
                    {toolGroups.map(([subTaskKey, items]) => (
                      <div key={`tool-${String(subTaskKey)}`} className="space-y-1.5">
                        <div className="text-xs font-medium text-[#98a2b3] dark:text-[#64748b]">
                          {subTaskKey === "none" ? "全局" : `子任务 ${Number(subTaskKey) + 1}`}
                          {subTaskKey !== "none" && derivedPlanSteps[Number(subTaskKey)]
                            ? `：${toText(derivedPlanSteps[Number(subTaskKey)].title).replace(/^计划 \d+:\s*/, "")}`
                            : ""}
                        </div>
                        {items
                          .slice()
                          .sort((a, b) => (a.round ?? 0) - (b.round ?? 0))
                          .map((step) => (
                            <button
                              key={step.id}
                              onClick={() => onSelectStep?.(step.id)}
                              className={`relative w-full rounded-lg border px-3 py-2 text-left ${
                                selectedStepId === step.id
                                  ? "border-[#91caff] bg-[#f0f7ff] dark:border-[#3b82f6] dark:bg-[#1e293b]"
                                  : "border-[#e7eaf0] bg-white dark:border-[#2f3441] dark:bg-[#111723]"
                              }`}
                            >
                              <span
                                className={`absolute left-0 top-2 h-[36px] w-[2px] rounded-r ${
                                  step.status === "error"
                                    ? "bg-[#ef4444]"
                                    : step.status === "done"
                                      ? "bg-[#22c55e]"
                                      : "bg-[#f59e0b]"
                                }`}
                              />
                              <div className="flex items-center justify-between gap-3 pl-2">
                                <div className="flex min-w-0 items-center gap-2">
                                  <FileTextOutlined className="text-[14px] text-[#98a2b3]" />
                                  <span className="text-[11px] text-[#98a2b3]">操作</span>
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="truncate text-sm font-semibold text-[#1f2937] dark:text-[#e2e8f0]">{toText(step.title).replace(/^工具结果:\s*/, "")}</div>
                                  <div className="truncate text-[11px] text-[#98a2b3]">Action: {toText(step.title).replace(/^工具结果:\s*/, "").toLowerCase()}</div>
                                </div>
                                {step.status === "running" ? (
                                  <LoadingOutlined className="shrink-0 text-[14px] text-[#f59e0b]" />
                                ) : step.status === "error" ? (
                                  <ExclamationCircleOutlined className="shrink-0 text-[14px] text-[#ef4444]" />
                                ) : (
                                  <CheckCircleOutlined className="shrink-0 text-[14px] text-[#22c55e]" />
                                )}
                              </div>
                              {step.detail ? (
                                <div className="mt-1.5 pl-[62px] line-clamp-2 whitespace-pre-wrap text-xs leading-5 text-[#6b7280] dark:text-[#94a3b8]">
                                  {toText(step.detail)}
                                </div>
                              ) : null}
                            </button>
                          ))}
                      </div>
                    ))}
                  </div>
                ) : null}
              </section>
            </div>
          </div>
        ) : isEmpty ? (
          <Empty description={<span className="dbgpt-ui-font text-sm leading-6">等待开始...</span>} />
        ) : (
          <OpenCodeChatCompletion messages={messages} />
        )}
      </div>
      {showButtons && (
        <div className="float-button-container z-scroll-buttons absolute bottom-[100px] right-4 flex flex-col gap-2">
          {!isAtTop && (
            <button
              className="h-10 w-10 rounded-full border border-gray-200 bg-white shadow-md transition-all duration-200 hover:shadow-lg dark:border-[rgba(255,255,255,0.2)] dark:bg-[rgba(255,255,255,0.2)]"
              onClick={() => ref.current?.scrollTo({ top: 0, behavior: "smooth" })}
            >
              <VerticalAlignTopOutlined />
            </button>
          )}
          {!isAtBottom && (
            <button
              className="h-10 w-10 rounded-full border border-gray-200 bg-white shadow-md transition-all duration-200 hover:shadow-lg dark:border-[rgba(255,255,255,0.2)] dark:bg-[rgba(255,255,255,0.2)]"
              onClick={scrollToBottom}
            >
              <VerticalAlignBottomOutlined />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
