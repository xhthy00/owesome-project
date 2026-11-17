import { Card, Typography } from "antd";

type Props = {
  title: string;
  description: string;
};

export default function SectionPlaceholder({ title, description }: Props) {
  return (
    <Card>
      <Typography.Title level={4}>{title}</Typography.Title>
      <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
    </Card>
  );
}
