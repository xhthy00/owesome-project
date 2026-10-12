import {
  DownloadOutlined,
  PlusOutlined,
  ReadOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined
} from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
  Upload,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  knowledgeApi,
  type KnowledgeItem,
  type KnowledgePayload,
  type KnowledgeVersion
} from "@/api/knowledge";

const LEVELS = ["stage", "grade", "subject", "module", "chapter", "secondary"] as const;
type Level = (typeof LEVELS)[number];

const LEVEL_LABEL: Record<Level, string> = {
  stage: "学段",
  grade: "年级",
  subject: "学科",
  module: "大概念",
  chapter: "重要概念",
  secondary: "次位概念"
};

const CHANGE_LABEL: Record<string, string> = {
  create: "新建",
  update: "修改",
  import: "导入",
  delete: "删除",
  migrate: "迁移"
};

const CHANGE_COLOR: Record<string, string> = {
  create: "green",
  migrate: "green",
  update: "orange",
  import: "orange",
  delete: "red"
};

type Filters = Record<Level | "keyword", string>;

const EMPTY_FILTERS: Filters = {
  stage: "",
  grade: "",
  subject: "",
  module: "",
  chapter: "",
  secondary: "",
  keyword: ""
};

const EMPTY_FORM: KnowledgePayload = {
  stage: "",
  grade: "",
  subject: "",
  subject_no: "",
  module: "",
  module_no: "",
  chapter: "",
  chapter_no: "",
  secondary: "",
  secondary_no: "",
  knowledge_name: "",
  knowledge_no: ""
};

const PANEL_CARD =
  "overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]";

const TABLE_CLASS =
  "[&_.ant-table]:!text-[13px] [&_.ant-table-thead>tr>th]:!bg-[#f8fafc] [&_.ant-table-thead>tr>th]:!font-semibold [&_.ant-table-thead>tr>th]:!text-[#334155] dark:[&_.ant-table-thead>tr>th]:!bg-[#1b2433] dark:[&_.ant-table-thead>tr>th]:!text-[#cbd5e1]";

function parentsBefore(level: Level, filters: Filters): Record<string, string> {
  const parents: Record<string, string> = {};
  for (const name of LEVELS) {
    if (name === level) break;
    parents[name] = filters[name];
  }
  return parents;
}

