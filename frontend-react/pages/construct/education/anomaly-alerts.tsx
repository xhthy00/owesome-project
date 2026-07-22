import { CheckOutlined, EyeOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Drawer,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import {
  educationApi,
  type AnomalyAlertItem
} from "@/api/education";

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "pending", label: "未处理" },
  { value: "confirmed", label: "已处理" }
];

type StudentRow = {
  key: string;
  student_id: string;
  subject: string;
  score: string;
  reason: string;
};

function asList(payload: Record<string, unknown> | undefined, key: string): Record<string, unknown>[] {
  const raw = payload?.[key];
  return Array.isArray(raw) ? (raw as Record<string, unknown>[]) : [];
}

function toStudentRows(items: Record<string, unknown>[], prefix: string): StudentRow[] {
  return items.map((it, idx) => {
    const studentId = String(it.student_id || it.name || "—");
    const subject = String(it.subject || it.low_subject || it.subject_name || "—");
    let score = "—";
    if (it.score != null && it.prev_score != null) {
      score = `${it.score} ← ${it.prev_score}`;
    } else if (it.score != null) {
      score = String(it.score);
    }
    return {
      key: `${prefix}-${studentId}-${idx}`,
      student_id: studentId,
      subject,
      score,
      reason: String(it.reason || "—")
    };
  });
}

const studentColumns: ColumnsType<StudentRow> = [
  { title: "学生", dataIndex: "student_id", width: 120 },
  { title: "科目", dataIndex: "subject", width: 100 },
  { title: "分数", dataIndex: "score", width: 110 },
  { title: "说明", dataIndex: "reason", ellipsis: true }
];

