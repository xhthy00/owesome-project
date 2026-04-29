import { Card, Typography } from "antd";
import { useRouter } from "next/router";

export default function SharePage() {
  const router = useRouter();
  const token = router.query.token;

  return (
    <div className="mx-auto mt-8 max-w-3xl">
      <Card>
        <Typography.Title level={4}>Shared Conversation</Typography.Title>
        <Typography.Paragraph>
          Token: <code>{String(token ?? "")}</code>
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
