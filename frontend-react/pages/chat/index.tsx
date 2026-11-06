import { useEffect, useRef } from "react";
import { message } from "antd";
import { useRouter } from "next/router";
import CoworkChat from "@/components/chat/CoworkChat";
import { useChat } from "@/hooks/useChat";

export default function ChatPage() {
  const router = useRouter();
  const chat = useChat();
  const { send, stop, loadConversation, clearConversation, loading, datasourceId, setDatasourceId } = chat;
  const prevConversationQuery = useRef<string | null>(null);
  const loadedConversationIdRef = useRef<number | null>(null);
  /** 探索广场 ?q= 只消费一次，避免 send/router 引用变化把同一问发成多条会话 */
  const consumedExploreQueryRef = useRef<string | null>(null);
  /** 新建任务清屏时先挡住当前 ?q=，避免 replace 完成前同一问再发一遍 */
  const ignoreExploreQueryRef = useRef(false);

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
      if (router.asPath !== router.pathname) {
        void router.replace(router.pathname, undefined, { shallow: true }).then(finish);
        return;
      }
      finish();
    };
    window.addEventListener("chat:new-task", onNewTask);
    return () => window.removeEventListener("chat:new-task", onNewTask);
  }, [stop, clearConversation, router]);

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
    void router.replace(router.pathname, undefined, { shallow: true });
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

  return <CoworkChat chat={chat} />;
}
