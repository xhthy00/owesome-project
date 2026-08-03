import {
  CheckCircleOutlined,
  DownOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  RobotOutlined,
  UserOutlined
} from "@ant-design/icons";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ASSISTANT_NAME,
  ASSISTANT_THINKING,
  STORY_FOLD_TITLE,
  THINK_FOLD_TITLE
} from "@/new-components/chat/assistant";
import { normalizeAssistantMarkdown, type StoryPhase } from "@/utils/toolLabels";
import { formatElapsed, formatTokenCount, type RunMetrics } from "@/utils/runMetrics";

type Props = {
  userMessage: string;
  assistantMessage?: string;
  activity?: string;
  isWorking?: boolean;
  thinkText?: string;
  storySummary?: string;
  storyPhases?: StoryPhase[];
  followups?: string[];
  selectedStepId?: string;
  onSelectStep?: (stepId: string) => void;
  onFollowup?: (text: string) => void;
  runMetrics?: RunMetrics | null;
};

function PhaseIcon({ status }: { status: StoryPhase["status"] }) {
  if (status === "running") return <LoadingOutlined className="text-[#f59e0b]" />;
  if (status === "error") return <ExclamationCircleOutlined className="text-[#ef4444]" />;
  if (status === "done") return <CheckCircleOutlined className="text-[#22c55e]" />;
  return <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#d1d5db]" />;
}

