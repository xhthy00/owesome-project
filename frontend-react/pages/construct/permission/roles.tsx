import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { permissionApi, type RoleItem, type UserRoleGrant } from "@/api/permission";

const fallbackRoles: RoleItem[] = [
  { id: 1, code: "admin", name: "系统管理员", description: "全局管理权限" },
  { id: 2, code: "ws_admin", name: "工作空间管理员", description: "工作空间内资源管理权限" },
  { id: 3, code: "member", name: "普通成员", description: "基础使用与查询权限" }
];

const fallbackUserGrants: UserRoleGrant[] = [
  { id: 1, user_id: 1, account: "admin", role_codes: ["admin"], oid: 1 },
  { id: 2, user_id: 1001, account: "demo.user", role_codes: ["member"], oid: 1 }
];

const roleColumns: ColumnsType<RoleItem> = [
  { title: "角色编码", dataIndex: "code", width: 160 },
  { title: "角色名称", dataIndex: "name", width: 180 },
  { title: "描述", dataIndex: "description" }
];

const grantColumns: ColumnsType<UserRoleGrant> = [
  { title: "用户ID", dataIndex: "user_id", width: 120 },
  { title: "账号", dataIndex: "account", width: 180 },
  {
    title: "角色",
    dataIndex: "role_codes",
    render: (_, row) => row.role_codes.map((role) => <Tag key={role}>{role}</Tag>)
  },
  { title: "工作空间(OID)", dataIndex: "oid", width: 160 }
];

export default function ConstructPermissionRolesPage() {
  const [roles, setRoles] = useState<RoleItem[]>(fallbackRoles);
  const [grants, setGrants] = useState<UserRoleGrant[]>(fallbackUserGrants);
  const [loading, setLoading] = useState(false);
  const [usingFallback, setUsingFallback] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const [rolesRes, grantsRes] = await Promise.all([
        permissionApi.listRoles(),
        permissionApi.listUserRoleGrants()
      ]);
      setRoles(rolesRes);
      setGrants(grantsRes);
      setUsingFallback(false);
    } catch (err) {
      setRoles(fallbackRoles);
      setGrants(fallbackUserGrants);
      setUsingFallback(true);
      message.warning(err instanceof Error ? `${err.message}，当前显示示例数据` : "后端接口未就绪，显示示例数据");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            角色管理
          </Typography.Title>
          <Typography.Text className="oc-muted">维护系统角色定义与用户角色映射关系</Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void reload()} loading={loading}>
          刷新
        </Button>
      </div>

      {usingFallback ? <Alert className="mb-4" type="warning" showIcon message="当前为示例数据，请联调权限接口后切换为真实数据。" /> : null}

      <Typography.Title level={5}>角色定义</Typography.Title>
      <div className="mb-6 overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={roleColumns} dataSource={roles} loading={loading} pagination={false} />
      </div>

      <Typography.Title level={5}>用户角色映射</Typography.Title>
      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={grantColumns} dataSource={grants} loading={loading} pagination={false} />
      </div>
    </div>
  );
}
