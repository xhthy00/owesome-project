import { CheckOutlined, DownOutlined, FileTextOutlined, PlusOutlined, WarningOutlined } from "@ant-design/icons";
import { Button, Drawer, Dropdown, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, type CurrentUser } from "@/api/auth";
import { canImportExamPapers, examLibraryApi, type ExamBatchItem, type ExamLibraryItem, type ExamPayload } from "@/api/examLibrary";

const SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"];
const SUBJECT_SCORE: Record<string, number> = {
  语文: 150,
  数学: 150,
  英语: 150,
  物理: 100,
  化学: 100,
  生物: 100,
  政治: 100,
  历史: 100,
  地理: 100
};
const EXAM_TYPES = [
  { value: "city", label: "全市统考" },
  { value: "school", label: "学校自建" },
  { value: "teacher", label: "教师自建" }
];

const SUBJECT_COLOR: Record<string, string> = {
  语文: "magenta",
  数学: "blue",
  英语: "cyan",
  物理: "geekblue",
  化学: "orange",
  生物: "green",
  政治: "purple",
  历史: "gold",
  地理: "lime"
};

function scoresDiffer(row: ExamLibraryItem): boolean {
  if (row.exam_score == null || row.question_score_sum == null) return false;
  return Math.round(row.question_score_sum * 100) !== Math.round(row.exam_score * 100);
}

function examAnomaly(row: ExamLibraryItem): string | null {
  if (row.question_count === 0) return "未关联题目";
  if (scoresDiffer(row)) return "卷面总分异常";
  if ((row.score_count ?? 0) === 0) return "未关联成绩";
  return null;
}

function StatusMark({ title, ok }: { title: string; ok?: boolean }) {
  return (
    <Tooltip title={title} getPopupContainer={() => document.body}>
      <span
        aria-label={title}
        className={
          ok
            ? "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#e9f8ef] text-[11px] text-[#14914a] ring-1 ring-[#b7e4c7]"
            : "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#fff6e8] text-[11px] text-[#d48806] ring-1 ring-[#ffe1b3]"
        }
      >
        {ok ? <CheckOutlined /> : <WarningOutlined />}
      </span>
    </Tooltip>
  );
}

