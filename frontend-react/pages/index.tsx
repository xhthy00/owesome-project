import {
  BarChartOutlined,
  FundViewOutlined,
  RadarChartOutlined,
  AlertOutlined,
} from "@ant-design/icons";
import { Card, Input, Typography } from "antd";
import Image from "next/image";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import DatasourcePicker from "@/components/chat/DatasourcePicker";

/** 提问示例：取自 docs/education_report_prompts.md 附录 D / 标准用户问法 */
const EXAMPLE_PROMPTS = [
  {
    title: "班级总览报告",
    desc: "人数、均分、及格优秀率、分数段与班级能力画像",
    prompt:
      "扬州中学高三(10)班连淮扬镇数学成绩总览，给我一份班级总览报告：人数均分及格优秀、分数段、能力画像和年级位置就行，别做各班对比，也别出临界生名单。",
    icon: <BarChartOutlined />,
  },
  {
    title: "科目诊断报告",
    desc: "小题得分率与知识点掌握情况分析",
    prompt:
      "请生成【科目诊断报告】：扬州中学高三(10)班，「连淮扬镇」，数学。小题+知识点掌握情况。",
    icon: <RadarChartOutlined />,
  },
  {
    title: "班级横向对比",
    desc: "全校各班均分、及格率等多维横向对比",
    prompt:
      "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析，出班级横向对比报告，不要做成单班总览。",
    icon: <FundViewOutlined />,
  },
  {
    title: "临界生预警",
    desc: "临界生、大幅退步与偏科名单预警",
    prompt: "扬州中学高三(10)班数学临界生预警报告",
    icon: <AlertOutlined />,
  },
] as const;

export default function HomePage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const defaultDatasourceId = Number(process.env.NEXT_PUBLIC_DEFAULT_DATASOURCE_ID ?? 1);
  const [selectedDatasourceId, setSelectedDatasourceId] = useState<number>(defaultDatasourceId);

  // 技能市场“在探索广场打开”：把提示词预填到对话框，但不自动提交（用户可继续修改）。
  useEffect(() => {
    const prefill = sessionStorage.getItem("prefill_prompt");
    if (prefill) {
      setPrompt(prefill);
      sessionStorage.removeItem("prefill_prompt");
    }
  }, []);

  const canSend = !!prompt.trim();

  const handleSend = (text?: string) => {
    const value = (text ?? prompt).trim();
    if (!value) return;
    void router.push({
      pathname: "/chat",
      query: { q: value, ds: selectedDatasourceId },
    });
  };

  return (
    <div className="dbgpt-ui-font flex h-full flex-col bg-[#f7f7f9] dark:bg-[#0f1012]">
      <div className="flex flex-1 flex-col items-center overflow-auto bg-white px-8 pb-6 pt-6 dark:bg-[#111217]">
        <Typography.Title
          level={1}
          className="dbgpt-title-font !mb-4 !flex !items-center !gap-3 !text-3xl md:!text-4xl !text-gray-900 dark:!text-gray-100"
        >
          <span className="relative inline-flex h-14 w-14 items-center justify-center overflow-hidden rounded-xl border border-gray-100 bg-white shadow-md dark:border-[#33353b] dark:bg-[#1a1b1e]">
            <Image src="/logo-mark.svg" alt="logo" width={140} height={140} className="rounded-lg" />
          </span>
          学情（考情）AI智能分析助手
        </Typography.Title>
        <Typography.Text className="dbgpt-subtitle-font mb-10 !text-sm md:!text-base !font-light text-gray-400 dark:text-gray-500">
          Agentic Data Driven Decisions
        </Typography.Text>

        <div className="w-full max-w-[860px] rounded-[28px] border border-gray-100 bg-white/95 px-7 pb-4 pt-5 shadow-[0_16px_48px_rgba(0,0,0,0.12),0_6px_20px_rgba(0,0,0,0.08)] backdrop-blur-md dark:border-[#33353b] dark:bg-[#1e1f24]/95 dark:shadow-[0_16px_48px_rgba(0,0,0,0.4)]">
          <Input.TextArea
            autoSize={{ minRows: 3, maxRows: 4 }}
            variant="borderless"
            placeholder="向您关心的成绩提问，生成分析报告"
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
              <DatasourcePicker value={selectedDatasourceId} onChange={setSelectedDatasourceId} />
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={() => handleSend()}
                disabled={!canSend}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-gray-100 text-[#90a2be] transition-colors enabled:cursor-pointer enabled:hover:bg-blue-500 enabled:hover:text-white disabled:opacity-60 dark:bg-[#2a2b2f]"
              >
                ↑
              </button>
            </div>
          </div>
        </div>

        <div className="mt-8 w-full max-w-[860px]">
          <div className="mb-3 text-center text-xs font-medium text-[#98a4b8]">提问示例</div>
          <div className="grid grid-cols-1 gap-[14px] sm:grid-cols-2">
            {EXAMPLE_PROMPTS.map((card, idx) => (
              <Card
                key={card.title}
                hoverable
                onClick={() => {
                  setPrompt(card.prompt);
                }}
                className={`!cursor-pointer !rounded-[14px] !border transition-shadow hover:!shadow-md ${
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
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/70 text-[#4978c8]">
                    {card.icon}
                  </div>
                  <div className="min-w-0">
                    <div className="mb-1 text-sm font-semibold text-[#2a3347]">{card.title}</div>
                    <div className="text-xs leading-5 text-[#5f6c84]">{card.desc}</div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
