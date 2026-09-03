import { LineChartOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Empty,
  Form,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  educationApi,
  type ExamBatchOption,
  type FractionBarExam,
  type FractionBarLineCatalog
} from "@/api/education";

const PANEL_CARD =
  "overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]";

const TABLE_CLASS =
  "[&_.ant-table-thead>tr>th]:!font-semibold [&_.ant-table-cell]:!px-2";

const TRACK_THEME = {
  wl: {
    title: "物理类",
    headClass: "!bg-[#eff6ff] !text-[#1d4ed8] dark:!bg-[#1e3a5f] dark:!text-[#93c5fd]",
    cellClass: "!bg-[#f8fbff] dark:!bg-[#1e3a5f]/30",
    text: "#1d4ed8",
    panel: "border-[#bfdbfe] bg-[#eff6ff] dark:border-[#1e3a5f] dark:bg-[#1e3a5f]/40",
    pill: "bg-[#2563eb]"
  },
  ls: {
    title: "历史类",
    headClass: "!bg-[#fff7ed] !text-[#c2410c] dark:!bg-[#7c2d12] dark:!text-[#fdba74]",
    cellClass: "!bg-[#fffbeb] dark:!bg-[#7c2d12]/25",
    text: "#c2410c",
    panel: "border-[#fed7aa] bg-[#fff7ed] dark:border-[#7c2d12] dark:bg-[#7c2d12]/30",
    pill: "bg-[#ea580c]"
  }
} as const;

type LineFormValue = Record<string, number | null | undefined>;

function lineFieldKey(track: "wl" | "ls", code: string): string {
  return `${track}_${code}`;
}

function examLineMap(exam: FractionBarExam | null): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const line of exam?.lines || []) {
    const track = line.track.includes("历史") ? "ls" : "wl";
    out[lineFieldKey(track, line.line_code)] = line.threshold;
  }
  return out;
}

