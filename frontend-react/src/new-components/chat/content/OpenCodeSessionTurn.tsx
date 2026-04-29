import { CopyOutlined, RobotOutlined, UserOutlined } from "@ant-design/icons";
import { Button, Tooltip, Typography } from "antd";

export type MessagePart = {
  type: "text" | "status";
  content: string;
};

type Props = {
  userMessage: string;
  assistantMessage?: string;
  parts?: MessagePart[];
  isWorking?: boolean;
};

export default function OpenCodeSessionTurn({ userMessage, assistantMessage, parts = [], isWorking }: Props) {
  const copy = async (text: string) => {
    if (!text) return;
    await navigator.clipboard.writeText(text);
  };

  return (
    <div className="oc-session-turn dbgpt-ui-font flex flex-col gap-4 py-3" data-component="session-turn">
      <div className="flex gap-3">
        <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-full bg-[#1677ff] text-white">
          <UserOutlined />
        </div>
        <div data-slot="user-message" className="oc-card min-w-0 flex-1 p-3">
          <div className="flex items-start justify-between gap-2">
            <Typography.Text className="whitespace-pre-wrap !text-sm !leading-6">{userMessage}</Typography.Text>
            <Tooltip title="Copy">
              <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => void copy(userMessage)} />
            </Tooltip>
          </div>
        </div>
      </div>

      <div className="flex gap-3">
        <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-full bg-[var(--oc-bg-soft)]">
          <RobotOutlined />
        </div>
        <div data-slot="assistant-section" className="oc-card min-w-0 flex-1 p-3">
          {parts.length > 0 && (
            <div className="mb-2 space-y-1">
              {parts.map((part, idx) => (
                <div key={`${part.type}-${idx}`} className="rounded-md bg-[var(--oc-bg-soft)] px-2 py-1 text-xs leading-5 oc-muted">
                  {part.content}
                </div>
              ))}
            </div>
          )}
          <Typography.Text className="whitespace-pre-wrap !text-sm !leading-6">
            {assistantMessage || (isWorking ? "Thinking..." : "")}
          </Typography.Text>
        </div>
      </div>
    </div>
  );
}