export default function ExamLibraryPage() {
  const router = useRouter();
  const [form] = Form.useForm<ExamPayload>();
  const [keyword, setKeyword] = useState("");
  const [examType, setExamType] = useState("");
  const [subjectFilter, setSubjectFilter] = useState("");
  const [batchFilter, setBatchFilter] = useState<number | undefined>();
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<ExamLibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<ExamLibraryItem | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [batches, setBatches] = useState<ExamBatchItem[]>([]);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchFormOpen, setBatchFormOpen] = useState(false);
  const [batchEditing, setBatchEditing] = useState<ExamBatchItem | null>(null);
  const [batchSaving, setBatchSaving] = useState(false);
  const [batchForm] = Form.useForm<{ batch_name: string; exam_time: string }>();
  const examTypeValue = Form.useWatch("exam_type", form);
  const canImport = canImportExamPapers(currentUser);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await examLibraryApi.list({
        keyword,
        examType,
        subject: subjectFilter,
        examBatchId: batchFilter,
        page,
        pageSize: 20
      });
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载试卷库失败");
    } finally {
      setLoading(false);
    }
  }, [keyword, examType, subjectFilter, batchFilter, page]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
    void examLibraryApi
      .listBatches()
      .then(setBatches)
      .catch((error) => message.error(error instanceof Error ? error.message : "加载考试批次失败"));
  }, []);

  const reloadBatches = async () => {
    const data = await examLibraryApi.listBatches();
    setBatches(data);
  };

  const openBatchCreate = () => {
    setBatchEditing(null);
    batchForm.setFieldsValue({ batch_name: "", exam_time: "" });
    setBatchFormOpen(true);
  };

  const openBatchEdit = (row: ExamBatchItem) => {
    setBatchEditing(row);
    batchForm.setFieldsValue({ batch_name: row.batch_name, exam_time: row.exam_time || "" });
    setBatchFormOpen(true);
  };

  const submitBatch = async () => {
    const values = await batchForm.validateFields();
    const payload = { batch_name: values.batch_name.trim(), exam_time: values.exam_time };
    setBatchSaving(true);
    try {
      if (batchEditing) {
        await examLibraryApi.updateBatch(batchEditing.id, payload);
        message.success("批次已更新");
      } else {
        const created = await examLibraryApi.createBatch(payload);
        message.success("批次已创建");
        if (open) form.setFieldValue("exam_batch_id", created.id);
      }
      setBatchFormOpen(false);
      await reloadBatches();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "保存批次失败");
    } finally {
      setBatchSaving(false);
    }
  };

  const removeBatch = (row: ExamBatchItem) => {
    Modal.confirm({
      zIndex: 1300,
      title: `删除批次「${row.batch_name}」？`,
      content: "已关联试卷、成绩宽表或分数线的批次不能删除。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await examLibraryApi.removeBatch(row.id);
        if (form.getFieldValue("exam_batch_id") === row.id) {
          form.setFieldValue("exam_batch_id", null);
        }
        message.success("批次已删除");
        await reloadBatches();
      }
    });
  };

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      exam_type: "city",
      subject: "语文",
      exam_score: 150,
      exam_time: "",
      school_id: "",
      owner_id: "",
      exam_batch_id: null
    });
    setOpen(true);
  };

  const openEdit = (row: ExamLibraryItem) => {
    setEditing(row);
    form.setFieldsValue({
      exam_type: row.exam_type,
      subject: row.subject,
      exam_score: row.exam_score ?? SUBJECT_SCORE[row.subject] ?? 100,
      exam_time: row.exam_time || "",
      school_id: row.school_id || "",
      owner_id: row.owner_id || "",
      exam_batch_id: row.exam_batch_id
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const batch = batches.find((item) => item.id === values.exam_batch_id);
    const payload: ExamPayload = {
      ...values,
      exam_name: batch?.batch_name || "",
      exam_time: values.exam_time || null,
      school_id: values.school_id || null,
      owner_id: values.owner_id || null,
      exam_batch_id: values.exam_batch_id || null
    };
    setSaving(true);
    try {
      if (editing) {
        await examLibraryApi.update(editing.id, payload);
        message.success("试卷已更新");
      } else {
        await examLibraryApi.create(payload);
        message.success("试卷已创建");
      }
      setOpen(false);
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const remove = (row: ExamLibraryItem) => {
    Modal.confirm({
      title: `删除「${row.exam_name}」？`,
      content: "将同时删除题目和知识点关联。已有成绩的试卷不能删除。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await examLibraryApi.remove(row.id);
        message.success("试卷已删除");
        await load();
      }
    });
  };

  const columns: ColumnsType<ExamLibraryItem> = [
    {
      title: "考试批次",
      dataIndex: "exam_batch_name",
      width: "24%",
      ellipsis: true,
      render: (value: string | null, row) => {
        const anomaly = examAnomaly(row);
        return (
          <span className="inline-flex min-w-0 max-w-full items-center gap-2">
            <StatusMark title={anomaly || "正常"} ok={!anomaly} />
            <span className="truncate font-medium text-[#1f2937] dark:text-[#e5e7eb]">
              {value || row.exam_name || "-"}
            </span>
          </span>
        );
      }
    },
    {
      title: "科目",
      dataIndex: "subject",
      width: "10%",
      align: "center",
      render: (value: string) => (
        <Tag color={SUBJECT_COLOR[value] || "default"} className="!mr-0">
          {value}
        </Tag>
      )
    },
    {
      title: "类型",
      width: "13%",
      render: (_, row) => (
        <div className="min-w-0">
          <div>{row.exam_type_label}</div>
          {row.exam_type !== "city" && (row.school_id || row.owner_id) ? (
            <div className="truncate text-xs text-slate-400">
              {[row.school_id, row.owner_id].filter(Boolean).join(" · ")}
            </div>
          ) : null}
        </div>
      )
    },
    { title: "考试时间", dataIndex: "exam_time", width: "14%", render: (value) => value || "-" },
    { title: "满分", dataIndex: "exam_score", width: "8%", align: "center" },
    { title: "题目", dataIndex: "question_count", width: "9%", align: "center" },
    {
      title: "操作",
      width: "22%",
      render: (_, row) => {
        const params = new URLSearchParams();
        if (row.exam_batch_id) params.set("batchId", String(row.exam_batch_id));
        if (row.subject) params.set("subject", row.subject);
        const query = params.toString();
        return (
          <Space size={0} className="whitespace-nowrap">
            <Button type="link" size="small" onClick={() => router.push(`/construct/education/exam-library/${row.id}`)}>
              详情
            </Button>
            <Button type="link" size="small" onClick={() => openEdit(row)}>
              编辑
            </Button>
            {canImport ? (
              <Button
                type="link"
                size="small"
                onClick={() => router.push(`/construct/education/exam-library/import${query ? `?${query}` : ""}`)}
              >
                导入
              </Button>
            ) : null}
            <Button
              type="link"
              size="small"
              onClick={() => router.push(`/construct/education/exam-library/${row.id}?check=1`)}
            >
              知识点核对
            </Button>
            <Dropdown
              trigger={["click"]}
              getPopupContainer={() => document.body}
              menu={{
                items: [
                  { key: "report", label: "核对报告" },
                  { key: "delete", label: "删除", danger: true }
                ],
                onClick: ({ key }) => {
                  if (key === "report") {
                    void router.push(`/construct/education/exam-library/${row.id}?report=1`);
                    return;
                  }
                  if (key === "delete") remove(row);
                }
              }}
            >
              <Button type="link" size="small">
                更多 <DownOutlined />
              </Button>
            </Dropdown>
          </Space>
        );
      }
    }
  ];

    return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto bg-[#f5f7fb] p-6 pb-10 dark:bg-transparent">
      <div className="w-full">
        <div className="mb-4 overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#eef2f6] bg-[linear-gradient(180deg,#f3f8ff_0%,#ffffff_72%)] px-5 py-4 dark:border-[#334155] dark:bg-none">
            <div className="flex min-w-0 items-center gap-3">
              <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#e8f3ff] text-[18px] text-[#0C75FC]">
                <FileTextOutlined />
              </span>
              <div className="min-w-0">
                <Typography.Title level={4} className="!mb-0">
                  试卷库
                </Typography.Title>
                <Typography.Text type="secondary" className="text-[13px]">
                  批次前的标记按优先级提示未关联题目、卷面总分异常、未关联成绩；齐全时为绿色勾。可见范围：全市统考、本校学校自建、本人教师自建。
                </Typography.Text>
              </div>
            </div>
            <Space>
              <Button onClick={() => setBatchOpen(true)}>批次管理</Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新增试卷
              </Button>
            </Space>
          </div>
          <div className="px-5 py-4">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <Input.Search
                allowClear
                className="w-56"
                placeholder="搜索考试批次"
                onSearch={(value) => {
                  setPage(1);
                  setKeyword(value.trim());
                }}
              />
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                className="w-64"
                placeholder="考试批次"
                value={batchFilter}
                options={batches.map((item) => ({ value: item.id, label: item.batch_name }))}
                onChange={(value) => {
                  setPage(1);
                  setBatchFilter(value);
                }}
              />
              <Select
                allowClear
                className="w-28"
                placeholder="学科"
                value={subjectFilter || undefined}
                options={SUBJECTS.map((item) => ({ value: item, label: item }))}
                onChange={(value) => {
                  setPage(1);
                  setSubjectFilter(value || "");
                }}
              />
              <Select
                allowClear
                className="w-36"
                placeholder="试卷类型"
                value={examType || undefined}
                options={EXAM_TYPES}
                onChange={(value) => {
                  setPage(1);
                  setExamType(value || "");
                }}
              />
            </div>
            <Table
              rowKey="id"
              size="middle"
              loading={loading}
              columns={columns}
              dataSource={rows}
              tableLayout="fixed"
              className="[&_.ant-table]:!w-full [&_.ant-table-thead>tr>th]:!bg-[#f7f9fc] [&_.ant-table-thead>tr>th]:!font-medium dark:[&_.ant-table-thead>tr>th]:!bg-[#1c2430]"
              pagination={{
                current: page,
                pageSize: 20,
                total,
                showSizeChanger: false,
                showTotal: (count) => `共 ${count} 条`,
                onChange: (next) => setPage(next)
              }}
            />
          </div>
        </div>
      </div>
      <Modal
        title={editing ? "编辑试卷" : "新增试卷"}
        open={open}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        onOk={() => void submit()}
        onCancel={() => setOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="exam_type" label="试卷类型" rules={[{ required: true }]}>
            <Select options={EXAM_TYPES} />
          </Form.Item>
          <Form.Item
            name="exam_batch_id"
            label="考试批次"
            extra={
              <span>
                试卷名称使用考试批次名称。成绩导入按批次和科目找到这张试卷。同一批次下同一科目只能有一张。
                <Button type="link" size="small" className="!px-1" onClick={() => setBatchOpen(true)}>
                  管理批次
                </Button>
              </span>
            }
            rules={[{ required: true, message: "请关联考试批次" }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择考试批次"
              options={batches.map((item) => ({
                value: item.id,
                label: item.batch_name
              }))}
              onChange={(batchId) => {
                const batch = batches.find((item) => item.id === batchId);
                if (!batch) return;
                if (!String(form.getFieldValue("exam_time") || "").trim() && batch.exam_time) {
                  form.setFieldValue("exam_time", String(batch.exam_time).slice(0, 10));
                }
              }}
            />
          </Form.Item>
          {examTypeValue !== "city" ? (
            <Form.Item name="school_id" label="学校" extra="留空时使用当前账号所属学校">
              <Input />
            </Form.Item>
          ) : null}
          {examTypeValue === "teacher" ? (
            <Form.Item name="owner_id" label="教师账号" extra="留空时归属于当前登录账号">
              <Input />
            </Form.Item>
          ) : null}
          <Form.Item name="subject" label="科目" rules={[{ required: true, message: "请选择科目" }]}>
            <Select
              options={SUBJECTS.map((item) => ({ value: item, label: item }))}
              onChange={(value) => form.setFieldValue("exam_score", SUBJECT_SCORE[value] ?? 100)}
            />
          </Form.Item>
          <Form.Item name="exam_score" label="卷面满分" rules={[{ required: true, message: "请填写满分" }]}>
            <InputNumber className="w-full" min={0.5} step={0.5} />
          </Form.Item>
          <Form.Item name="exam_time" label="考试时间">
            <Input placeholder="YYYY-MM-DD" />
          </Form.Item>
        </Form>
      </Modal>
      <Drawer
        title="考试批次"
        width={640}
        zIndex={1100}
        open={batchOpen}
        onClose={() => setBatchOpen(false)}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openBatchCreate}>
            新建批次
          </Button>
        }
      >
        <Table<ExamBatchItem>
          rowKey="id"
          size="middle"
          dataSource={batches}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          columns={[
            { title: "批次名称", dataIndex: "batch_name", ellipsis: true },
            { title: "考试时间", dataIndex: "exam_time", width: 120, render: (value) => value || "-" },
            { title: "试卷", dataIndex: "exam_count", width: 70 },
            {
              title: "操作",
              width: 120,
              render: (_, row) => (
                <Space size={0}>
                  <Button type="link" size="small" onClick={() => openBatchEdit(row)}>
                    编辑
                  </Button>
                  <Button type="link" size="small" danger onClick={() => removeBatch(row)}>
                    删除
                  </Button>
                </Space>
              )
            }
          ]}
        />
      </Drawer>
      <Modal
        title={batchEditing ? "编辑批次" : "新建批次"}
        zIndex={1200}
        open={batchFormOpen}
        confirmLoading={batchSaving}
        okText="保存"
        cancelText="取消"
        onOk={() => void submitBatch()}
        onCancel={() => setBatchFormOpen(false)}
        destroyOnClose
      >
        <Form form={batchForm} layout="vertical" className="mt-4">
          <Form.Item name="batch_name" label="批次名称" rules={[{ required: true, message: "请填写批次名称" }]}>
            <Input maxLength={255} placeholder="如 2027届高二6月期末" />
          </Form.Item>
          <Form.Item
            name="exam_time"
            label="考试时间"
            extra="修改名称会同步仍使用原批次名称的试卷、成绩宽表和分数线。"
            rules={[{ required: true, message: "请填写考试时间" }]}
          >
            <Input placeholder="YYYY-MM-DD" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
