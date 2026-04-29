import { BulbOutlined } from "@ant-design/icons";
import { Card, Space, Typography } from "antd";

const suggestions = [
  "帮我分析销售数据并给出图表建议",
  "根据上传文档总结关键风险点",
  "生成一份本周项目进展简报"
];

export default function ChatDefault() {
  return (
    <div className="mx-auto w-5/6 py-10">
      <Typography.Title level={4}>欢迎使用启明AI分析助手</Typography.Title>
      <Typography.Paragraph className="oc-muted">
        Start with a prompt or pick one of the recommended questions.
      </Typography.Paragraph>
      <Space direction="vertical" className="w-full">
        {suggestions.map((item) => (
          <Card key={item} size="small" className="!rounded-xl">
            <Space>
              <BulbOutlined />
              <span>{item}</span>
            </Space>
          </Card>
        ))}
      </Space>
    </div>
  );
}
