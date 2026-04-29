import { PauseCircleOutlined, SendOutlined, SettingOutlined } from "@ant-design/icons";
import { Button, Input, Select, Slider, Space, Typography } from "antd";
import { useState } from "react";

type Props = {
  loading: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
};

export default function ChatInputPanel({ loading, onSend, onStop }: Props) {
  const [input, setInput] = useState("");
  const [temperature, setTemperature] = useState(0.6);
  const [model, setModel] = useState("dbgpt-pro");

  const submit = () => {
    onSend(input);
    setInput("");
  };

  return (
    <div className="oc-panel p-3">
      <div className="mb-2 flex items-center justify-between">
        <Space>
          <SettingOutlined className="oc-muted" />
          <Select
            size="small"
            value={model}
            style={{ width: 150 }}
            options={[
              { value: "dbgpt-pro", label: "dbgpt-pro" },
              { value: "dbgpt-reasoner", label: "dbgpt-reasoner" }
            ]}
            onChange={setModel}
          />
        </Space>
        <Space size={6}>
          <Typography.Text className="oc-muted text-xs">Temperature</Typography.Text>
          <Slider
            min={0}
            max={1}
            step={0.1}
            value={temperature}
            onChange={setTemperature}
            style={{ width: 100 }}
          />
        </Space>
      </div>

      <Space.Compact block>
        <Input.TextArea
          value={input}
          autoSize={{ minRows: 2, maxRows: 4 }}
          placeholder="Type your question..."
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {loading ? (
          <Button danger icon={<PauseCircleOutlined />} onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button type="primary" icon={<SendOutlined />} onClick={submit}>
            Send
          </Button>
        )}
      </Space.Compact>
    </div>
  );
}
