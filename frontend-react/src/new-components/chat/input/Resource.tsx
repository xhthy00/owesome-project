import { ExperimentOutlined } from "@ant-design/icons";
import { Select, Tooltip } from "antd";
import { useContext } from "react";
import { ChatContentContext } from "@/new-components/chat/context";

const resourceOptions = [
  { value: "database:sales", label: "database:sales" },
  { value: "knowledge:product_docs", label: "knowledge:product_docs" },
  { value: "plugin:analysis", label: "plugin:analysis" }
];

export default function Resource() {
  const { appInfo, resourceValue, setResourceValue } = useContext(ChatContentContext);
  const paramKeys = appInfo.param_need?.map((item) => item.type) ?? [];

  if (!paramKeys.includes("resource")) {
    return (
      <Tooltip title="Resource disabled">
        <div className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-[rgb(221,221,221,0.6)]">
          <ExperimentOutlined className="cursor-not-allowed text-lg opacity-30" />
        </div>
      </Tooltip>
    );
  }

  return (
    <Select
      value={resourceValue}
      onChange={setResourceValue}
      options={resourceOptions}
      size="small"
      dropdownMatchSelectWidth={320}
      suffixIcon={<ExperimentOutlined className="text-xs" />}
      className="h-8 w-52 !text-xs [&_.ant-select-selector]:!h-8 [&_.ant-select-selector]:!rounded-md [&_.ant-select-selector]:!border-[#e5e7eb] [&_.ant-select-selector]:!bg-white dark:[&_.ant-select-selector]:!border-[#3a404d] dark:[&_.ant-select-selector]:!bg-[#1f2430] [&_.ant-select-selection-item]:!leading-8"
    />
  );
}