export default function FractionBarPage() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [denied, setDenied] = useState("");
  const [exams, setExams] = useState<FractionBarExam[]>([]);
  const [batches, setBatches] = useState<ExamBatchOption[]>([]);
  const [catalog, setCatalog] = useState<FractionBarLineCatalog[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<FractionBarExam | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setDenied("");
    try {
      const data = await educationApi.listFractionBar();
      setExams(data.exams || []);
      setBatches(data.batches || []);
      setCatalog(data.line_catalog || []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "加载分数线失败";
      if (msg.includes("无权") || msg.includes("学生")) {
        setDenied(msg);
        setExams([]);
      } else {
        message.error(msg);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const batchSelectOptions = useMemo(() => {
    const usedIds = new Set(
      exams
        .map((e) => e.exam_batch_id)
        .filter((id): id is number => typeof id === "number")
    );
    const usedNames = new Set(exams.map((e) => e.exam_name).filter(Boolean));
    const opts = batches
      .filter((b) => {
        if (editing != null) return true;
        return !usedIds.has(b.id) && !usedNames.has(b.batch_name);
      })
      .map((b) => ({ value: b.id, label: b.batch_name }));
    if (
      editing &&
      !opts.some((o) => o.value === editing.exam_batch_id || o.label === editing.exam_name)
    ) {
      opts.unshift({
        value: editing.exam_batch_id ?? 0,
        label: editing.exam_name
      });
    }
    return opts;
  }, [batches, exams, editing]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (row: FractionBarExam) => {
    setEditing(row);
    const matched = batches.find(
      (b) =>
        b.id === row.exam_batch_id ||
        (row.exam_batch_id == null && b.batch_name === row.exam_name)
    );
    form.setFieldsValue({
      exam_batch_id: matched?.id ?? row.exam_batch_id ?? 0,
      ...examLineMap(row)
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    const values = (await form.validateFields()) as LineFormValue & { exam_batch_id?: number };
    const examBatchId =
      typeof values.exam_batch_id === "number" && values.exam_batch_id > 0
        ? values.exam_batch_id
        : undefined;
    const batch = batches.find((b) => b.id === examBatchId);
    const examName = batch?.batch_name || editing?.exam_name || "";
    if (!examBatchId && !examName) {
      message.error("请选择考试");
      return;
    }
    const lines: Array<{ track: string; line_code: string; threshold: number | null }> = [];
    for (const item of catalog) {
      if (item.wl_column) {
        const raw = values[lineFieldKey("wl", item.line_code)];
        lines.push({
          track: "物理类",
          line_code: item.line_code,
          threshold: raw == null || raw === ("" as never) ? null : Number(raw)
        });
      }
      if (item.ls_column) {
        const raw = values[lineFieldKey("ls", item.line_code)];
        lines.push({
          track: "历史类",
          line_code: item.line_code,
          threshold: raw == null || raw === ("" as never) ? null : Number(raw)
        });
      }
    }
    setSaving(true);
    try {
      const res = await educationApi.upsertFractionBar({
        exam_batch_id: examBatchId ?? undefined,
        exam_name: examName,
        lines
      });
      message.success(res.message || `已保存，写入 ${res.indicator_rows} 条达线指标`);
      setModalOpen(false);
      await loadData();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleRecompute = async (examName?: string) => {
    setLoading(true);
    try {
      const res = await educationApi.recomputeScoreIndicator(examName);
      message.success(`已重算 ${res.indicator_rows} 条达线指标`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "重算失败");
    } finally {
      setLoading(false);
    }
  };

  const lineValue = (row: FractionBarExam, track: "wl" | "ls", code: string): number | null => {
    const label = track === "wl" ? "物理" : "历史";
    const hit = row.lines.find((x) => x.line_code === code && x.track.includes(label));
    return hit?.threshold ?? null;
  };

  const lineGroup = (track: "wl" | "ls"): ColumnsType<FractionBarExam>[number] | null => {
    const items = catalog.filter((x) => (track === "wl" ? x.wl_column : x.ls_column));
    if (!items.length) return null;
    const theme = TRACK_THEME[track];
    const headerCell = () => ({ className: theme.headClass });
    const bodyCell = () => ({ className: theme.cellClass });
    return {
      title: (
        <span className="inline-flex items-center gap-1.5 text-[13px] font-semibold" style={{ color: theme.text }}>
          <span className={`h-2 w-2 rounded-full ${theme.pill}`} />
          {theme.title}
        </span>
      ),
      onHeaderCell: headerCell,
      children: items.map((item) => ({
        title: <span className="text-[12px] font-medium" style={{ color: theme.text }}>{item.line_name}</span>,
        key: lineFieldKey(track, item.line_code),
        width: 88,
        align: "right" as const,
        onHeaderCell: headerCell,
        onCell: bodyCell,
        render: (_: unknown, row: FractionBarExam) => {
          const v = lineValue(row, track, item.line_code);
          if (v == null) {
            return <span className="text-[#94a3b8]">—</span>;
          }
          return (
            <span className="tabular-nums font-medium" style={{ color: theme.text }}>
              {v}
            </span>
          );
        }
      }))
    };
  };

  const columns: ColumnsType<FractionBarExam> = [
    {
      title: "考试",
      dataIndex: "exam_name",
      key: "exam_name",
      fixed: "left",
      width: 200,
      render: (name: string) => (
        <span className="font-medium text-[#0f172a] dark:text-[#f1f5f9]">{name}</span>
      )
    },
    ...([lineGroup("wl"), lineGroup("ls")].filter(Boolean) as ColumnsType<FractionBarExam>),
    {
      title: "操作",
      key: "actions",
      width: 148,
      fixed: "right",
      render: (_, row) => (
        <Space size={4}>
          <Button type="link" size="small" className="!px-1" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            className="!px-1"
            onClick={() => void handleRecompute(row.exam_name)}
          >
            重算
          </Button>
        </Space>
      )
    }
  ];

  const renderTrackFields = (track: "wl" | "ls") => {
    const items = catalog.filter((x) => (track === "wl" ? x.wl_column : x.ls_column));
    if (!items.length) return null;
    const theme = TRACK_THEME[track];
    return (
      <div className={`mb-3 rounded-xl border p-3 ${theme.panel}`}>
        <div className="mb-3 inline-flex items-center gap-1.5 text-[13px] font-semibold" style={{ color: theme.text }}>
          <span className={`h-2 w-2 rounded-full ${theme.pill}`} />
          {theme.title}
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          {items.map((item) => (
            <Form.Item
              key={lineFieldKey(track, item.line_code)}
              name={lineFieldKey(track, item.line_code)}
              label={<span className="text-[12px]" style={{ color: theme.text }}>{item.line_name}</span>}
              className="mb-2"
            >
              <InputNumber className="w-full" min={0} max={900} precision={1} placeholder="分数线" />
            </Form.Item>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eff6ff] text-[16px] text-[#2563eb] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
            <LineChartOutlined />
          </span>
          <div className="min-w-0">
            <Typography.Title level={4} className="!mb-1">
              预测分数线
            </Typography.Title>
            <Typography.Text className="oc-muted">
              每场考试一行分数线；保存后按该场成绩自动写入达线指标表，供问数查询
            </Typography.Text>
          </div>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => void loadData()} loading={loading}>
            刷新
          </Button>
          <Button onClick={() => void handleRecompute()} loading={loading} disabled={!exams.length}>
            全部重算指标
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增考试
          </Button>
        </Space>
      </div>

      {denied ? (
        <Alert type="warning" showIcon message={denied} />
      ) : (
        <div className={PANEL_CARD}>
          {exams.length === 0 && !loading ? (
            <div className="py-16">
              <Empty description="暂无预测分数线，点击右上角新增考试" />
            </div>
          ) : (
            <Table<FractionBarExam>
              rowKey={(row) => String(row.exam_batch_id ?? row.exam_name)}
              loading={loading}
              columns={columns}
              dataSource={exams}
              pagination={false}
              scroll={{ x: 1600 }}
              size="small"
              className={TABLE_CLASS}
            />
          )}
        </div>
      )}

      <Modal
        title={editing ? `编辑 ${editing.exam_name}` : "新增考试分数线"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void handleSave()}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        width={720}
        destroyOnClose
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="exam_batch_id"
            label="考试名称"
            rules={[{ required: true, message: "请选择考试" }]}
            extra={!editing && !batches.length ? "暂无考试批次可选择" : undefined}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="请选择考试批次"
              disabled={Boolean(editing)}
              options={batchSelectOptions}
            />
          </Form.Item>
          {renderTrackFields("wl")}
          {renderTrackFields("ls")}
        </Form>
      </Modal>
    </div>
  );
}
