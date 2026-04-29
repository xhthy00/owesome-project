import { BellOutlined, ThunderboltOutlined, UserOutlined } from "@ant-design/icons";
import { Typography } from "antd";

type Props = {
  title: string;
  loading: boolean;
};

export default function ChatHeader({ title, loading }: Props) {
  return (
    <div className="dbgpt-ui-font flex h-14 items-center justify-between border-b border-[#eceff5] px-5 dark:border-[#2f3441]">
      <div className="flex items-center">
        <Typography.Title level={5} className="!mb-0 !text-[20px] !font-semibold !leading-none">
          启明AI数据助理
        </Typography.Title>
      </div>
      <div className="flex items-center gap-4 text-[#6b7280]">
        <button className="text-[12px] hover:text-[#111827]">Clear Chat</button>
        <BellOutlined className="text-[13px]" />
        <div className="flex items-center gap-1 rounded-full bg-[#f5f6fa] px-2 py-0.5 text-xs dark:bg-[#1d2230]">
          <ThunderboltOutlined className="text-[11px] text-[#f59e0b]" />
          <span>300</span>
        </div>
        <div className="flex h-5 w-5 items-center justify-center rounded-full bg-[#3b82f6] text-[9px] text-white">
          <UserOutlined />
        </div>
      </div>
    </div>
  );
}
