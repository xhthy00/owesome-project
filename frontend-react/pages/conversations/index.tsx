import { List, Typography } from "antd";
import Link from "next/link";

const conversations = ["Release planning", "SQL analysis", "Knowledge assistant"];

export default function ConversationsPage() {
  return (
    <div>
      <Typography.Title level={4}>Conversations</Typography.Title>
      <List
        bordered
        dataSource={conversations}
        renderItem={(item, index) => (
          <List.Item>
            <Link href={`/chat?id=${index + 1}`}>{item}</Link>
          </List.Item>
        )}
      />
    </div>
  );
}
