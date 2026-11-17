import { Card, Tabs, Typography } from "antd";
import Link from "@/routing/Link";

const items = [
  { key: "app", label: "App", children: <Link href="/construct/app">进入应用管理</Link> },
  { key: "database", label: "Datasource", children: <Link href="/construct/database">进入数据源管理</Link> },
  { key: "permission", label: "Permission", children: <Link href="/construct/permission">进入权限管理</Link> },
  { key: "knowledge", label: "Knowledge", children: <Link href="/construct/knowledge">进入知识库管理</Link> }
];

export default function ConstructPage() {
  return (
    <Card>
      <Typography.Title level={4}>Construct</Typography.Title>
      <Tabs items={items} />
    </Card>
  );
}
