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

type Tone = "blue" | "green" | "violet" | "amber";

/** 点缀色走 tokens.css 的 --accent-*,浅/深色模式各一套 */
const TONE_CARD: Record<Tone, string> = {
  blue: "!border-accent-blue-border !bg-accent-blue-tint hover:!border-accent-blue",
  green: "!border-accent-green-border !bg-accent-green-tint hover:!border-accent-green",
  violet: "!border-accent-violet-border !bg-accent-violet-tint hover:!border-accent-violet",
  amber: "!border-accent-amber-border !bg-accent-amber-tint hover:!border-accent-amber",
};

const TONE_ICON: Record<Tone, string> = {
  blue: "bg-surface-1 text-accent-blue",
  green: "bg-surface-1 text-accent-green",
  violet: "bg-surface-1 text-accent-violet",
  amber: "bg-surface-1 text-accent-amber",
};

/** 提问示例：取自 docs/education_report_prompts.md 附录 D / 标准用户问法 */
const EXAMPLE_PROMPTS = [
  {
    title: "班级总览报告",
    desc: "人数、均分、及格优秀率、分数段与班级能力画像",
    tone: "blue" as Tone,
    prompt:
      "扬州中学高三(10)班连淮扬镇数学成绩总览，给我一份班级总览报告：人数均分及格优秀、分数段、能力画像和年级位置就行，别做各班对比，也别出临界生名单。",
    icon: <BarChartOutlined />,
  },
  {
    title: "科目诊断报告",
    desc: "小题得分率与知识点掌握情况分析",
    tone: "green" as Tone,
    prompt:
      "请生成【科目诊断报告】：扬州中学高三(10)班，「连淮扬镇」，数学。小题+知识点掌握情况。",
    icon: <RadarChartOutlined />,
  },
  {
    title: "班级横向对比",
    desc: "全校各班均分、及格率等多维横向对比",
    tone: "violet" as Tone,
    prompt:
      "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析，出班级横向对比报告，不要做成单班总览。",
    icon: <FundViewOutlined />,
  },
  {
    title: "临界生预警",
    desc: "临界生、大幅退步与偏科名单预警",
    tone: "amber" as Tone,
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
    <div className="flex h-full flex-col bg-surface-0">
      <div className="flex flex-1 flex-col items-center overflow-auto bg-surface-1 px-6 pb-10 pt-10">
        <Typography.Title
          level={1}
          className="dbgpt-title-font !mb-3 !flex !items-center !gap-3 !text-3xl md:!text-4xl !text-ink-1"
        >
          <span className="relative inline-flex h-14 w-14 items-center justify-center overflow-hidden rounded-xl border border-line-1 bg-surface-1 shadow-1">
            <Image src="/logo-mark.svg" alt="" width={140} height={140} className="rounded-lg" />
          </span>
          学情AI智能分析助手
        </Typography.Title>
        <Typography.Text className="dbgpt-subtitle-font mb-9 !text-sm !text-ink-3">
          Agentic Data Driven Decisions
        </Typography.Text>

        <div className="w-full max-w-[860px] rounded-xl border border-line-1 bg-surface-1 px-6 pb-3 pt-4 shadow-3">
          <Input.TextArea
            autoSize={{ minRows: 3, maxRows: 4 }}
            variant="borderless"
            placeholder="向您关心的成绩提问，生成分析报告"
            aria-label="提问内容"
            className="dbgpt-input-font !resize-none !text-lg !leading-8 !text-ink-1"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onPressEnter={(e) => {
              if (e.shiftKey) return;
              e.preventDefault();
              handleSend();
            }}
          />
          <div className="mt-1 flex items-center justify-between">
            <div className="flex items-center gap-3 text-xs text-ink-3">
              <DatasourcePicker value={selectedDatasourceId} onChange={setSelectedDatasourceId} />
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden text-xs text-ink-4 sm:inline">Enter 发送 · Shift+Enter 换行</span>
              <button
                type="button"
                onClick={() => handleSend()}
                disabled={!canSend}
                aria-label="发送"
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-2 text-ink-3 transition-colors enabled:cursor-pointer enabled:hover:bg-brand enabled:hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className="text-base leading-none">↑</span>
              </button>
            </div>
          </div>
        </div>

        <div className="mt-8 w-full max-w-[860px]">
          <div className="mb-3 text-xs font-medium text-ink-3">提问示例</div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {EXAMPLE_PROMPTS.map((card) => (
              <Card
                key={card.title}
                hoverable
                tabIndex={0}
                role="button"
                aria-label={`使用示例：${card.title}`}
                onClick={() => {
                  setPrompt(card.prompt);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setPrompt(card.prompt);
                  }
                }}
                className={`!cursor-pointer !rounded-lg !shadow-none transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand ${TONE_CARD[card.tone]}`}
                styles={{ body: { padding: 14 } }}
              >
                <div className="flex gap-3">
                  <div
                    className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${TONE_ICON[card.tone]}`}
                  >
                    {card.icon}
                  </div>
                  <div className="min-w-0">
                    <div className="mb-1 text-sm font-semibold text-ink-1">{card.title}</div>
                    <div className="text-xs leading-5 text-ink-2">{card.desc}</div>
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
