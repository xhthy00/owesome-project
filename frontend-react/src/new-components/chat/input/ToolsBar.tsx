import { AudioOutlined, PlusOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";

export default function ToolsBar() {
  return (
    <div className="dbgpt-ui-font mt-1.5 flex items-center justify-between border-t border-[#f0f2f5] pt-1.5 dark:border-[rgba(255,255,255,0.08)]">
      <div className="flex items-center gap-0.5">
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
        <Tooltip title="语音输入">
          <Button
            type="text"
            size="small"
            className="flex h-7 w-7 items-center justify-center rounded-full border-0 text-[#8c8c8c] hover:!bg-[#f5f6f8] hover:!text-[#5c5c5c] dark:text-[#8a93a6] dark:hover:!bg-[#2a3040]"
            icon={<AudioOutlined className="text-[11px]" />}
          />
        </Tooltip>
      </div>
      <div className="text-[11px] text-[#c4c8d0] dark:text-[#667085]"> </div>
    </div>
  );
}
