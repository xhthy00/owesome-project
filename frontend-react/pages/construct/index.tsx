import { Card, Tabs, Typography } from "antd";

const items = [
  { key: "app", label: "App", children: "Construct App placeholder" },
  { key: "flow", label: "Flow", children: "Construct Flow placeholder" },
  { key: "knowledge", label: "Knowledge", children: "Construct Knowledge placeholder" },
  { key: "prompt", label: "Prompt", children: "Construct Prompt placeholder" }
];

export default function ConstructPage() {
  return (
    <Card>
      <Typography.Title level={4}>Construct</Typography.Title>
      <Tabs items={items} />
    </Card>
  );
}
