import {
  AimOutlined,
  AlertOutlined,
  ApartmentOutlined,
  AuditOutlined,
  CopyOutlined,
  DashboardOutlined,
  DotChartOutlined,
  FundProjectionScreenOutlined,
  IdcardOutlined,
  LineChartOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { Button, Card, Modal, Skeleton, Tag, Typography, message } from "antd";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";

/**
 * 学情报告技能市场。
 * 卡片数据来自静态配置 public/education_skills.json（与 config/education_skills.json 同源）。
 * 提示词面向普通教师、不含库/表/字段/SQL 等技术术语；所有结论由 edu 库实时查询决定。
 */

type SkillTag = string;

interface SkillItem {
  id: string;
  name: string;
  icon: string;
  tags: SkillTag[];
  report_type: string;
  audience_default: string;
  desc: string;
  prompt: string;
}

interface SkillsConfig {
  skills: SkillItem[];
}

/** icon key → antd 图标组件（与 config icon_catalog 一一对应） */
const ICON_MAP: Record<string, ReactNode> = {
  "dashboard-outlined": <DashboardOutlined />,
  "apartment-outlined": <ApartmentOutlined />,
  "aim-outlined": <AimOutlined />,
  "idcard-outlined": <IdcardOutlined />,
  "line-chart-outlined": <LineChartOutlined />,
  "alert-outlined": <AlertOutlined />,
  "dot-chart-outlined": <DotChartOutlined />,
  "fund-projection-screen-outlined": <FundProjectionScreenOutlined />,
  "audit-outlined": <AuditOutlined />
};

function iconOf(key: string): ReactNode {
  return ICON_MAP[key] ?? <ThunderboltOutlined />;
}

export default function ConstructSkillsPage() {
  const router = useRouter();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<SkillItem | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/education_skills.json", { cache: "no-cache" })
      .then((r) => r.json() as Promise<SkillsConfig>)
      .then((cfg) => {
        if (cancelled) return;
        setSkills(Array.isArray(cfg.skills) ? cfg.skills : []);
      })
      .catch(() => {
        if (!cancelled) message.error("技能配置加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const cards = useMemo(() => skills, [skills]);

  const openModal = (s: SkillItem) => {
    setActive(s);
    setCopied(false);
  };

  const closeModal = () => setActive(null);

  const handleCopy = async () => {
    if (!active) return;
    try {
      await navigator.clipboard.writeText(active.prompt);
      setCopied(true);
      message.success("已复制到剪贴板");
      setTimeout(() => setCopied(false), 1600);
    } catch {
      message.error("复制失败，请手动选择提示词复制");
    }
  };

  const handleOpenInExplore = () => {
    if (!active) return;
    try {
      sessionStorage.setItem("prefill_prompt", active.prompt);
    } catch {
      message.error("无法写入会话存储，请改用复制到剪贴板");
      return;
    }
    void router.push("/");
  };

  return (
    <div className="dbgpt-ui-font p-6" style={{ background: "var(--oc-bg-base)", minHeight: "100%" }}>
      <div style={{ maxWidth: 1160, margin: "0 auto" }}>
        {/* 页头 */}
        <div style={{ marginBottom: 28, maxWidth: "60ch" }}>
          <Typography.Title level={3} style={{ marginBottom: 8, color: "var(--oc-text-strong)" }}>
            学情报告技能
          </Typography.Title>
          <Typography.Paragraph style={{ margin: 0, color: "var(--oc-text-soft)", fontSize: 15 }}>
            选择技能查看详情，复制提示词到对话框即可问数。所有结论由 edu 库实时查询得出。
          </Typography.Paragraph>
        </div>

        {/* 卡片网格 */}
        {loading ? (
          <div
            style={{
              display: "grid",
              gap: 14,
              gridTemplateColumns: "repeat(auto-fill, minmax(316px, 1fr))"
            }}
          >
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i} style={cardStyle}>
                <Skeleton active paragraph={{ rows: 3 }} />
              </Card>
            ))}
          </div>
        ) : (
          <div
            style={{
              display: "grid",
              gap: 14,
              gridTemplateColumns: "repeat(auto-fill, minmax(316px, 1fr))"
            }}
          >
            {cards.map((s) => (
              <Card
                key={s.id}
                hoverable
                style={cardStyle}
                styles={{ body: { padding: 20, height: "100%", display: "flex", flexDirection: "column" } }}
                onClick={() => openModal(s)}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 13, marginBottom: 14 }}>
                  <div style={iconChipStyle}>{iconOf(s.icon)}</div>
                  <Typography.Title
                    level={5}
                    style={{ margin: 0, color: "var(--oc-text-strong)", lineHeight: 1.3 }}
                  >
                    {s.name}
                  </Typography.Title>
                </div>
                <Typography.Paragraph
                  style={{
                    margin: 0,
                    color: "var(--oc-text-soft)",
                    fontSize: 13,
                    lineHeight: 1.7,
                    flex: 1,
                    overflow: "hidden"
                  }}
                  ellipsis={{ rows: 3 }}
                >
                  {s.desc}
                </Typography.Paragraph>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5, paddingTop: 12 }}>
                  {s.tags.map((t, i) => (
                    <Tag key={t} style={i === 0 ? tagBrandStyle : tagNeutralStyle}>
                      {t}
                    </Tag>
                  ))}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* 技能详情弹框 */}
      <Modal
        open={!!active}
        onCancel={closeModal}
        width={740}
        footer={null}
        destroyOnHidden
        styles={{ body: { padding: 0 } }}
      >
        {active && (
          <div style={{ color: "var(--oc-text)" }}>
            {/* 头部 */}
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 16,
                padding: "26px 28px 20px",
                borderBottom: "1px solid var(--oc-border)"
              }}
            >
              <div style={modalIconStyle}>{iconOf(active.icon)}</div>
              <div>
                <Typography.Title level={3} style={{ margin: 0, marginBottom: 7, color: "var(--oc-text-strong)" }}>
                  {active.name}
                </Typography.Title>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {active.tags.map((t, i) => (
                    <Tag key={t} style={i === 0 ? tagBrandStyle : tagNeutralStyle}>
                      {t}
                    </Tag>
                  ))}
                </div>
              </div>
            </div>

            {/* 正文 */}
            <div style={{ padding: "22px 28px", maxHeight: "52vh", overflow: "auto" }}>
              <div style={sectionLabelStyle}>技能简介</div>
              <Typography.Paragraph
                style={{ margin: 0, marginBottom: 22, color: "var(--oc-text-soft)", fontSize: 15, lineHeight: 1.8 }}
              >
                {active.desc}
              </Typography.Paragraph>
              <div style={sectionLabelStyle}>技能内容 · 提示词</div>
              <pre style={promptBoxStyle}>{active.prompt}</pre>
            </div>

            {/* 底部按钮 */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-end",
                gap: 12,
                padding: "18px 28px",
                borderTop: "1px solid var(--oc-border)"
              }}
            >
              <Button icon={<CopyOutlined />} onClick={handleCopy} style={btnSecondaryStyle}>
                {copied ? "已复制" : "复制到剪贴板"}
              </Button>
              <Button type="primary" onClick={handleOpenInExplore}>
                在探索广场打开
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

/* ---- 样式（用 --oc-* 变量，自动深浅色） ---- */

const cardStyle: React.CSSProperties = {
  borderRadius: 12,
  border: "1px solid var(--oc-border)",
  background: "var(--oc-bg-elevated)",
  cursor: "pointer"
};

const iconChipStyle: React.CSSProperties = {
  width: 42,
  height: 42,
  borderRadius: 11,
  display: "grid",
  placeItems: "center",
  fontSize: 22,
  color: "var(--oc-text-strong)",
  background: "var(--oc-bg-soft)",
  flexShrink: 0
};

const tagBrandStyle: React.CSSProperties = {
  margin: 0,
  borderRadius: 7,
  fontSize: 11,
  padding: "5px 9px",
  lineHeight: 1,
  border: "1px solid color-mix(in srgb, var(--oc-primary) 26%, transparent)",
  background: "color-mix(in srgb, var(--oc-primary) 10%, transparent)",
  color: "color-mix(in srgb, var(--oc-primary) 82%, #000)"
};

const tagNeutralStyle: React.CSSProperties = {
  margin: 0,
  borderRadius: 7,
  fontSize: 11,
  padding: "5px 9px",
  lineHeight: 1,
  border: "1px solid var(--oc-border)",
  background: "var(--oc-bg-soft)",
  color: "var(--oc-text-soft)"
};

const modalIconStyle: React.CSSProperties = {
  width: 54,
  height: 54,
  borderRadius: 12,
  flexShrink: 0,
  display: "grid",
  placeItems: "center",
  fontSize: 27,
  color: "var(--oc-primary)",
  background: "color-mix(in srgb, var(--oc-primary) 11%, transparent)"
};

const sectionLabelStyle: React.CSSProperties = {
  fontSize: 11,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--oc-text-weaker)",
  fontWeight: 600,
  marginBottom: 10
};

const promptBoxStyle: React.CSSProperties = {
  margin: 0,
  background: "var(--oc-bg-base)",
  border: "1px solid var(--oc-border)",
  borderRadius: 10,
  padding: "18px 20px",
  fontSize: 12.5,
  lineHeight: 1.8,
  color: "var(--oc-text)",
  fontFamily:
    "ui-monospace, Menlo, Consolas, monospace",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word"
};

const btnSecondaryStyle: React.CSSProperties = {
  borderRadius: 9
};
