import { SettingOutlined } from "@ant-design/icons";
import { Select, Tooltip } from "antd";
import { useContext } from "react";
import { ChatContentContext } from "@/new-components/chat/context";

export default function ModelSwitcher() {
  const { modelValue, setModelValue, modelList, appInfo } = useContext(ChatContentContext);
  const paramKeys = appInfo.param_need?.map((item) => item.type) ?? [];

  if (!paramKeys.includes("model")) {
    return (
      <Tooltip title="Model disabled">
        <div className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-[rgb(221,221,221,0.6)]">
          <SettingOutlined className="cursor-not-allowed text-xl opacity-30" />
        </div>
      </Tooltip>
    );
  }

  return (
    <Select
      value={modelValue}
      className="h-8 min-w-40 !text-xs [&_.ant-select-selector]:!h-8 [&_.ant-select-selector]:!rounded-md [&_.ant-select-selector]:!border-[#e5e7eb] [&_.ant-select-selector]:!bg-white dark:[&_.ant-select-selector]:!border-[#3a404d] dark:[&_.ant-select-selector]:!bg-[#1f2430] [&_.ant-select-selection-item]:!leading-8"
      onChange={setModelValue}
      popupMatchSelectWidth={260}
      options={modelList.map((item) => ({ value: item, label: item }))}
    />
  );
}
