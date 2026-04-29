import { DesktopOutlined, DownOutlined, FileTextOutlined } from "@ant-design/icons";
import React from "react";
import { useMemo, useState } from "react";
import { ExecutionStep } from "@/hooks/useChat";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  steps: ExecutionStep[];
  summary?: string;
  selectedStepId?: string;
  onSelectStep?: (stepId: string) => void;
};

function normalizeToText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => normalizeToText(item)).join("\n");
  }
  if (React.isValidElement(value)) {
    return normalizeToText((value as React.ReactElement<{ children?: unknown }>).props?.children);
  }
  try {
    const seen = new WeakSet<object>();
    return JSON.stringify(
      value,
      (_key, val) => {
        if (typeof val === "object" && val !== null) {
          if (seen.has(val)) return "[Circular]";
          seen.add(val);
        }
        return val;
      },
      2
    );
  } catch {
    return String(value);
  }
}

function parseThinkContent(raw: unknown) {
  const text = normalizeToText(raw);
  const thinkRegex = /<think>([\s\S]*?)(?:<\/think>|$)/gi;
  const thinkBlocks: string[] = [];
  let m: RegExpExecArray | null = null;
  while ((m = thinkRegex.exec(text)) !== null) {
    const content = m[1]?.trim();
    if (content) thinkBlocks.push(content);
  }
  const plain = text.replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, "").trim();
  return { thinkBlocks, plain };
}

