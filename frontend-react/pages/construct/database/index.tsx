import { DeleteOutlined, EditOutlined, LinkOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { datasourceApi, type DatasourceCreatePayload, type DatasourceItem } from "@/api/datasource";

type FormValues = DatasourceCreatePayload;

const emptyForm = (): FormValues => ({
  name: "",
  description: "",
  type: "pg",
  config: {
    host: "",
    port: 5432,
    username: "",
    password: "",
    database: "",
    timeout: 30,
    ssl: false
  }
});

function formatDate(value?: string) {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export default function ConstructDatabasePage() {
  const [list, setList] = useState<DatasourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [form] = Form.useForm<FormValues>();

  const dialogTitle = useMemo(() => (editingId ? "编辑数据源" : "新增数据源"), [editingId]);

  const openDialogWithValues = (values: FormValues) => {
    setDialogOpen(true);
    requestAnimationFrame(() => {
      form.setFieldsValue(values);
    });
  };

  const reload = async () => {
    setLoading(true);
    try {
      const res = await datasourceApi.list({ limit: 200 });
      setList(res.items || []);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const onAdd = () => {
    setEditingId(null);
    openDialogWithValues(emptyForm());
  };

  const onEdit = async (row: DatasourceItem) => {
    try {
      const detail = await datasourceApi.get(row.id);
      setEditingId(row.id);
      openDialogWithValues({
        name: detail.name,
        description: detail.description || "",
        type: detail.type,
        config: { ...emptyForm().config, ...detail.config, password: "" }
      });
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载失败");
    }
  };

  const onDelete = (row: DatasourceItem) => {
    Modal.confirm({
      title: "提示",
      content: `确定删除数据源 ${row.name}？`,
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        await datasourceApi.delete(row.id);
        message.success("已删除");
        await reload();
      }
    });
  };

  const onTest = async (row: DatasourceItem) => {
    try {
      const res = await datasourceApi.testConnection(row.id);
      if (res.success) {
        message.success(res.message || "连接成功");
      } else {
        message.error(res.message || "连接失败");
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "连接失败");
    }
  };

  const onSubmit = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      if (editingId) {
        await datasourceApi.update(editingId, values);
        message.success("已更新");
      } else {
        await datasourceApi.add(values);
        message.success("已新增");
      }
      setDialogOpen(false);
      await reload();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  const onTestForm = async () => {
    const values = await form.validateFields();
    setTesting(true);
    try {
      let id = editingId;
      if (!id) {
        const created = await datasourceApi.add(values);
        id = created.id;
        setEditingId(id);
        message.success("已新增，正在测试连接");
        await reload();
      }
      const res = await datasourceApi.testConnection(id);
      if (res.success) {
        message.success(res.message || "连接成功");
      } else {
        message.error(res.message || "连接失败");
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "连接失败");
    } finally {
      setTesting(false);
    }
  };

  const columns: ColumnsType<DatasourceItem> = [
    { title: "ID", dataIndex: "id", width: 80 },
    {
      title: "名称",
      dataIndex: "name",
      width: 220,
      render: (_, row) => (
        <div className="flex flex-col">
          <span className="font-medium text-[#1f2937]">{row.name}</span>
          {row.description ? <span className="text-xs text-[#94a3b8]">{row.description}</span> : null}
        </div>
      )
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 110,
      render: (_, row) => <Tag className="!m-0 !rounded-md !px-2.5 !py-0.5">{(row.type || "").toUpperCase()}</Tag>
    },
    { title: "描述", dataIndex: "description" },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (_, row) => (
        <Tag
          color={row.status === "online" || row.status === "active" ? "success" : "default"}
          className="!m-0 !rounded-md !px-2.5 !py-0.5"
        >
          {row.status || "unknown"}
        </Tag>
      )
    },
    {
      title: "创建时间",
      dataIndex: "create_time",
      width: 180,
      render: (_, row) => formatDate(row.create_time)
    },
    {
      title: "操作",
      width: 260,
      fixed: "right",
      render: (_, row) => (
        <Space size={4}>
          <Button type="link" icon={<LinkOutlined />} className="!px-1" onClick={() => void onTest(row)}>
            测试连接
          </Button>
          <Button type="link" icon={<EditOutlined />} className="!px-1" onClick={() => void onEdit(row)}>
            编辑
          </Button>
          <Button danger type="link" icon={<DeleteOutlined />} className="!px-1" onClick={() => onDelete(row)}>
            删除
          </Button>
        </Space>
      )
    }
  ];

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            数据源管理
          </Typography.Title>
          <Typography.Text className="oc-muted">配置可用于 SQL 问答的数据库连接</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void reload()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>
            新增数据源
          </Button>
        </Space>
      </div>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={list}
          loading={loading}
          scroll={{ x: 1100 }}
          pagination={false}
          rowClassName={() => "hover:!bg-[#f8fbff]"}
          className="[&_.ant-table-thead>tr>th]:!bg-[#f8fafc] [&_.ant-table-thead>tr>th]:!font-semibold [&_.ant-table-thead>tr>th]:!text-[#334155]"
        />
      </div>

      <Modal
        title={dialogTitle}
        open={dialogOpen}
        onCancel={() => setDialogOpen(false)}
        onOk={() => void onSubmit()}
        okText="保存"
        cancelText="取消"
        confirmLoading={submitting}
        footer={(_, { OkBtn, CancelBtn }) => (
          <>
            <CancelBtn />
            <Button loading={testing} onClick={() => void onTestForm()}>
              测试连接
            </Button>
            <OkBtn />
          </>
        )}
        width={640}
        destroyOnClose
        forceRender
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={emptyForm()}
          preserve={false}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true, message: "请选择类型" }]}>
            <Select
              options={[
                { label: "PostgreSQL", value: "pg" },
                { label: "MySQL", value: "mysql" }
              ]}
            />
          </Form.Item>
          <Form.Item name={["config", "host"]} label="主机" rules={[{ required: true, message: "请输入主机" }]}>
            <Input />
          </Form.Item>
          <Form.Item name={["config", "port"]} label="端口" rules={[{ required: true, message: "请输入端口" }]}>
            <InputNumber min={1} max={65535} className="!w-full" />
          </Form.Item>
          <Form.Item name={["config", "database"]} label="数据库" rules={[{ required: true, message: "请输入数据库" }]}>
            <Input />
          </Form.Item>
          <Form.Item name={["config", "username"]} label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input />
          </Form.Item>
          <Form.Item name={["config", "password"]} label="密码">
            <Input.Password placeholder="编辑时留空表示不修改" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
