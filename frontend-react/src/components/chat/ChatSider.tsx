import { MessageOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, List, Typography } from "antd";

type Props = {
  conversations: Array<{ id: string; title: string }>;
  activeId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
};

export default function ChatSider({ conversations, activeId, onSelect, onNew }: Props) {
  return (
    <aside className="dbgpt-ui-font m-3 flex h-[calc(100vh-24px)] w-[280px] flex-col rounded-2xl border border-[#d5e5f6] bg-[#ffffff80] p-3 shadow-sm dark:border-[#ffffff66] dark:bg-[#ffffff29]">
      <Button type="primary" block icon={<PlusOutlined />} onClick={onNew} className="!h-10 !text-sm !font-medium">
        New Chat
      </Button>
      <Typography.Text className="mt-3 px-1 !text-base !font-semibold !leading-6 !text-[#1c2533] dark:!text-[rgba(255,255,255,0.85)]">
        Recent Conversations
      </Typography.Text>
      <List
        className="mt-2 flex-1 overflow-auto pr-1"
        dataSource={conversations}
        renderItem={(item) => (
          <List.Item className="!border-none !py-1">
            <Button
              type={item.id === activeId ? "primary" : "text"}
              icon={<MessageOutlined />}
              block
              onClick={() => onSelect(item.id)}
              className="!h-9 !justify-start !rounded-lg !text-sm"
            >
              {item.title}
            </Button>
          </List.Item>
        )}
      />
    </aside>
  );
}