export default function ChatExecutionPanel({ steps, summary, selectedStepId, onSelectStep }: Props) {
  const [activeTab, setActiveTab] = useState<"steps" | "summary">("steps");
  const [summaryThinkExpanded, setSummaryThinkExpanded] = useState(false);
  const [summaryConclusionExpanded, setSummaryConclusionExpanded] = useState(true);
  const [stepDetailExpanded, setStepDetailExpanded] = useState(true);
  const flowAgents = ["Planner", "DataAnalyst", "Charter", "Summarizer"] as const;

  const selectedStep = useMemo(
    () => steps.find((s) => s.id === selectedStepId) ?? steps[steps.length - 1],
    [steps, selectedStepId]
  );
  const selectedStepTitleText = useMemo(() => normalizeToText(selectedStep?.title), [selectedStep?.title]);
  const selectedStepStatusText = useMemo(() => normalizeToText(selectedStep?.status), [selectedStep?.status]);
  const detailText = useMemo(() => normalizeToText(selectedStep?.detail), [selectedStep?.detail]);
  const summaryText = useMemo(() => normalizeToText(summary), [summary]);
  const parsedDetail = useMemo(() => parseThinkContent(detailText), [detailText]);
  const parsedSummary = useMemo(() => parseThinkContent(summaryText), [summaryText]);
  const detailFallbackText = useMemo(
    () => (detailText ? "" : "点击左侧步骤卡片查看详细执行结果"),
    [detailText]
  );
  const markdownDetailText = useMemo(
    () => normalizeToText(parsedDetail.plain || detailFallbackText),
    [parsedDetail.plain, detailFallbackText]
  );
  const safeMarkdownText = useMemo(() => {
    const normalized = normalizeToText(markdownDetailText);
    return typeof normalized === "string" ? normalized : String(normalized ?? "");
  }, [markdownDetailText]);
  const markdownPlugins = useMemo(() => [remarkGfm], []);
  const isToolResultStep = selectedStepTitleText.startsWith("工具结果:");
  const isStepError =
    selectedStepStatusText === "error" ||
    /execute failed|failed|error|异常|失败/i.test(detailText);
  const stepTitle = (selectedStepTitleText || "选择一个步骤查看详情").replace(/^工具结果:\s*/, "");
  const flowStatus = useMemo(() => {
    const statusMap: Record<string, "idle" | "running" | "done" | "error"> = {};
    flowAgents.forEach((agent) => {
      statusMap[agent] = "idle";
    });
    steps.forEach((step) => {
      const [agent, event] = normalizeToText(step.title).split(":").map((v) => v.trim());
      if (!flowAgents.includes(agent as (typeof flowAgents)[number])) return;
      if (event === "error" || step.status === "error") {
        statusMap[agent] = "error";
      } else if (event === "start" || step.status === "running") {
        if (statusMap[agent] !== "error") statusMap[agent] = "running";
      } else if (event === "end" || step.status === "done") {
        if (statusMap[agent] !== "error") statusMap[agent] = "done";
      }
    });
    return statusMap;
  }, [steps]);

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden border-l border-[#eceff5] bg-[#f8f9fc] dark:border-[#2f3441] dark:bg-[#171b24]">
      <div className="flex h-14 items-center justify-between border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
        <div className="flex items-center gap-2 text-sm font-semibold text-[#1f2937] dark:text-[#e2e8f0]">
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full bg-[#ef4444]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#f59e0b]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#22c55e]" />
          </span>
          <DesktopOutlined className="ml-1 text-[13px]" />
          <span>终端显示</span>
        </div>
        <a className="cursor-pointer text-[12px] text-[#3b82f6]">分享</a>
      </div>

      <div className="flex h-11 items-center justify-between border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
        <div className="flex items-center">
          <button
            onClick={() => setActiveTab("steps")}
            className={`mr-6 pb-2 text-sm font-medium ${
              activeTab === "steps"
                ? "border-b-2 border-black text-[#111827] dark:border-white dark:text-[#f8fafc]"
                : "text-[#94a3b8]"
            }`}
          >
            执行步骤
          </button>
          <button
            onClick={() => setActiveTab("summary")}
            className={`pb-2 text-sm font-medium ${
              activeTab === "summary"
                ? "border-b-2 border-black text-[#111827] dark:border-white dark:text-[#f8fafc]"
                : "text-[#94a3b8]"
            }`}
          >
            摘要
          </button>
        </div>
        <div className="ml-4 flex min-w-0 items-center gap-2 overflow-hidden">
          {flowAgents.map((agent, idx) => {
            const st = flowStatus[agent];
            const color =
              st === "error"
                ? "bg-[#ef4444]"
                : st === "running"
                  ? "bg-[#f59e0b]"
                  : st === "done"
                    ? "bg-[#22c55e]"
                    : "bg-[#cbd5e1]";
            return (
              <div key={agent} className="flex shrink-0 items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${color}`} />
                <span className="text-[11px] text-[#64748b] dark:text-[#94a3b8]">{agent}</span>
                {idx < flowAgents.length - 1 ? <span className="text-[#cbd5e1]">→</span> : null}
              </div>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-scroll overflow-x-hidden p-4 text-[#94a3b8]">
        {activeTab === "steps" && steps.length ? (
          <div className="flex h-full min-w-0 flex-col">
            <div className="flex min-h-0 flex-1 min-w-0 flex-col rounded-lg border border-[#e5e7eb] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
              <button
                onClick={() => setStepDetailExpanded((prev) => !prev)}
                className="mb-2 flex w-full min-w-0 items-center justify-between gap-3 rounded-md border border-[#e5e7eb] bg-white px-2.5 py-1.5 text-left dark:border-[#2f3441] dark:bg-[#11131a]"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <DownOutlined
                    className={`text-[12px] text-[#64748b] transition-transform ${
                      stepDetailExpanded ? "rotate-0" : "-rotate-90"
                    }`}
                  />
                  <div className="truncate text-sm font-semibold text-[#0f172a] dark:text-[#e2e8f0]">
                    {isToolResultStep ? `工具结果: ${stepTitle}` : selectedStepTitleText || "选择一个步骤查看详情"}
                  </div>
                </div>
                {selectedStepStatusText ? (
                  <span
                    className={`ml-2 shrink-0 rounded px-2 py-0.5 text-[11px] ${
                      selectedStepStatusText === "error"
                        ? "bg-[#fee2e2] text-[#b91c1c] dark:bg-[#7f1d1d]/30 dark:text-[#fecaca]"
                        : selectedStepStatusText === "running"
                          ? "bg-[#fef3c7] text-[#92400e] dark:bg-[#78350f]/30 dark:text-[#fde68a]"
                          : "bg-[#dcfce7] text-[#166534] dark:bg-[#14532d]/30 dark:text-[#bbf7d0]"
                    }`}
                  >
                    {selectedStepStatusText}
                  </span>
                ) : null}
              </button>
              <div className="min-h-0 min-w-0 flex-1 overflow-auto">
                {stepDetailExpanded && parsedDetail.thinkBlocks.length ? (
                  <div className="mb-3 space-y-2">
                    {parsedDetail.thinkBlocks.map((block, idx) => (
                      <div key={`think-${idx}`} className="rounded-md border border-[#dbeafe] bg-[#eff6ff] px-3 py-2 dark:border-[#1d4ed8] dark:bg-[#172554]">
                        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">think</div>
                        <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-[#1e3a8a] dark:text-[#bfdbfe]">{block}</pre>
                      </div>
                    ))}
                  </div>
                ) : null}
                {stepDetailExpanded ? (
                  <div
                    className={`rounded-md border px-3 py-2 ${
                      isToolResultStep
                        ? "min-w-0 w-full max-w-full flex-none border-[#1f314f] bg-gray-900 px-4 py-3 overflow-x-auto overflow-y-hidden"
                        : "border-[#dbeafe] bg-[#eff6ff] dark:border-[#1d4ed8] dark:bg-[#172554]"
                    }`}
                  >
                    {isToolResultStep ? (
                      <div
                        className={`prose prose-invert max-w-none w-full max-w-full overflow-x-auto leading-relaxed [&_p]:text-inherit [&_li]:text-inherit [&_strong]:text-inherit [&_strong]:font-bold [&_b]:text-inherit [&_b]:font-bold [&_h1]:text-inherit [&_h1]:text-2xl [&_h1]:leading-9 [&_h1]:font-bold [&_h2]:text-inherit [&_h2]:text-xl [&_h2]:leading-8 [&_h2]:font-semibold [&_h3]:text-inherit [&_h3]:text-lg [&_h3]:leading-7 [&_h3]:font-semibold [&_h4]:text-inherit [&_h5]:text-inherit [&_h6]:text-inherit [&_code]:bg-transparent [&_code]:px-0 [&_code]:font-mono [&_pre]:bg-transparent [&_pre]:p-0 [&_table]:w-max [&_table]:min-w-full [&_table]:border-collapse [&_table]:border [&_table]:border-[#2b425f] [&_th]:border [&_th]:border-[#2b425f] [&_th]:px-2 [&_th]:py-1 [&_th]:font-semibold [&_td]:border [&_td]:border-[#2b425f] [&_td]:px-2 [&_td]:py-1 ${
                          isStepError ? "text-[#fca5a5]" : "text-green-400"
                        }`}
                      >
                        <ReactMarkdown remarkPlugins={markdownPlugins}>{safeMarkdownText}</ReactMarkdown>
                      </div>
                    ) : (
                      <pre className="m-0 text-[#1e3a8a] dark:text-[#bfdbfe]">
                        {markdownDetailText}
                      </pre>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : activeTab === "summary" ? (
          summary ? (
            <div className="space-y-3">
              {parsedSummary.thinkBlocks.length ? (
                <div className="rounded-xl border border-[#e6eefc] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <button
                    onClick={() => setSummaryThinkExpanded((prev) => !prev)}
                    className="flex w-full items-center justify-between text-left"
                  >
                    <span className="text-xs font-semibold tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">
                      思维推理
                    </span>
                    <DownOutlined
                      className={`text-[12px] text-[#64748b] transition-transform ${
                        summaryThinkExpanded ? "rotate-180" : "rotate-0"
                      }`}
                    />
                  </button>
                  {summaryThinkExpanded ? (
                    <div className="mt-2 space-y-2">
                      {parsedSummary.thinkBlocks.map((block, idx) => (
                        <div
                          key={`summary-think-${idx}`}
                          className="rounded-md border border-[#dbeafe] bg-[#eff6ff] px-3 py-2 dark:border-[#1d4ed8] dark:bg-[#172554]"
                        >
                          <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-[#1e3a8a] dark:text-[#bfdbfe]">
                            {block}
                          </pre>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {parsedSummary.plain ? (
                <div className="rounded-xl border border-[#e6eefc] bg-white p-4 dark:border-[#2f3441] dark:bg-[#11131a]">
                  <button
                    onClick={() => setSummaryConclusionExpanded((prev) => !prev)}
                    className="flex w-full items-center justify-between text-left"
                  >
                    <span className="text-xs font-semibold tracking-wide text-[#1d4ed8] dark:text-[#93c5fd]">
                      结论总结
                    </span>
                    <DownOutlined
                      className={`text-[12px] text-[#64748b] transition-transform ${
                        summaryConclusionExpanded ? "rotate-180" : "rotate-0"
                      }`}
                    />
                  </button>
                  {summaryConclusionExpanded ? (
                    <div className="mt-2 rounded-md border border-[#dbeafe] bg-[#eff6ff] p-3 text-sm leading-7 text-[#1e3a8a] dark:border-[#1d4ed8] dark:bg-[#172554] dark:text-[#bfdbfe]">
                      {parsedSummary.plain}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {!parsedSummary.thinkBlocks.length && !parsedSummary.plain ? (
                <div className="flex h-full items-center justify-center text-sm">暂无摘要</div>
              ) : null}
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm">暂无摘要</div>
          )
        ) : steps.length ? (
          <div className="space-y-2">
            {steps.map((step) => (
              <button
                key={step.id}
                onClick={() => onSelectStep?.(step.id)}
                className="w-full rounded-xl border border-[#e5e7eb] bg-white p-3 text-left dark:border-[#2f3441] dark:bg-[#11131a]"
              >
                <div className="text-sm font-medium text-[#0f172a] dark:text-[#e2e8f0]">{normalizeToText(step.title)}</div>
                {step.detail ? (
                  <div className="mt-1 line-clamp-3 text-xs text-[#64748b] dark:text-[#94a3b8]">{normalizeToText(step.detail)}</div>
                ) : null}
              </button>
            ))}
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-2xl bg-white/70 text-3xl dark:bg-white/10">
              <FileTextOutlined />
            </div>
            <div className="text-base">选择一个步骤查看详情</div>
            <div className="mt-2 text-sm">点击左侧的步骤卡片以显示执行结果</div>
          </div>
        )}
      </div>

      <div className="h-7 border-t border-[#e5e7eb] px-4 text-[10px] leading-7 text-[#94a3b8] dark:border-[#2f3441]">
        就绪
      </div>
    </div>
  );
}
