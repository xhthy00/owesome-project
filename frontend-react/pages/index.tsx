import { AudioOutlined, BarChartOutlined, FileTextOutlined, FundOutlined, ToolOutlined } from "@ant-design/icons";
import { Card, Input, Typography } from "antd";
import Image from "next/image";
import { useRouter } from "next/router";
import { useState } from "react";
import DatasourcePicker from "@/components/chat/DatasourcePicker";

export default function HomePage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const defaultDatasourceId = Number(process.env.NEXT_PUBLIC_DEFAULT_DATASOURCE_ID ?? 1);
  const [selectedDatasourceId, setSelectedDatasourceId] = useState<number>(defaultDatasourceId);
  const cards = [
    { title: "销售数据分析", desc: "分析销售CSV数据，生成可视化网页报告", icon: <BarChartOutlined /> },
    { title: "数据库画像与分析报告", desc: "连接数据库后，生成数据库画像并生成可视化网页报告", icon: <FileTextOutlined /> },
    { title: "金融财报深度分析", desc: "分析季度报告，生成数据可视化报告", icon: <FundOutlined /> },
    { title: "创建SQL分析技能", desc: "使用skill-creator创建一个实用的SQL数据分析技能", icon: <ToolOutlined /> }
  ];

  const canSend = !!prompt.trim();

  const handleSend = () => {
    const value = prompt.trim();
    if (!value) return;
    void router.push({
      pathname: "/chat",
      query: { q: value, ds: selectedDatasourceId }
    });
  };

  return (
    <div className="dbgpt-ui-font flex h-full flex-col bg-[#f7f7f9] dark:bg-[#0f1012]">
      <div className="flex flex-1 flex-col items-center overflow-auto bg-white px-8 pb-6 pt-6 dark:bg-[#111217]">
        <Typography.Title
          level={1}
          className="dbgpt-title-font !mb-4 !flex !items-center !gap-4 !text-4xl md:!text-5xl !text-gray-900 dark:!text-gray-100"
        >
          <span className="relative inline-flex h-20 w-20 items-center justify-center overflow-hidden rounded-xl border border-gray-100 bg-white shadow-md dark:border-[#33353b] dark:bg-[#1a1b1e]">
            <Image src="/logo-mark.svg" alt="logo" width={200} height={200} className="rounded-lg" />
          </span>
         启明 AI数据助理
        </Typography.Title>
        <Typography.Text className="dbgpt-subtitle-font mb-10 !text-sm md:!text-base !font-light text-gray-400 dark:text-gray-500">
          Agentic Data Driven Decisions
        </Typography.Text>

        <div className="w-full max-w-[860px] rounded-[28px] border border-gray-100 bg-white/95 px-7 pb-4 pt-5 shadow-[0_16px_48px_rgba(0,0,0,0.12),0_6px_20px_rgba(0,0,0,0.08)] backdrop-blur-md dark:border-[#33353b] dark:bg-[#1e1f24]/95 dark:shadow-[0_16px_48px_rgba(0,0,0,0.4)]">
          <Input.TextArea
            autoSize={{ minRows: 3, maxRows: 4 }}
            variant="borderless"
            placeholder="向您的数据库提问，上传CSV，或生成报告..."
            className="dbgpt-input-font !text-lg !leading-8"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onPressEnter={(e) => {
              if (e.shiftKey) return;
              e.preventDefault();
              handleSend();
            }}
          />
          <div className="mt-1 flex items-center justify-between text-[#8b97aa]">
            <div className="flex items-center gap-3 text-xs">
              <span>+</span>
              <span>⚡</span>
              <span>▣</span>
              <span>◱</span>
              <DatasourcePicker value={selectedDatasourceId} onChange={setSelectedDatasourceId} />
            </div>
            <div className="flex items-center gap-3">
              <AudioOutlined />
              <button
                onClick={handleSend}
                disabled={!canSend}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-gray-100 text-[#90a2be] transition-colors enabled:cursor-pointer enabled:hover:bg-blue-500 enabled:hover:text-white disabled:opacity-60 dark:bg-[#2a2b2f]"
              >
                ↑
              </button>
            </div>
          </div>
        </div>

        <div className="mt-8 w-full max-w-[860px]">
          <div className="mb-3 text-center text-xs font-medium text-[#98a4b8]">推荐示例</div>
          <div className="grid grid-cols-2 gap-[14px]">
            {cards.map((card, idx) => (
              <Card
                key={card.title}
                className={`!rounded-[14px] !border ${
                  idx === 0
                    ? "!border-blue-200/60 !bg-[#eaf3ff]"
                    : idx === 1
                      ? "!border-emerald-200/60 !bg-[#e9f8f2]"
                      : idx === 2
                        ? "!border-violet-200/60 !bg-[#f3edff]"
                        : "!border-amber-200/60 !bg-[#fff3e8]"
                }`}
                styles={{ body: { padding: 13 } }}
              >
                <div className="flex gap-3">
                  <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-lg bg-white/70 text-[#4978c8]">
                    {card.icon}
                  </div>
                  <div>
                    <div className="mb-1 text-sm font-semibold text-[#2a3347]">{card.title}</div>
                    <div className="text-xs leading-5 text-[#5f6c84]">{card.desc}</div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </div>

        <div className="mt-7 text-sm text-[#7e8ca3]">
          <span className="rounded-full bg-white px-3 py-1 shadow-sm">Agentic Data Driven Decisions</span>
        </div>
      </div>
    </div>
  );
}
