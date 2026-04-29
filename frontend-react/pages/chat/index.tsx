import { useEffect, useMemo, useState } from "react";
import { Flex, Layout, Spin } from "antd";
import { useRouter } from "next/router";
import ChatContentContainer from "@/components/chat/ChatContentContainer";
import ChatExecutionPanel from "@/components/chat/ChatExecutionPanel";
import ChatHeader from "@/components/chat/ChatHeader";
import { useChat } from "@/hooks/useChat";
import { ChatContentContext } from "@/new-components/chat/context";
import ChatInputPanel from "@/new-components/chat/input/ChatInputPanel";

const initConversations = [{ id: "default", title: "Default Assistant" }];

export default function ChatPage() {
  const router = useRouter();
  const { messages, executionSteps, summary, loading, send, stop, loadConversation, clearConversation } = useChat();
  const [conversations] = useState(initConversations);
  const [activeId] = useState("default");
  const [temperatureValue, setTemperatureValue] = useState(0.6);
  const [maxNewTokensValue, setMaxNewTokensValue] = useState(4000);
  const [resourceValue, setResourceValue] = useState("database:sales");
  const [modelValue, setModelValue] = useState("dbgpt-pro");
  const [localMessages, setLocalMessages] = useState(messages);
  const [selectedStepId, setSelectedStepId] = useState<string | undefined>(undefined);

  useEffect(() => {
    setLocalMessages(messages);
  }, [messages]);

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
    const datasourceId = dsValue ? Number(dsValue) : undefined;
    if (!text?.trim()) return;
    void send(text.trim(), {
      datasourceId: datasourceId && !Number.isNaN(datasourceId) ? datasourceId : undefined
    });
    void router.replace("/chat", undefined, { shallow: true });
  }, [router, send]);

  useEffect(() => {
    if (!router.isReady) return;
    const cid = router.query.conversation_id;
    if (!cid) {
      clearConversation();
      return;
    }
    const value = Array.isArray(cid) ? cid[0] : cid;
    const id = Number(value);
    if (!id || Number.isNaN(id)) return;
    void loadConversation(id);
  }, [router.isReady, router.query.conversation_id, loadConversation, clearConversation]);

  const title = useMemo(
    () => conversations.find((item) => item.id === activeId)?.title ?? "Conversation",
    [activeId, conversations]
  );

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
        await send(text);
        setLocalMessages((prev) => [...prev]);
      },
      stopReply: stop,
      replayLast: () => {
        const latest = [...messages].reverse().find((item) => item.role === "user");
        if (latest?.content) {
          void send(latest.content);
        }
      },
      clearHistory: () => {
        setLocalMessages([]);
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
      setModelValue
    }),
    [loading, send, stop, appInfo, temperatureValue, maxNewTokensValue, resourceValue, modelValue, messages]
  );

  return (
    <ChatContentContext.Provider value={pageContext}>
      <Flex flex={1}>
        <Layout className="bg-gradient-light bg-cover bg-center dark:bg-gradient-dark">
          <Layout className="!bg-transparent">
            <Spin spinning={false} className="m-auto h-full w-full">
              <div className="dbgpt-ui-font flex h-screen flex-1 flex-col">
                <ChatHeader title={title} loading={loading} />
                <div className="grid min-h-0 flex-1 grid-cols-[45%_55%] overflow-hidden">
                  <div className="flex min-w-0 flex-col overflow-hidden border-r border-[#eceff5] bg-[#f5f6f8] dark:border-[#2f3441] dark:bg-[#0f1219]">
                    <div className="min-h-0 flex-1 px-6 pt-4">
                      <ChatContentContainer
                        messages={localMessages.length ? localMessages : messages}
                        steps={executionSteps}
                        selectedStepId={selectedStepId}
                        onSelectStep={setSelectedStepId}
                      />
                    </div>
                    <div className="px-5">
                      <ChatInputPanel />
                    </div>
                  </div>
                  <div className="relative min-w-0 overflow-hidden bg-[#f8f9fc] dark:bg-[#171b24]">
                    <div className="pointer-events-none absolute left-0 top-0 h-full w-px bg-[#eceff5] dark:bg-[#2f3441]" />
                    <div className="absolute left-0 top-1/2 z-10 flex h-8 w-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-[#e5e7eb] bg-white text-[#cbd5e1] dark:border-[#2f3441] dark:bg-[#141923]">
                      ›
                    </div>
                    <ChatExecutionPanel
                      steps={executionSteps}
                      summary={summary}
                      selectedStepId={selectedStepId}
                      onSelectStep={setSelectedStepId}
                    />
                  </div>
                </div>
              </div>
            </Spin>
          </Layout>
        </Layout>
      </Flex>
    </ChatContentContext.Provider>
  );
}
