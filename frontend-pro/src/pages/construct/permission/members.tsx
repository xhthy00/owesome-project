import { ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { systemApi, type Workspace, type WorkspaceMember } from "@/api/system";

export default function PermissionMembersPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [currentOid, setCurrentOid] = useState<number>(1);
  const [rows, setRows] = useState<WorkspaceMember[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async (oid = currentOid) => {
    setLoading(true);
    try {
      const [ws, members] = await Promise.all([
        systemApi.listWorkspaces(),
        systemApi.pagerWorkspaceMembers(oid, 1, 100)
      ]);
      setWorkspaces(ws);
      setRows(members.items);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载成员失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(currentOid);
  }, [currentOid]);

  const columns: ColumnsType<WorkspaceMember> = [
    { title: "UID", dataIndex: "uid", width: 100 },
    { title: "账号", dataIndex: "account", width: 180 },
    { title: "名称", dataIndex: "name", width: 160 },
    { title: "邮箱", dataIndex: "email", width: 220 },
    {
      title: "成员类型",
      dataIndex: "weight",
      render: (_, row) => <Tag color={row.weight === 1 ? "gold" : "blue"}>{row.weight === 1 ? "ws_admin" : "member"}</Tag>
    }
  ];

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            成员管理
          </Typography.Title>
          <Typography.Text className="oc-muted">按工作空间管理成员与角色</Typography.Text>
        </div>
        <Space>
          <Select
            value={currentOid}
            onChange={setCurrentOid}
            options={workspaces.map((item) => ({ label: `${item.name}(${item.id})`, value: item.id }))}
            style={{ width: 240 }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load(currentOid)} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={columns} dataSource={rows} loading={loading} pagination={false} />
      </div>
    </div>
  );
}
