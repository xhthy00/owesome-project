import { MenuFoldOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { Flex, Layout, Tooltip, message } from "antd";
import { useRouter } from "next/router";
import ChatContentContainer from "@/components/chat/ChatContentContainer";
import ChatExecutionPanel from "@/components/chat/ChatExecutionPanel";
import { useChat } from "@/hooks/useChat";
import { ChatContentContext } from "@/new-components/chat/context";
import ChatInputPanel from "@/new-components/chat/input/ChatInputPanel";
import QuestionDock from "@/new-components/chat/input/QuestionDock";

const initConversations = [{ id: "default", title: "Default Assistant" }];
const EXPERT_PANEL_OPEN_KEY = "chat-expert-panel-open";

export default function ChatPage() {
  const router = useRouter();
  const {
    messages,
    executionSteps,
    summary,
    summaryByRunId,
    reports,
    queryResults,
    chartByRunId,
    loading,
    activity,
    runMetrics,
    metricsByRunId,
    clarifyByRunId,
    pendingQuestion,
    questionSubmitting,
    questionError,
    replyQuestion,
    rejectQuestion,
    send,
    stop,
    loadConversation,
    clearConversation,
    patchReport,
    datasourceId,
    setDatasourceId,
    agentMode,
    setAgentMode
  } = useChat();
  const [conversations] = useState(initConversations);
  const [activeId] = useState("default");
  const [temperatureValue, setTemperatureValue] = useState(0.6);
  const [maxNewTokensValue, setMaxNewTokensValue] = useState(4000);
  const [resourceValue, setResourceValue] = useState("database:sales");
  const [modelValue, setModelValue] = useState("dbgpt-pro");
  const [selectedStepId, setSelectedStepId] = useState<string | undefined>(undefined);
  const [expertPanelOpen, setExpertPanelOpen] = useState(true);
  const prevConversationQuery = useRef<string | null>(null);
  const loadedConversationIdRef = useRef<number | null>(null);
  /** 探索广场 ?q= 只消费一次，避免 send/router 引用变化把同一问发成多条会话 */
  const consumedExploreQueryRef = useRef<string | null>(null);
  /** 新建任务清屏时先挡住当前 ?q=，避免 replace 完成前同一问再发一遍 */
  const ignoreExploreQueryRef = useRef(false);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(EXPERT_PANEL_OPEN_KEY) === "0") {
        setExpertPanelOpen(false);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const toggleExpertPanel = () => {
    setExpertPanelOpen((open) => {
      const next = !open;
      try {
        window.localStorage.setItem(EXPERT_PANEL_OPEN_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  useEffect(() => {
    const onNewTask = () => {
      ignoreExploreQueryRef.current = true;
      stop();
      clearConversation();
      prevConversationQuery.current = null;
      loadedConversationIdRef.current = null;
      const finish = () => {
        consumedExploreQueryRef.current = null;
        ignoreExploreQueryRef.current = false;
      };
      if (router.asPath !== "/chat") {
        void router.replace("/chat", undefined, { shallow: true }).then(finish);
        return;
      }
      finish();
    };
    window.addEventListener("chat:new-task", onNewTask);
    return () => window.removeEventListener("chat:new-task", onNewTask);
  }, [stop, clearConversation, router]);

  useEffect(() => {
    if (!executionSteps.length) {
      setSelectedStepId(undefined);
      return;
    }
    const latest = executionSteps[executionSteps.length - 1];
    setSelectedStepId((prev) => {
      if (!prev) return latest?.id;
      const prevStep = executionSteps.find((step) => step.id === prev);
      if (!prevStep) return latest?.id;
      // 新一轮提问后跟到最新 run，避免右侧专家条仍停在上一问并累计计时
      if (latest?.runId && prevStep.runId && prevStep.runId !== latest.runId) {
        return latest.id;
      }
      return prev;
    });
  }, [executionSteps]);

  useEffect(() => {
    if (!router.isReady) return;
    const q = router.query.q;
    const ds = router.query.ds;
    if (!q) return;
    if (ignoreExploreQueryRef.current) return;
    const text = (Array.isArray(q) ? q[0] : q).trim();
    const dsValue = Array.isArray(ds) ? ds[0] : ds;
    const dsId = dsValue ? Number(dsValue) : undefined;
    if (!text) return;
    if (consumedExploreQueryRef.current === text) return;
    consumedExploreQueryRef.current = text;
    const resolvedDsId = dsId && !Number.isNaN(dsId) ? dsId : datasourceId;
    if (dsId && !Number.isNaN(dsId)) {
      setDatasourceId(dsId);
    }
    void send(text, { datasourceId: resolvedDsId });
    void router.replace("/chat", undefined, { shallow: true });
  }, [router, send, datasourceId, setDatasourceId]);

  useEffect(() => {
    if (!router.isReady) return;
    const raw = router.query.conversation_id;
    const value = Array.isArray(raw) ? raw[0] : raw;
    const prev = prevConversationQuery.current;

    // 无 conversation_id：仅从「历史会话 → 新建任务」切过来时清空。
    // 首次进入 / 发送中途不要 clear，否则会与 SSE 竞态把 messages 清掉，左侧一直停在欢迎页。
    if (!value) {
      if (prev) {
        clearConversation();
      }
      prevConversationQuery.current = null;
      loadedConversationIdRef.current = null;
      return;
    }
    prevConversationQuery.current = value;
    const id = Number(value);
    if (!id || Number.isNaN(id)) return;
    // 流式中不加载；同一会话回答结束也不再 load（避免冲掉 SSE 里的 agent_speak）
    if (loading) return;
    if (loadedConversationIdRef.current === id) return;
    loadedConversationIdRef.current = id;
    void loadConversation(id).catch((err) => {
      if (loadedConversationIdRef.current === id) {
        loadedConversationIdRef.current = null;
      }
      const text = err instanceof Error ? err.message : "加载会话失败";
      void message.error(text);
    });
  }, [router.isReady, router.query.conversation_id, loadConversation, clearConversation, loading]);

  const appInfo = useMemo(
    () => ({
      param_need: [
        { type: "model" },
        { type: "temperature" },
        { type: "max_new_tokens" },
        { type: "resource", value: "database" }
      ]
    }),
    []
  );

  const pageContext = useMemo(
    () => ({
      replyLoading: loading,
      canAbort: loading,
      handleChat: async (text: string) => {
        await send(text, { datasourceId });
      },
      stopReply: stop,
      replayLast: () => {
        const latest = [...messages].reverse().find((item) => item.role === "user");
        if (latest?.content) {
          void send(latest.content, { datasourceId });
        }
      },
      clearHistory: () => {
        clearConversation();
      },
      appInfo,
      temperatureValue,
      maxNewTokensValue,
      resourceValue,
      modelValue,
      modelList: ["dbgpt-pro", "dbgpt-reasoner", "dbgpt-lite"],
      setTemperatureValue,
      setMaxNewTokensValue,
      setResourceValue,
      setModelValue,
      datasourceId,
      setDatasourceId,
      agentMode,
      setAgentMode
    }),
    [
      loading,
      send,
      stop,
      clearConversation,
      appInfo,
      temperatureValue,
      maxNewTokensValue,
      resourceValue,
      modelValue,
      messages,
      datasourceId,
      setDatasourceId,
      agentMode,
      setAgentMode
    ]
  );

  return (
    <ChatContentContext.Provider value={pageContext}>
      <Flex flex={1} className="h-full min-h-0 overflow-hidden">
        <Layout className="h-full min-h-0 overflow-hidden bg-gradient-light bg-cover bg-center dark:bg-gradient-dark">
          <Layout className="h-full min-h-0 overflow-hidden !bg-transparent">
            <div className="dbgpt-ui-font flex h-full min-h-0 flex-1 flex-col">
              <div
                className={`grid h-full min-h-0 flex-1 overflow-hidden ${
                  expertPanelOpen ? "grid-cols-[45%_55%]" : "grid-cols-[minmax(0,1fr)_48px]"
                }`}
              >
                <div className="flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-[#eceff5] bg-[#f5f6f8] dark:border-[#2f3441] dark:bg-[#0f1219]">
                  <div className="min-h-0 flex-1 overflow-hidden px-6 pt-4">
                    <ChatContentContainer
                      messages={messages}
                      steps={executionSteps}
                      loading={loading}
                      activity={activity}
                      reports={reports}
                      summaryByRunId={summaryByRunId}
                      runMetrics={runMetrics}
                      metricsByRunId={metricsByRunId}
                      selectedStepId={selectedStepId}
                      onSelectStep={setSelectedStepId}
                      clarifyByRunId={clarifyByRunId}
                    />
                  </div>
                  <div className="shrink-0 px-5">
                    {pendingQuestion ? (
                      <QuestionDock
                        question={pendingQuestion}
                        submitting={questionSubmitting}
                        error={questionError}
                        onConfirm={replyQuestion}
                        onCancel={rejectQuestion}
                      />
                    ) : null}
                    <ChatInputPanel />
                  </div>
                </div>
                {expertPanelOpen ? (
                  <div className="relative min-h-0 min-w-0 overflow-hidden bg-[#f8f9fc] dark:bg-[#171b24]">
                    <div className="pointer-events-none absolute left-0 top-0 h-full w-px bg-[#eceff5] dark:bg-[#2f3441]" />
                    <button
                      type="button"
                      onClick={toggleExpertPanel}
                      className="absolute left-0 top-1/2 z-10 flex h-8 w-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-[#e5e7eb] bg-white text-[#94a3b8] hover:border-[#93c5fd] hover:text-[#2563eb] dark:border-[#2f3441] dark:bg-[#141923]"
                      aria-label="收起专家团"
                      title="收起专家团"
                    >
                      ›
                    </button>
                    <ChatExecutionPanel
                      steps={executionSteps}
                      summary={summary}
                      summaryByRunId={summaryByRunId}
                      reports={reports}
                      queryResults={queryResults}
                      chartByRunId={chartByRunId}
                      selectedStepId={selectedStepId}
                      onSelectStep={setSelectedStepId}
                      runMetrics={runMetrics}
                      agentMode={agentMode}
                      onPatchReport={patchReport}
                      onCollapse={toggleExpertPanel}
                    />
                  </div>
                ) : (
                  <div className="flex h-full min-h-0 flex-col items-center border-l border-[#eceff5] bg-[#f8f9fc] pt-3 dark:border-[#2f3441] dark:bg-[#171b24]">
                    <Tooltip title="展开专家团" placement="left">
                      <button
                        type="button"
                        onClick={toggleExpertPanel}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                        aria-label="展开专家团"
                      >
                        <MenuFoldOutlined style={{ fontSize: 14 }} />
                      </button>
                    </Tooltip>
                    <button
                      type="button"
                      onClick={toggleExpertPanel}
                      className="mt-3 flex flex-1 flex-col items-center gap-2 px-1 text-[11px] text-[#64748b] hover:text-[#2563eb]"
                      aria-label="展开专家团"
                    >
                      <img
                        src="/expert-team-avatar.png?v=2"
                        alt=""
                        width={28}
                        height={28}
                        className="h-7 w-7 rounded-lg object-cover shadow-sm ring-1 ring-[#dbeafe]"
                      />
                      <span className="[writing-mode:vertical-rl] tracking-widest">专家团</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </Layout>
        </Layout>
      </Flex>
    </ChatContentContext.Provider>
  );
}
