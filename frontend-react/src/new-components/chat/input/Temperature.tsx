import { ControlOutlined } from "@ant-design/icons";
import { InputNumber, Popover, Slider, Tooltip } from "antd";
import { useContext } from "react";
import { ChatContentContext } from "@/new-components/chat/context";

export default function Temperature() {
  const { appInfo, temperatureValue, setTemperatureValue } = useContext(ChatContentContext);
  const paramKeys = appInfo.param_need?.map((item) => item.type) ?? [];

  if (!paramKeys.includes("temperature")) {
    return (
      <Tooltip title="Temperature disabled">
        <div className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-[rgb(221,221,221,0.6)]">
          <ControlOutlined className="cursor-not-allowed text-xl opacity-30" />
        </div>
      </Tooltip>
    );
  }

  return (
    <div className="flex items-center">
      {/** Keep behavior consistent with AwesomeDB toolbar popover. */}
      <Popover
        trigger={["click"]}
        placement="topLeft"
        content={
          <div className="flex items-center gap-2">
            <Slider
              className="w-20"
              min={0}
              max={1}
              step={0.1}
              onChange={(value) => setTemperatureValue(Array.isArray(value) ? value[0] : value)}
              value={temperatureValue}
            />
            <InputNumber size="small" className="w-14" min={0} max={1} step={0.1} onChange={(v) => v !== null && setTemperatureValue(Number(v))} value={temperatureValue} />
          </div>
        }
      >
        <div className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-[rgb(221,221,221,0.6)]">
          <ControlOutlined />
        </div>
      </Popover>
      <span className="ml-2 text-sm leading-6">{temperatureValue}</span>
    </div>
  );
}
