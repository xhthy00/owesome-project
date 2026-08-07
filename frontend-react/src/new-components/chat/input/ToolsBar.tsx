import { PlusOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { Button, Segmented, Tooltip } from "antd";
import { useContext } from "react";
import DatasourcePicker from "@/components/chat/DatasourcePicker";
import type { AgentMode } from "@/hooks/useChat";
import { ChatContentContext } from "@/new-components/chat/context";

const AGENT_MODE_OPTIONS: { label: string; value: AgentMode }[] = [
  { label: "团队协作", value: "team" },
  { label: "单体分析", value: "agent" }
];

export default function ToolsBar() {
  const { datasourceId, setDatasourceId, agentMode, setAgentMode, replyLoading } =
    useContext(ChatContentContext);

  return (
    <div className="dbgpt-ui-font mt-1.5 flex items-center justify-between border-t border-[#f0f2f5] pt-1.5 dark:border-[rgba(255,255,255,0.08)]">
      <div className="flex min-w-0 flex-1 items-center gap-0.5">
        <Tooltip title="添加资源">
          <Button
            type="text"
            size="small"
            className="flex h-7 w-7 items-center justify-center rounded-full border-0 text-[#8c8c8c] hover:!bg-[#f5f6f8] hover:!text-[#5c5c5c] dark:text-[#8a93a6] dark:hover:!bg-[#2a3040]"
            icon={<PlusOutlined className="text-[11px]" />}
          />
        </Tooltip>
        <Tooltip title="快捷能力">
          <Button
            type="text"
            size="small"
            className="flex h-7 w-7 items-center justify-center rounded-full border-0 text-[#8c8c8c] hover:!bg-[#f5f6f8] hover:!text-[#5c5c5c] dark:text-[#8a93a6] dark:hover:!bg-[#2a3040]"
            icon={<ThunderboltOutlined className="text-[11px]" />}
          />
        </Tooltip>
        <span className="mx-1 h-4 w-px bg-[#eceff5] dark:bg-[#343b4a]" />
        <DatasourcePicker value={datasourceId} onChange={setDatasourceId} compact />
        <Segmented
          size="small"
          className="ml-2 shrink-0 !rounded-full [&_.ant-segmented-item-selected]:!rounded-full"
          value={agentMode === "legacy" ? "team" : agentMode}
          disabled={replyLoading}
          options={AGENT_MODE_OPTIONS}
          onChange={(v) => setAgentMode(v as AgentMode)}
        />
      </div>
      <div className="text-[11px] text-[#c4c8d0] dark:text-[#667085]"> </div>
    </div>
  );
}
