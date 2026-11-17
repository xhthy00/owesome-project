import { App } from 'antd';
import { history, useLocation } from '@umijs/max';
import { useEffect, useRef } from 'react';
import CoworkChat from '@/components/chat/CoworkChat';
import { useChat } from '@/hooks/useChat';

export default function Chat() {
  const { message } = App.useApp();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  const question = params.get('q')?.trim();
  const source = params.get('ds');
  const conversation = params.get('conversation_id');
  const chat = useChat();
  const { send, stop, loadConversation, clearConversation, loading, datasourceId, setDatasourceId } = chat;
  const consumed = useRef<string | undefined>(undefined);
  const loaded = useRef<number | undefined>(undefined);
  const previous = useRef<string | null>(null);

  useEffect(() => {
    const newTask = () => {
      consumed.current = question;
      loaded.current = undefined;
      previous.current = null;
      stop();
      clearConversation();
    };
    window.addEventListener('chat:new-task', newTask);
    return () => window.removeEventListener('chat:new-task', newTask);
  }, [question, stop, clearConversation]);

  useEffect(() => () => stop(), [stop]);

  useEffect(() => {
    if (!question) {
      consumed.current = undefined;
      return;
    }
    if (consumed.current === question) return;
    consumed.current = question;
    const selected = Number(source);
    const validSource = Number.isSafeInteger(selected) && selected > 0;
    if (validSource) setDatasourceId(selected);
    void send(question, { datasourceId: validSource ? selected : datasourceId });
    history.replace('/chat');
  }, [question, source, send, datasourceId, setDatasourceId]);

  useEffect(() => {
    if (!conversation) {
      if (previous.current) clearConversation();
      previous.current = null;
      loaded.current = undefined;
      return;
    }
    previous.current = conversation;
    const id = Number(conversation);
    if (!Number.isSafeInteger(id) || id <= 0 || loading || loaded.current === id) return;
    loaded.current = id;
    const request = new AbortController();
    let settled = false;
    void loadConversation(id, request.signal).catch(error => {
      if (request.signal.aborted || loaded.current !== id) return;
      loaded.current = undefined;
      void message.error(error instanceof Error ? error.message : '加载会话失败');
    }).finally(() => { settled = true; });
    return () => {
      if (!settled && loaded.current === id) loaded.current = undefined;
      request.abort();
    };
  }, [conversation, loading, loadConversation, clearConversation, message]);

  return <div className="pro-chat-route">
    <div id="chat-header-slot" />
    <CoworkChat chat={chat} />
  </div>;
}
