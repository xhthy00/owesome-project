import { BulbOutlined } from "@ant-design/icons";
import { FloatButton } from "antd";

type Props = {
  onPick: (prompt: string) => void;
};

const quickPrompts = [
  "帮我总结当前会话关键结论",
  "继续执行下一步并展示结果",
  "把当前结果转成简报大纲"
];

export default function PromptBot({ onPick }: Props) {
  return (
    <FloatButton.Group
      trigger="click"
      type="primary"
      icon={<BulbOutlined />}
      tooltip="PromptBot"
      className="z-[998]"
    >
      {quickPrompts.map((prompt) => (
        <FloatButton key={prompt} description={prompt} onClick={() => onPick(prompt)} />
      ))}
    </FloatButton.Group>
  );
}