export default function ConstructKnowledgePage() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [options, setOptions] = useState<Record<Level, string[]>>({
    stage: [],
    grade: [],
    subject: [],
    module: [],
    chapter: [],
    secondary: []
  });
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<KnowledgePayload>();
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versions, setVersions] = useState<KnowledgeVersion[]>([]);
  const [versionTitle, setVersionTitle] = useState("");

  const loadList = useCallback(async (query: Filters, nextPage: number, nextSize: number) => {
    setLoading(true);
    try {
      const data = await knowledgeApi.list({
        page: nextPage,
        pageSize: nextSize,
        ...query
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(data.page || nextPage);
      setPageSize(data.page_size || nextSize);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载知识点失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLevel = useCallback(async (level: Level, source: Filters) => {
    try {
      const list = await knowledgeApi.cascade(level, parentsBefore(level, source));
      setOptions((prev) => ({ ...prev, [level]: list }));
    } catch {
      setOptions((prev) => ({ ...prev, [level]: [] }));
    }
  }, []);

  useEffect(() => {
    void loadLevel("stage", EMPTY_FILTERS);
    void loadList(EMPTY_FILTERS, 1, 20);
  }, [loadLevel, loadList]);

  const onFilterChange = (level: Level, value: string) => {
    const next = { ...filters, [level]: value || "" };
    const index = LEVELS.indexOf(level);
    LEVELS.slice(index + 1).forEach((name) => {
      next[name] = "";
    });
    setFilters(next);
    setOptions((prev) => {
      const cleared = { ...prev };
      LEVELS.slice(index + 1).forEach((name) => {
        cleared[name] = [];
      });
      return cleared;
    });
    const child = LEVELS[index + 1];
    if (value && child) void loadLevel(child, next);
  };

  const search = () => {
    setApplied(filters);
    void loadList(filters, 1, pageSize);
  };

  const reset = () => {
    setFilters(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setOptions({ stage: [], grade: [], subject: [], module: [], chapter: [], secondary: [] });
    void loadLevel("stage", EMPTY_FILTERS);
    void loadList(EMPTY_FILTERS, 1, pageSize);
  };

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (row: KnowledgeItem) => {
    setEditing(row);
    form.setFieldsValue({
      stage: row.stage,
      grade: row.grade,
      subject: row.subject,
      subject_no: row.subject_no,
      module: row.module,
      module_no: row.module_no,
      chapter: row.chapter,
      chapter_no: row.chapter_no,
      secondary: row.secondary || "",
      secondary_no: row.secondary_no || "",
      knowledge_name: row.knowledge_name,
      knowledge_no: row.knowledge_no
    });
    setFormOpen(true);
  };

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editing) {
        await knowledgeApi.update(editing.id, values);
      } else {
        await knowledgeApi.create(values);
      }
      message.success("保存成功");
      setFormOpen(false);
      void loadLevel("stage", EMPTY_FILTERS);
      void loadList(applied, page, pageSize);
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const openVersions = async (row: KnowledgeItem) => {
    setVersionTitle(row.knowledge_name);
    setVersionsOpen(true);
    setVersionsLoading(true);
    try {
      setVersions(await knowledgeApi.versions(row.id));
    } catch (err) {
      setVersions([]);
      message.error(err instanceof Error ? err.message : "加载历史版本失败");
    } finally {
      setVersionsLoading(false);
    }
  };

  const remove = (row: KnowledgeItem) => {
    Modal.confirm({
      title: "删除知识点",
      content: `确定删除知识点 ${row.knowledge_name}？`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await knowledgeApi.remove(row.id);
        } catch (err) {
          message.error(err instanceof Error ? err.message : "删除失败");
          throw err;
        }
        message.success("删除成功");
        void loadList(applied, page, pageSize);
      }
    });
  };

  const onImport = async (file: File) => {
    try {
      const result = await knowledgeApi.importFile(file);
      const failed = result.errors?.length || 0;
      message.success(`新增 ${result.inserted} 条，覆盖 ${result.updated} 条`);
      if (failed) {
        Modal.warning({
          title: "导入明细",
          width: 560,
          content: (
            <div className="max-h-80 overflow-auto whitespace-pre-wrap">
              {result.errors
                .slice(0, 15)
                .map((item) => `第 ${item.row} 行：${item.message}`)
                .join("\n")}
              {failed > 15 ? `\n…共 ${failed} 条` : ""}
            </div>
          )
        });
      }
      void loadLevel("stage", filters);
      void loadList(applied, 1, pageSize);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "导入失败");
    }
    return false;
  };

  const columns: ColumnsType<KnowledgeItem> = [
    { title: "编号", dataIndex: "knowledge_no", width: 118, ellipsis: true },
    { title: "学段", dataIndex: "stage", width: 72 },
    { title: "年级", dataIndex: "grade", width: 72 },
    { title: "学科", dataIndex: "subject", width: 72 },
    { title: "大概念", dataIndex: "module", ellipsis: true },
    { title: "重要概念", dataIndex: "chapter", ellipsis: true },
    { title: "次位概念", dataIndex: "secondary", ellipsis: true },
    { title: "知识点名称", dataIndex: "knowledge_name", ellipsis: true },
    {
      title: "操作",
      key: "actions",
      width: 176,
      align: "right",
      onCell: () => ({ style: { whiteSpace: "nowrap", paddingInline: 8 } }),
      onHeaderCell: () => ({ style: { paddingInline: 8 } }),
      render: (_, row) => (
        <div className="inline-flex flex-nowrap items-center">
          <Button type="link" size="small" className="!px-1" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Button type="link" size="small" className="!px-1" onClick={() => void openVersions(row)}>
            历史版本
          </Button>
          <Button type="link" size="small" danger className="!px-1" onClick={() => remove(row)}>
            删除
          </Button>
        </div>
      )
    }
  ];

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eff6ff] text-[16px] text-[#2563eb] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
            <ReadOutlined />
          </span>
          <Typography.Title level={4} className="!mb-0">
            知识点
          </Typography.Title>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => void loadList(applied, page, pageSize)} loading={loading}>
            刷新
          </Button>
          <Button
            icon={<DownloadOutlined />}
            onClick={() =>
              void knowledgeApi.downloadTemplate().catch((err: unknown) =>
                message.error(err instanceof Error ? err.message : "下载模板失败")
              )
            }
          >
            下载模板
          </Button>
          <Upload accept=".xlsx" showUploadList={false} beforeUpload={onImport}>
            <Button icon={<UploadOutlined />}>导入</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增
          </Button>
        </Space>
      </div>

      <div className={`${PANEL_CARD} mb-4 px-4 py-3`}>
        <div className="flex flex-wrap items-center gap-2">
          {LEVELS.map((level, index) => (
            <Select
              key={level}
              allowClear
              showSearch
              placeholder={LEVEL_LABEL[level]}
              style={{ width: level === "module" || level === "chapter" || level === "secondary" ? 168 : 108 }}
              value={filters[level] || undefined}
              disabled={index > 0 && !filters[LEVELS[index - 1]]}
              options={options[level].map((item) => ({ label: item, value: item }))}
              onChange={(value) => onFilterChange(level, value || "")}
            />
          ))}
          <Input
            allowClear
            placeholder="搜索知识点名称"
            className="w-[200px]"
            value={filters.keyword}
            onChange={(event) => setFilters((prev) => ({ ...prev, keyword: event.target.value }))}
            onPressEnter={search}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={search}>
            查询
          </Button>
          <Button onClick={reset}>重置</Button>
        </div>
      </div>

      <div className={PANEL_CARD}>
        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={items}
          tableLayout="fixed"
          size="middle"
          className={TABLE_CLASS}
          locale={{ emptyText: "暂无知识点" }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (count) => `共 ${count} 条`,
            onChange: (nextPage, nextSize) => {
              void loadList(applied, nextPage, nextSize);
            }
          }}
        />
      </div>

      <Modal
        title={editing ? "编辑知识点" : "新增知识点"}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={() => void submit()}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={720}
        forceRender
      >
        <Form form={form} layout="vertical" className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
          <Form.Item name="stage" label="学段" rules={[{ required: true, message: "请填写学段" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="grade" label="年级" rules={[{ required: true, message: "请填写年级" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="subject" label="学科" rules={[{ required: true, message: "请填写学科" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="subject_no" label="学科编码" rules={[{ required: true, message: "请填写学科编码" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="module" label="大概念" rules={[{ required: true, message: "请填写大概念" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="module_no" label="大概念编码" rules={[{ required: true, message: "请填写大概念编码" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="chapter" label="重要概念" rules={[{ required: true, message: "请填写重要概念" }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="chapter_no"
            label="重要概念编码"
            rules={[{ required: true, message: "请填写重要概念编码" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="secondary" label="次位概念">
            <Input />
          </Form.Item>
          <Form.Item name="secondary_no" label="次位概念编码">
            <Input />
          </Form.Item>
          <Form.Item
            name="knowledge_name"
            label="知识点名称"
            rules={[{ required: true, message: "请填写知识点名称" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="knowledge_no"
            label="知识点编码"
            rules={[{ required: true, message: "请填写知识点编码" }]}
          >
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={`历史版本 — ${versionTitle}`}
        open={versionsOpen}
        onClose={() => setVersionsOpen(false)}
        width={480}
      >
        <Timeline
          pending={versionsLoading ? "加载中" : undefined}
          items={versions.map((item, index) => ({
            color: CHANGE_COLOR[item.change_type] || "gray",
            children: (
              <div>
                <Space size={8}>
                  <Tag color={CHANGE_COLOR[item.change_type] || "default"}>
                    {CHANGE_LABEL[item.change_type] || item.change_type}
                  </Tag>
                  <span className="text-xs text-gray-500">v{item.version_no}</span>
                  {index === 0 ? <span className="text-xs text-green-600">最新</span> : null}
                </Space>
                <div className="mt-1 text-xs text-gray-500">
                  {item.operated_at} · {item.operator || "-"} / {item.source || "-"}
                </div>
                <div className="mt-1 whitespace-pre-wrap text-sm">
                  {item.knowledge_content || "（空内容）"}
                </div>
              </div>
            )
          }))}
        />
        {!versionsLoading && versions.length === 0 ? (
          <Typography.Text type="secondary">暂无历史版本</Typography.Text>
        ) : null}
      </Drawer>
    </div>
  );
}
