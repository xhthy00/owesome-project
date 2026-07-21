import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Form, InputNumber, Space, Table, Typography, message } from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  educationApi,
  type AnomalyRuleItem,
  type ReportConfig
} from "@/api/education";

const RULE_TYPE_LABEL: Record<string, string> = {
  critical: "临界生",
  regression: "大幅退步",
  imbalanced: "偏科"
};

const COMPARE_LABEL: Record<string, string> = {
  pass_line: "及格线",
  prev_exam: "上次成绩",
  self_subjects: "同生各科"
};

export default function AnomalyRulesPage() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rules, setRules] = useState<AnomalyRuleItem[]>([]);

  const applyConfig = useCallback(
    (cfg: ReportConfig) => {
      form.setFieldsValue({
        pass_percent: cfg.pass_percent ?? Math.round((cfg.pass_ratio ?? 0.6) * 1000) / 10,
        excellent_percent:
          cfg.excellent_percent ?? Math.round((cfg.excellent_ratio ?? 0.85) * 1000) / 10,
        default_full_score: cfg.default_full_score,
        critical_margin: cfg.critical_margin,
        regression_threshold: cfg.regression_threshold,
        imbalance_score_gap: cfg.imbalance_score_gap
      });
      setRules(cfg.anomaly_rules || []);
    },
    [form]
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const cfg = await educationApi.getReportConfig();
      applyConfig(cfg);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [applyConfig]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const cfg = await educationApi.updateReportConfig({
        pass_ratio: Number(values.pass_percent) / 100,
        excellent_ratio: Number(values.excellent_percent) / 100,
        default_full_score: values.default_full_score,
        critical_margin: values.critical_margin,
        regression_threshold: values.regression_threshold,
        imbalance_score_gap: values.imbalance_score_gap
      });
      applyConfig(cfg);
      message.success("已保存到系统库");
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const onReset = async () => {
    setSaving(true);
    try {
      const cfg = await educationApi.resetReportConfig();
      applyConfig(cfg);
      message.success("已恢复默认并写入数据库");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "重置失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-6 pb-10">
      <div>
        <Typography.Title level={4} style={{ marginBottom: 4 }}>
          异常规则配置
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          配置存于系统库表 <code>edu_anomaly_config</code>，影响分层预警等异常判定。默认与历史行为一致。
        </Typography.Paragraph>
      </div>

      <Alert
        type="info"
        showIcon
        message="及格/优秀按「你保存的百分比 × 卷面满分」计算（例如满分150、及格30%→45分）。无卷面满分时才用满分兜底换算绝对分。"
      />

      <Card title="经典阈值" loading={loading}>
        <Form form={form} layout="vertical" requiredMark={false}>
          <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
            <Form.Item
              name="pass_percent"
              label="及格线（%）"
              rules={[{ required: true, message: "必填" }]}
              tooltip="占卷面满分的比例。例：60% 在 100 分卷→60 分，150 分卷→90 分"
            >
              <InputNumber className="w-full" min={0} max={100} addonAfter="%" />
            </Form.Item>
            <Form.Item
              name="excellent_percent"
              label="优秀线（%）"
              rules={[{ required: true, message: "必填" }]}
              tooltip="占卷面满分的比例。例：85% 在 100 分卷→85 分，150 分卷→127.5 分"
            >
              <InputNumber className="w-full" min={0} max={100} addonAfter="%" />
            </Form.Item>
            <Form.Item
              name="default_full_score"
              label="满分兜底"
              rules={[{ required: true, message: "必填" }]}
              tooltip="仅当成绩数据读不到卷面满分时使用；有 exam_score 时仍以实际满分为准。"
            >
              <InputNumber className="w-full" min={1} />
            </Form.Item>
            <Form.Item
              name="critical_margin"
              label="临界半径（及格线 ±N 分）"
              rules={[{ required: true, message: "必填" }]}
              tooltip="按绝对分：落在 [及格线−N, 及格线+N) 视为临界生；及格线由满分×及格%算出"
            >
              <InputNumber className="w-full" min={0} />
            </Form.Item>
            <Form.Item
              name="regression_threshold"
              label="退步阈值（负数）"
              rules={[{ required: true, message: "必填" }]}
              tooltip="本次−上次 ≤ 该值则算大幅退步，默认 -10"
            >
              <InputNumber className="w-full" />
            </Form.Item>
            <Form.Item
              name="imbalance_score_gap"
              label="偏科分差下限"
              rules={[{ required: true, message: "必填" }]}
              tooltip="同生最高科−最低科 ≥ 该值且最低科低于及格线"
            >
              <InputNumber className="w-full" min={0} />
            </Form.Item>
          </div>
          <Space>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void onSave()}>
              保存
            </Button>
            <Button icon={<ReloadOutlined />} loading={saving} onClick={() => void onReset()}>
              恢复默认
            </Button>
            <Button onClick={() => void load()} disabled={loading || saving}>
              刷新
            </Button>
          </Space>
        </Form>
      </Card>

      <Card title="当前生效规则（五类参数预览）" loading={loading}>
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={rules}
          columns={[
            {
              title: "规则",
              dataIndex: "anomaly_type",
              render: (t: string) => RULE_TYPE_LABEL[t] || t
            },
            {
              title: "启用",
              dataIndex: "enabled",
              width: 70,
              render: (v: boolean) => (v ? "是" : "否")
            },
            {
              title: "对比对象",
              dataIndex: "compare_target",
              render: (t: string) => COMPARE_LABEL[t] || t
            },
            {
              title: "阈值",
              dataIndex: "threshold",
              render: (v: number | null | undefined) => (v == null ? "—" : v)
            },
            {
              title: "连续次数",
              dataIndex: "consecutive_n",
              width: 90
            },
            {
              title: "波动",
              key: "fluctuation",
              render: (_: unknown, row: AnomalyRuleItem) =>
                `${row.fluctuation_mode || "abs"} / ${row.fluctuation_value ?? "—"}`
            },
            {
              title: "范围",
              key: "range",
              render: (_: unknown, row: AnomalyRuleItem) => {
                if (row.range_lo_offset != null || row.range_hi_offset != null) {
                  return `offset [${row.range_lo_offset ?? "—"}, ${row.range_hi_offset ?? "—"})`;
                }
                if (row.range_lo != null || row.range_hi != null) {
                  return `[${row.range_lo ?? "—"}, ${row.range_hi ?? "—"})`;
                }
                return "—";
              }
            }
          ]}
        />
      </Card>
      </div>
    </div>
  );
}
