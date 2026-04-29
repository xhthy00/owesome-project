import { Typography } from "antd";
import ChatHeader from "@/components/chat/ChatHeader";
import ChatInputPanel from "@/components/chat/ChatInputPanel";
import ChatContentContainer from "@/components/chat/ChatContentContainer";
import PromptBot from "@/components/chat/PromptBot";
import { useChat } from "@/hooks/useChat";

export default function MobileChatPage() {
  const { messages, loading, send, stop } = useChat();

  return (
    <div className="oc-page-bg mx-auto min-h-screen max-w-md p-3">
      <Typography.Title level={4}>Mobile Chat</Typography.Title>
      <ChatHeader title="Mobile Assistant" loading={loading} />
      <div className="oc-panel mb-3 mt-3 p-2">
        <ChatContentContainer messages={messages} />
      </div>
      <ChatInputPanel loading={loading} onSend={send} onStop={stop} />
      <PromptBot onPick={send} />
    </div>
  );
}
