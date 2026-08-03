import { useEffect, useMemo, useRef, useState } from "react";
import { Flex, Layout } from "antd";
import { useRouter } from "next/router";
import ChatContentContainer from "@/components/chat/ChatContentContainer";
import ChatExecutionPanel from "@/components/chat/ChatExecutionPanel";
import { useChat } from "@/hooks/useChat";
import { ChatContentContext } from "@/new-components/chat/context";
import ChatInputPanel from "@/new-components/chat/input/ChatInputPanel";

const initConversations = [{ id: "default", title: "Default Assistant" }];

export default function ChatPage() {
  const router = useRouter();
  const {
    messages,
    executionSteps,
    summary,
    summaryByRunId,
    reports,
    queryResults,
    loading,
    activity,
    runMetrics,
    metricsByRunId,
    send,
    stop,
    loadConversation,
    clearConversation,
    patchReport,
    datasourceId,
    setDatasourceId
  } = useChat();
  const [conversations] = useState(initConversations);
  const [activeId] = useState("default");
  const [temperatureValue, setTemperatureValue] = useState(0.6);
  const [maxNewTokensValue, setMaxNewTokensValue] = useState(4000);
  const [resourceValue, setResourceValue] = useState("database:sales");
  const [modelValue, setModelValue] = useState("dbgpt-pro");
  const [selectedStepId, setSelectedStepId] = useState<string | undefined>(undefined);
  const prevConversationQuery = useRef<string | null>(null);
  const loadedConversationIdRef = useRef<number | null>(null);

  useEffect(() => {
    if (!executionSteps.length) {
      setSelectedStepId(undefined);
      return;
    }
    setSelectedStepId((prev) => (prev && executionSteps.some((step) => step.id === prev) ? prev : executionSteps[executionSteps.length - 1]?.id));
  }, [executionSteps]);

  useEffect(() => {
    const q = router.query.q;
    const ds = router.query.ds;
    if (!router.isReady || !q) return;
    const text = Array.isArray(q) ? q[0] : q;
    const dsValue = Array.isArray(ds) ? ds[0] : ds;
    const dsId = dsValue ? Number(dsValue) : undefined;
    if (!text?.trim()) return;
    const resolvedDsId = dsId && !Number.isNaN(dsId) ? dsId : datasourceId;
    if (dsId && !Number.isNaN(dsId)) {
      setDatasourceId(dsId);
    }
    void send(text.trim(), { datasourceId: resolvedDsId });
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
    void loadConversation(id);
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
      setDatasourceId
    }),
    [loading, send, stop, clearConversation, appInfo, temperatureValue, maxNewTokensValue, resourceValue, modelValue, messages, datasourceId, setDatasourceId]
  );

  return (
    <ChatContentContext.Provider value={pageContext}>
      <Flex flex={1} className="h-full min-h-0 overflow-hidden">
        <Layout className="h-full min-h-0 overflow-hidden bg-gradient-light bg-cover bg-center dark:bg-gradient-dark">
          <Layout className="h-full min-h-0 overflow-hidden !bg-transparent">
            <div className="dbgpt-ui-font flex h-full min-h-0 flex-1 flex-col">
              <div className="grid h-full min-h-0 flex-1 grid-cols-[45%_55%] overflow-hidden">
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
                    />
                  </div>
                  <div className="shrink-0 px-5">
                    <ChatInputPanel />
                  </div>
                </div>
                <div className="relative min-h-0 min-w-0 overflow-hidden bg-[#f8f9fc] dark:bg-[#171b24]">
                  <div className="pointer-events-none absolute left-0 top-0 h-full w-px bg-[#eceff5] dark:bg-[#2f3441]" />
                  <div className="absolute left-0 top-1/2 z-10 flex h-8 w-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-[#e5e7eb] bg-white text-[#cbd5e1] dark:border-[#2f3441] dark:bg-[#141923]">
                    ›
                  </div>
                  <ChatExecutionPanel
                    steps={executionSteps}
                    summary={summary}
                    summaryByRunId={summaryByRunId}
                    reports={reports}
                    queryResults={queryResults}
                    selectedStepId={selectedStepId}
                    onSelectStep={setSelectedStepId}
                    onPatchReport={patchReport}
                  />
                </div>
              </div>
            </div>
          </Layout>
        </Layout>
      </Flex>
    </ChatContentContext.Provider>
  );
}