export default function AnomalyAlertsPage() {
  const [loading, setLoading] = useState(false);
  const [accessible, setAccessible] = useState(true);
  const [denyMessage, setDenyMessage] = useState("");
  const [status, setStatus] = useState<string>("pending");
  const [items, setItems] = useState<AnomalyAlertItem[]>([]);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<AnomalyAlertItem | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await educationApi.listAnomalyAlerts({
        status: (status as "pending" | "confirmed" | "") || "",
        limit: 100
      });
      setAccessible(res.accessible !== false);
      setDenyMessage(res.message || "");
      setItems(res.items || []);
      setTotal(res.total || 0);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDetail = async (row: AnomalyAlertItem) => {
    try {
      const full = await educationApi.getAnomalyAlert(row.id);
      setDetail(full);
      setNote("");
      setDetailOpen(true);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载详情失败");
    }
  };

  const onConfirm = async () => {
    if (!detail) return;
    setConfirming(true);
    try {
      const updated = await educationApi.confirmAnomalyAlert(detail.id, note);
      setDetail(updated);
      message.success("已确认处理");
      void load();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "确认失败");
    } finally {
      setConfirming(false);
    }
  };

  const detailTables = (() => {
    if (!detail) {
      return { critical: [] as StudentRow[], regression: [] as StudentRow[], imbalanced: [] as StudentRow[] };
    }
    const payload = detail.payload || {};
    return {
      critical: toStudentRows(asList(payload, "critical"), "c"),
      regression: toStudentRows(asList(payload, "regression"), "r"),
      imbalanced: toStudentRows(asList(payload, "imbalanced"), "i")
    };
  })();

  const columns: ColumnsType<AnomalyAlertItem> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (v: string) =>
        v === "confirmed" ? <Tag color="green">已处理</Tag> : <Tag color="orange">未处理</Tag>
    },
    {
      title: "报告",
      dataIndex: "title",
      ellipsis: true
    },
    {
      title: "班级",
      dataIndex: "class_name",
      width: 120
    },
    {
      title: "考试",
      dataIndex: "exam_name",
      width: 160,
      ellipsis: true,
      render: (v: string, row) => v || row.exam_id
    },
    {
      title: "异常汇总",
      key: "counts",
      width: 220,
      render: (_, row) => {
        const c = row.counts || {
          critical: 0,
          regression: 0,
          imbalanced: 0
        };
        return (
          <Space size={4} wrap>
            <Tag color="orange">临界 {c.critical}</Tag>
            <Tag color="red">退步 {c.regression}</Tag>
            <Tag color="purple">偏科 {c.imbalanced}</Tag>
          </Space>
        );
      }
    },
    {
      title: "时间",
      dataIndex: "create_time",
      width: 170,
      render: (v?: string | null) => (v ? v.replace("T", " ").slice(0, 19) : "—")
    },
    {
      title: "操作",
      key: "actions",
      width: 90,
      render: (_, row) => (
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => void openDetail(row)}>
          详情
        </Button>
      )
    }
  ];

  if (!accessible) {
    return (
      <div className="h-full overflow-y-auto p-6">
        <Alert
          type="info"
          showIcon
          message="校内异常提醒"
          description={denyMessage || "教育局/学生账号不提供校内异常提醒，请由校长或班主任在本校视角查看。"}
        />
      </div>
    );
  }

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-4 p-6 pb-10">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <Typography.Title level={4} style={{ marginBottom: 4 }}>
              异常提醒
            </Typography.Title>
            <Typography.Text type="secondary">
              每场考试/班级一份报告；点开可查看临界生、退步、偏科明细。仅本校可见，确认后标记为已处理。
            </Typography.Text>
          </div>
          <Space>
            <Select
              style={{ width: 140 }}
              value={status}
              options={STATUS_OPTIONS}
              onChange={(v) => setStatus(v)}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>

        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={items}
          pagination={{ total, pageSize: 100, showTotal: (t) => `共 ${t} 条` }}
          size="middle"
        />
      </div>

      <Drawer
        title={detail?.title || "报告详情"}
        width={720}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        destroyOnClose
      >
        {detail ? (
          <div className="space-y-4">
            <div>
              {detail.status === "confirmed" ? (
                <Tag color="green">已处理</Tag>
              ) : (
                <Tag color="orange">未处理</Tag>
              )}
              <Tag>{detail.anomaly_type_label}</Tag>
            </div>
            <Typography.Paragraph style={{ marginBottom: 0 }}>
              <strong>班级：</strong>
              {detail.class_name || "—"}
              {" · "}
              <strong>考试：</strong>
              {detail.exam_name || detail.exam_id}
              {detail.subject_name ? (
                <>
                  {" · "}
                  <strong>科目：</strong>
                  {detail.subject_name}
                </>
              ) : null}
            </Typography.Paragraph>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              {detail.reason}
              {" · 来源 "}
              {detail.source}
            </Typography.Paragraph>

            <div>
              <Typography.Text strong>临界生（{detailTables.critical.length}）</Typography.Text>
              <Table
                className="mt-2"
                size="small"
                rowKey="key"
                pagination={false}
                columns={studentColumns}
                dataSource={detailTables.critical}
                locale={{ emptyText: "无" }}
              />
            </div>
            <div>
              <Typography.Text strong>大幅退步（{detailTables.regression.length}）</Typography.Text>
              <Table
                className="mt-2"
                size="small"
                rowKey="key"
                pagination={false}
                columns={studentColumns}
                dataSource={detailTables.regression}
                locale={{ emptyText: "无" }}
              />
            </div>
            <div>
              <Typography.Text strong>偏科（{detailTables.imbalanced.length}）</Typography.Text>
              <Table
                className="mt-2"
                size="small"
                rowKey="key"
                pagination={false}
                columns={studentColumns}
                dataSource={detailTables.imbalanced}
                locale={{ emptyText: "无" }}
              />
            </div>

            {detail.status === "confirmed" ? (
              <Alert
                type="success"
                showIcon
                message="已确认"
                description={
                  detail.confirm_note
                    ? `说明：${detail.confirm_note}`
                    : detail.confirmed_at
                      ? `确认时间：${detail.confirmed_at}`
                      : undefined
                }
              />
            ) : (
              <>
                <Input.TextArea
                  rows={3}
                  placeholder="可选：处理说明"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={512}
                />
                <Button
                  type="primary"
                  icon={<CheckOutlined />}
                  loading={confirming}
                  onClick={() => void onConfirm()}
                  block
                >
                  确认处理本报告
                </Button>
              </>
            )}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
