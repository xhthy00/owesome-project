import { BulbOutlined, VerticalAlignBottomOutlined, VerticalAlignTopOutlined } from "@ant-design/icons";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { ExecutionStep, Message, ReportPayload } from "@/hooks/useChat";
import type { RunMetrics } from "@/utils/runMetrics";
import type { ClarifyPayload } from "@/api/adapter/chatAdapter";
import {
  ASSISTANT_NAME,
  ASSISTANT_SUGGESTIONS,
  ASSISTANT_WELCOME_DESC,
  ASSISTANT_WELCOME_TITLE,
  getFollowups
} from "@/new-components/chat/assistant";
import { ChatContentContext } from "@/new-components/chat/context";
import OpenCodeSessionTurn from "@/new-components/chat/content/OpenCodeSessionTurn";
import {
  buildStoryPhases,
  extractThinkFromSteps,
  extractThinkFromText,
  mergeThinkText,
  pickCustomerAnswer,
  summarizeRun
} from "@/utils/toolLabels";

type Props = {
  messages: Message[];
  steps?: ExecutionStep[];
  selectedStepId?: string;
  onSelectStep?: (stepId: string) => void;
  loading?: boolean;
  activity?: string;
  reports?: ReportPayload[];
  summaryByRunId?: Record<string, string>;
  runMetrics?: RunMetrics;
  metricsByRunId?: Record<string, RunMetrics>;
  clarifyByRunId?: Record<string, ClarifyPayload>;
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

function WelcomePanel({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mx-auto flex w-[92%] max-w-xl flex-col gap-4 py-10">
      <div>
        <div className="text-lg font-semibold text-[#0f172a] dark:text-[#e2e8f0]">{ASSISTANT_WELCOME_TITLE}</div>
        <p className="mt-1 text-sm leading-6 text-[#64748b] dark:text-[#94a3b8]">{ASSISTANT_WELCOME_DESC}</p>
        <p className="mt-0.5 text-xs text-[#94a3b8]">{ASSISTANT_NAME} 随时待命</p>
      </div>
      <div className="flex flex-col gap-2">
        {ASSISTANT_SUGGESTIONS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => onPick(item)}
            className="flex items-start gap-2 rounded-xl border border-[#e7eaf0] bg-white px-4 py-3 text-left text-sm leading-6 text-[#334155] shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors hover:border-[#91caff] hover:bg-[#f0f7ff] dark:border-[#2f3441] dark:bg-[#111723] dark:text-[#e2e8f0] dark:hover:border-[#3b82f6]"
          >
            <BulbOutlined className="mt-1 shrink-0 text-[#0c75fc]" />
            <span className="min-w-0">{item}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function clarifyChips(payload?: ClarifyPayload): string[] {
  if (!payload?.options) return [];
  const raw = payload.options;
  if (Array.isArray(raw)) {
    return raw.map((x) => String(x || "").trim()).filter(Boolean);
  }
  const out: string[] = [];
  for (const vals of Object.values(raw)) {
    if (!Array.isArray(vals)) continue;
    for (const v of vals) {
      const s = String(v || "").trim();
      if (s && !out.includes(s)) out.push(s);
    }
  }
  return out;
}

export default function ChatContentContainer({
  messages,
  steps = [],
  selectedStepId,
  onSelectStep,
  loading = false,
  activity = "",
  reports = [],
  summaryByRunId = {},
  runMetrics,
  metricsByRunId = {},
  clarifyByRunId = {}
}: Props) {
  const { handleChat } = useContext(ChatContentContext);
  const ref = useRef<HTMLDivElement>(null);
  const [isAtTop, setIsAtTop] = useState(true);
  const [isAtBottom, setIsAtBottom] = useState(true);
  // 用 ref 记录是否贴底，避免流式更新时强制拽回底部打断上滑阅读
  const stickToBottomRef = useRef(true);

  // 新一轮开始时重新贴底跟随
  useEffect(() => {
    if (!loading) return;
    stickToBottomRef.current = true;
  }, [loading]);

  useEffect(() => {
    const ele = ref.current;
    if (!ele || !stickToBottomRef.current) return;
    ele.scrollTop = ele.scrollHeight;
  }, [messages, steps, activity]);

  useEffect(() => {
    const ele = ref.current;
    if (!ele) return;
    const onScroll = () => {
      const buffer = 48;
      const atTop = ele.scrollTop <= buffer;
      const atBottom = ele.scrollTop + ele.clientHeight >= ele.scrollHeight - buffer;
      setIsAtTop(atTop);
      setIsAtBottom(atBottom);
      stickToBottomRef.current = atBottom;
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

  // 有执行步骤也算「已开聊」：避免 messages 被竞态清空时仍卡在欢迎页
  const isEmpty = !messages.length && !steps.length;
  const userMessages = messages.filter((m) => m.role === "user" && toText(m.content).trim());
  const stepWithoutRunId = steps.filter((s) => !s.runId);

  const runEntries = useMemo(() => {
    const fromUser = userMessages.map((msg, idx) => {
      const isLatest = idx === userMessages.length - 1;
      const runSteps = steps.filter((s) => s.runId && s.runId === msg.runId);
      const mergedSteps = isLatest ? [...runSteps, ...stepWithoutRunId] : runSteps;
      const assistantMsg = messages.find(
        (m) => m.role === "assistant" && m.runId && m.runId === msg.runId && toText(m.content).trim()
      );
      const runId = msg.runId || "";
      const summaryText = runId ? summaryByRunId[runId] : undefined;
      const assistantText = toText(assistantMsg?.content);
      const answer = pickCustomerAnswer(summaryText, assistantText);
      const runReports = reports.filter((r) => r.runId === msg.runId);
      const latestType = runReports[runReports.length - 1]?.reportType;
      const liveOpts = isLatest && loading ? { loading: true, activity } : undefined;
      return {
        message: msg,
        mergedSteps,
        answer,
        reportType: latestType,
        summary: summarizeRun(mergedSteps, liveOpts),
        phases: buildStoryPhases(mergedSteps, liveOpts),
        thinkText: mergeThinkText(
          extractThinkFromText(summaryText || assistantText),
          extractThinkFromSteps(mergedSteps)
        )
      };
    });
    if (fromUser.length > 0) return fromUser;
    if (!steps.length) return [];
    const fallbackMessage: Message = {
      id: "fallback-run-message",
      role: "user",
      content: toText(messages.find((m) => m.role === "user")?.content).trim() || "当前任务"
    };
    const liveOpts = loading ? { loading: true, activity } : undefined;
    return [
      {
        message: fallbackMessage,
        mergedSteps: steps,
        answer: "",
        reportType: undefined as string | undefined,
        summary: summarizeRun(steps, liveOpts),
        phases: buildStoryPhases(steps, liveOpts),
        thinkText: extractThinkFromSteps(steps)
      }
    ];
  }, [userMessages, steps, stepWithoutRunId, messages, summaryByRunId, reports, loading, activity, clarifyByRunId]);

  return (
    <div className="relative h-full min-h-0">
      <div ref={ref} className="chat-scroll-hidden h-full min-h-0 overflow-y-auto overflow-x-hidden pr-2">
        {isEmpty ? (
          <WelcomePanel onPick={(text) => void handleChat(text)} />
        ) : (
          <div className="mx-auto flex w-[95%] flex-col gap-2 py-3">
            {runEntries.map((entry, idx) => {
              const isLatest = idx === runEntries.length - 1;
              const runId = entry.message.runId || "";
              const stored = runId ? metricsByRunId[runId] : undefined;
              // 进行中用实时指标；历史轮 / 已完成轮用按 run 归档的指标
              const turnMetrics =
                isLatest && loading ? runMetrics : stored ?? (isLatest ? runMetrics : undefined);
              return (
                <OpenCodeSessionTurn
                  key={entry.message.id}
                  userMessage={toText(entry.message.content)}
                  assistantMessage={entry.answer}
                  activity={isLatest && loading ? activity : undefined}
                  isWorking={isLatest && loading}
                  thinkText={entry.thinkText || undefined}
                  storySummary={entry.summary.label}
                  storyPhases={entry.phases}
                  followups={
                    !loading && isLatest
                      ? clarifyChips(clarifyByRunId[runId]).length
                        ? clarifyChips(clarifyByRunId[runId])
                        : entry.answer
                          ? getFollowups(entry.reportType)
                          : []
                      : []
                  }
                  selectedStepId={selectedStepId}
                  onSelectStep={onSelectStep}
                  onFollowup={(text) => void handleChat(text)}
                  runMetrics={turnMetrics}
                />
              );
            })}
          </div>
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
      <style jsx>{`
        .chat-scroll-hidden {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
        .chat-scroll-hidden::-webkit-scrollbar {
          width: 0;
          height: 0;
        }
      `}</style>
    </div>
  );
}
