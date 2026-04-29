import { ControlOutlined } from "@ant-design/icons";
import { InputNumber, Popover, Slider, Tooltip } from "antd";
import { useContext } from "react";
import { ChatContentContext } from "@/new-components/chat/context";

export default function MaxNewTokens() {
  const { appInfo, maxNewTokensValue, setMaxNewTokensValue } = useContext(ChatContentContext);
  const paramKeys = appInfo.param_need?.map((item) => item.type) ?? [];

  if (!paramKeys.includes("max_new_tokens")) {
    return (
      <Tooltip title="max_new_tokens disabled">
        <div className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-[rgb(221,221,221,0.6)]">
          <ControlOutlined className="cursor-not-allowed text-xl opacity-30" />
        </div>
      </Tooltip>
    );
  }

  return (
    <div className="flex items-center">
      <Popover
        trigger={["click"]}
        placement="topLeft"
        content={
          <div className="flex items-center gap-2">
            <Slider
              className="w-32"
              min={1}
              max={8192}
              step={1}
              onChange={(value) => setMaxNewTokensValue(Array.isArray(value) ? value[0] : value)}
              value={maxNewTokensValue}
            />
            <InputNumber
              size="small"
              className="w-20"
              min={1}
              max={8192}
              step={1}
              onChange={(v) => v !== null && setMaxNewTokensValue(Number(v))}
              value={maxNewTokensValue}
            />
          </div>
        }
      >
        <div className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-[rgb(221,221,221,0.6)]">
          <ControlOutlined />
        </div>
      </Popover>
      <span className="ml-2 text-sm leading-6">{maxNewTokensValue}</span>
    </div>
  );
}
