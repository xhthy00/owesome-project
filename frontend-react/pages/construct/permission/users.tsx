import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Form, Input, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { systemApi, type SystemUser } from "@/api/system";

export default function PermissionUsersPage() {
  const [rows, setRows] = useState<SystemUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ account: string; name: string; email?: string; password?: string }>();

  const load = async () => {
    setLoading(true);
    try {
      const res = await systemApi.pagerUsers(1, 100);
      setRows(res.items);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const columns: ColumnsType<SystemUser> = [
    { title: "ID", dataIndex: "id", width: 90 },
    { title: "账号", dataIndex: "account", width: 180 },
    { title: "名称", dataIndex: "name", width: 160 },
    { title: "邮箱", dataIndex: "email", width: 220 },
    {
      title: "角色",
      dataIndex: "id",
      width: 160,
      render: (_, row) => {
        if (row.id === 1 && row.account === "admin") return <Tag color="red">admin</Tag>;
        return <Tag color={row.status ? "blue" : "default"}>{row.status ? "member" : "disabled"}</Tag>;
      }
    },
    { title: "工作空间", dataIndex: "oid", width: 120 }
  ];

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            用户管理
          </Typography.Title>
          <Typography.Text className="oc-muted">对齐 SQLBot 用户管理能力</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新增用户
          </Button>
        </Space>
      </div>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={columns} dataSource={rows} loading={loading} pagination={false} />
      </div>

      <Modal
        title="新增用户"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => {
          form
            .validateFields()
            .then(async (values) => {
              await systemApi.createUser(values);
              message.success("创建成功");
              setOpen(false);
              form.resetFields();
              await load();
            })
            .catch(() => void 0);
        }}
      >
        <Form layout="vertical" form={form}>
          <Form.Item name="account" label="账号" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="password" label="初始密码">
            <Input.Password />
          </Form.Item>
          <Form.Item name="oid" label="工作空间" initialValue={1}>
            <Select options={[{ label: "默认工作空间(1)", value: 1 }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
