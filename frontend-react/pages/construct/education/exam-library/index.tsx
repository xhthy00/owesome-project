import { DownOutlined, FileTextOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Dropdown, Form, Input, InputNumber, Modal, Select, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, type CurrentUser } from "@/api/auth";
import { canImportExamPapers, examLibraryApi, type ExamLibraryItem, type ExamPayload } from "@/api/examLibrary";

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

const PANEL =
  "rounded-2xl border border-[#e5e7eb] bg-white p-4 shadow-sm dark:border-[#334155] dark:bg-[#141923]";

export default function ExamLibraryPage() {
  const router = useRouter();
  const [form] = Form.useForm<ExamPayload>();
  const [keyword, setKeyword] = useState("");
  const [examType, setExamType] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<ExamLibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<ExamLibraryItem | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const examTypeValue = Form.useWatch("exam_type", form);
  const canImport = canImportExamPapers(currentUser);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await examLibraryApi.list({ keyword, examType, page, pageSize: 20 });
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载试卷库失败");
    } finally {
      setLoading(false);
    }
  }, [keyword, examType, page]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, []);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      exam_name: "",
      exam_type: "teacher",
      subject: "语文",
      exam_score: 150,
      exam_time: "",
      school_id: "",
      owner_id: ""
    });
    setOpen(true);
  };

  const openEdit = (row: ExamLibraryItem) => {
    setEditing(row);
    form.setFieldsValue({
      exam_name: row.exam_name,
      exam_type: row.exam_type,
      subject: row.subject,
      exam_score: row.exam_score ?? SUBJECT_SCORE[row.subject] ?? 100,
      exam_time: row.exam_time || "",
      school_id: row.school_id || "",
      owner_id: row.owner_id || ""
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const payload: ExamPayload = {
      ...values,
      exam_time: values.exam_time || null,
      school_id: values.school_id || null,
      owner_id: values.owner_id || null
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
    { title: "试卷名称", dataIndex: "exam_name", ellipsis: true },
    { title: "科目", dataIndex: "subject", width: 80 },
    { title: "类型", dataIndex: "exam_type_label", width: 110 },
    { title: "考试时间", dataIndex: "exam_time", width: 120, render: (value) => value || "-" },
    { title: "满分", dataIndex: "exam_score", width: 80 },
    { title: "题目", dataIndex: "question_count", width: 70 },
    {
      title: "范围",
      width: 160,
      render: (_, row) => row.school_id || row.owner_id || "全市"
    },
    {
      title: "操作",
      width: 300,
      render: (_, row) => {
        const params = new URLSearchParams();
        if (row.exam_batch_id) params.set("batchId", String(row.exam_batch_id));
        if (row.subject) params.set("subject", row.subject);
        const query = params.toString();
        return (
          <Space size={0}>
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
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          <FileTextOutlined />
          <Typography.Title level={4} className="!mb-0">
            试卷库
          </Typography.Title>
        </div>
        <div className={PANEL}>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Input.Search
              allowClear
              className="w-64"
              placeholder="按试卷名称搜索"
              onSearch={(value) => {
                setPage(1);
                setKeyword(value.trim());
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
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增试卷
            </Button>
          </div>
          <Typography.Paragraph type="secondary" className="!mb-3">
            可见范围：全市统考、本校学校自建、本人教师自建。
          </Typography.Paragraph>
          <Table
            rowKey="id"
            size="middle"
            loading={loading}
            columns={columns}
            dataSource={rows}
            scroll={{ x: 1080 }}
            pagination={{
              current: page,
              pageSize: 20,
              total,
              showSizeChanger: false,
              onChange: (next) => setPage(next)
            }}
          />
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
          <Form.Item name="exam_name" label="试卷名称" rules={[{ required: true, message: "请填写试卷名称" }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="exam_type" label="试卷类型" rules={[{ required: true }]}>
            <Select options={EXAM_TYPES} />
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
    </div>
  );
}