export default function OpenCodeSessionTurn({
  userMessage,
  assistantMessage,
  activity,
  isWorking,
  thinkText,
  storySummary,
  storyPhases = [],
  followups = [],
  selectedStepId,
  onSelectStep,
  onFollowup,
  runMetrics = null
}: Props) {
  const [thinkOpen, setThinkOpen] = useState(false);
  const statusLine = activity || (isWorking && !assistantMessage ? ASSISTANT_THINKING : "");
  const phasesToShow = storyPhases.filter((p) => p.status !== "idle");
  // 实时轮：有开始时间或进行中；历史轮：只要传入了 metrics（含 token/耗时未知时的 —）
  const showMetrics = Boolean(
    runMetrics &&
      (isWorking ||
        runMetrics.runStartedAt != null ||
        runMetrics.tokenKnown ||
        runMetrics.elapsedKnown ||
        runMetrics.progressPct >= 100)
  );
  const showProgress = Boolean(runMetrics && (isWorking || runMetrics.runStartedAt != null));

  return (
    <div className="dbgpt-ui-font flex flex-col gap-3 py-2" data-component="session-turn">
      <div className="flex justify-end">
        <div className="flex max-w-[90%] gap-2">
          <div className="rounded-2xl bg-white px-4 py-3 shadow-[0_1px_2px_rgba(16,24,40,0.04)] dark:bg-[#2a2b2f]">
            <div className="whitespace-pre-wrap text-sm font-semibold leading-relaxed text-black dark:text-white">
              {userMessage}
            </div>
          </div>
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#1677ff] text-white">
            <UserOutlined />
          </div>
        </div>
      </div>

      <div className="flex gap-2">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#e8f3ff] text-[#0c75fc] dark:bg-[#1e293b]">
          <RobotOutlined />
        </div>
        <div className="min-w-0 flex-1 rounded-2xl border border-[#e7eaf0] bg-white p-4 shadow-[0_1px_3px_rgba(16,24,40,0.04)] dark:border-[#2f3441] dark:bg-[#111723]">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-semibold text-[#0c75fc]">{ASSISTANT_NAME}</span>
            {showMetrics && runMetrics ? (
              <span className="run-metrics-blink text-[13px] font-bold tabular-nums">
                Token {formatTokenCount(runMetrics.totalTokens, runMetrics.tokenKnown)}
                {" · "}
                耗时 {formatElapsed(runMetrics.elapsedMs, runMetrics.elapsedKnown)}
                {showProgress ? (
                  <>
                    {" · "}
                    进度 {Math.min(100, Math.max(0, runMetrics.progressPct))}%
                  </>
                ) : null}
              </span>
            ) : null}
          </div>
          {showProgress && runMetrics ? (
            <div className="mb-3 h-1 overflow-hidden rounded-full bg-[#e8eef8] dark:bg-[#1e293b]">
              <div
                className="h-full rounded-full bg-[#3b82f6] transition-[width] duration-300 ease-out"
                style={{ width: `${Math.min(100, Math.max(0, runMetrics.progressPct))}%` }}
              />
            </div>
          ) : null}
          {isWorking || statusLine ? (
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-[#bfdbfe] bg-[#eff6ff] px-3 py-2 dark:border-[#1e3a5f] dark:bg-[#172554]">
              <LoadingOutlined className="shrink-0 text-[#2563eb]" />
              <span className="text-xs leading-5 text-[#1d4ed8] dark:text-[#93c5fd]">
                {statusLine || ASSISTANT_THINKING}
              </span>
            </div>
          ) : null}

          {thinkText ? (
            <div className="mb-2 rounded-lg border border-[#e8eef8] bg-[#f8fafc] dark:border-[#2f3441] dark:bg-[#0f141f]">
              <button
                type="button"
                onClick={() => setThinkOpen((v) => !v)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left"
              >
                <DownOutlined
                  className={`text-[10px] text-[#98a2b3] transition-transform ${thinkOpen ? "rotate-0" : "-rotate-90"}`}
                />
                <span className="text-xs font-medium text-[#64748b]">{THINK_FOLD_TITLE}</span>
              </button>
              {thinkOpen ? (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words px-3 pb-2 text-xs leading-5 text-[#64748b]">
                  {thinkText}
                </pre>
              ) : null}
            </div>
          ) : null}

          {phasesToShow.length > 0 || storySummary ? (
            <div className="mb-3 rounded-xl border border-[#e8eef8] bg-gradient-to-b from-[#f8fafc] to-white p-3 dark:border-[#2f3441] dark:from-[#0f141f] dark:to-[#111723]">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-[#344054] dark:text-[#cbd5e1]">{STORY_FOLD_TITLE}</span>
                {storySummary ? (
                  <span className="truncate text-[11px] text-[#98a2b3]">{storySummary}</span>
                ) : null}
              </div>
              <div className="relative space-y-0">
                {phasesToShow.map((phase, idx) => {
                  const selected = phase.stepId && selectedStepId === phase.stepId;
                  const isLast = idx === phasesToShow.length - 1;
                  return (
                    <div key={phase.id} className="relative flex gap-3">
                      <div className="flex w-5 shrink-0 flex-col items-center">
                        <div
                          className={`z-[1] flex h-5 w-5 items-center justify-center rounded-full border bg-white text-[11px] dark:bg-[#111723] ${
                            phase.status === "running"
                              ? "border-[#f59e0b]"
                              : phase.status === "done"
                                ? "border-[#86efac]"
                                : phase.status === "error"
                                  ? "border-[#fca5a5]"
                                  : "border-[#e5e7eb]"
                          }`}
                        >
                          <PhaseIcon status={phase.status} />
                        </div>
                        {!isLast ? (
                          <div
                            className={`my-0.5 w-px flex-1 min-h-[18px] ${
                              phase.status === "done" ? "bg-[#86efac]" : "bg-[#e5e7eb] dark:bg-[#334155]"
                            }`}
                          />
                        ) : null}
                      </div>
                      <button
                        type="button"
                        disabled={!phase.stepId}
                        onClick={() => phase.stepId && onSelectStep?.(phase.stepId)}
                        className={`mb-2 min-w-0 flex-1 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                          selected
                            ? "bg-[#eef6ff] dark:bg-[#1e293b]"
                            : phase.status === "idle"
                              ? "opacity-50"
                              : "hover:bg-white/80 dark:hover:bg-[#1a2030]"
                        } ${phase.stepId ? "cursor-pointer" : "cursor-default"}`}
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span
                            className={`text-xs font-semibold ${
                              phase.status === "running"
                                ? "text-[#b45309]"
                                : "text-[#1f2937] dark:text-[#e2e8f0]"
                            }`}
                          >
                            {phase.title}
                          </span>
                          {phase.count > 1 ? (
                            <span className="shrink-0 text-[10px] text-[#94a3b8]">×{phase.count}</span>
                          ) : null}
                        </div>
                        <div className="mt-0.5 text-[11px] leading-5 text-[#64748b] dark:text-[#94a3b8]">
                          {phase.tip}
                        </div>
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {assistantMessage ? (
            <div
              className="assistant-md max-w-none overflow-x-auto text-sm leading-6 text-[#1f2937] dark:text-[#e2e8f0]
                [&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-lg [&_h1]:font-bold
                [&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-base [&_h2]:font-semibold
                [&_h3]:mb-1.5 [&_h3]:mt-2.5 [&_h3]:text-sm [&_h3]:font-semibold
                [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5
                [&_strong]:font-semibold
                [&_table]:my-3 [&_table]:w-full [&_table]:min-w-[240px] [&_table]:border-collapse [&_table]:text-xs
                [&_thead]:bg-[#f8fafc] dark:[&_thead]:bg-[#141923]
                [&_th]:border [&_th]:border-[#d0d7e2] [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-[#334155] dark:[&_th]:border-[#334155] dark:[&_th]:text-[#e2e8f0]
                [&_td]:border [&_td]:border-[#e5e7eb] [&_td]:px-3 [&_td]:py-2 [&_td]:text-[#475569] dark:[&_td]:border-[#2f3441] dark:[&_td]:text-[#cbd5e1]
                [&_tr:nth-child(even)_td]:bg-[#fafbfc] dark:[&_tr:nth-child(even)_td]:bg-[#0f141f]/60"
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {normalizeAssistantMarkdown(assistantMessage)}
              </ReactMarkdown>
            </div>
          ) : isWorking ? (
            <div className="text-sm text-[#98a2b3]">{ASSISTANT_THINKING}</div>
          ) : null}

          {followups.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {followups.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => onFollowup?.(q)}
                  className="rounded-full border border-[#dbeafe] bg-[#eff6ff] px-3 py-1 text-xs text-[#1d4ed8] transition-colors hover:border-[#93c5fd] dark:border-[#1e3a5f] dark:bg-[#172554] dark:text-[#93c5fd]"
                >
                  {q}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
