import { ReloadOutlined } from "@ant-design/icons";
import { Button, Empty, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { permissionApi, type ResourceGrant } from "@/api/permission";

const columns: ColumnsType<ResourceGrant> = [
  {
    title: "授权主体",
    dataIndex: "principal",
    width: 260,
    render: (_, row) => (
      <span>
        <Tag color={row.principal_type === "role" ? "processing" : "default"}>
          {row.principal_type === "role" ? "角色" : "用户"}
        </Tag>
        {row.principal}
      </span>
    )
  },
  {
    title: "资源类型",
    dataIndex: "resource_type",
    width: 140,
    render: (_, row) => (
      <Tag color={row.resource_type === "datasource" ? "success" : "purple"}>
        {row.resource_type}
      </Tag>
    )
  },
  {
    title: "资源ID集合",
    dataIndex: "resource_ids",
    render: (_, row) => row.resource_ids.join(", ")
  }
];

export default function ConstructPermissionResourcesPage() {
  const [list, setList] = useState<ResourceGrant[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const res = await permissionApi.listResourceGrants();
      setList(res);
      setLoadFailed(false);
    } catch (err) {
      setList([]);
      setLoadFailed(true);
      message.error(err instanceof Error ? err.message : "加载资源授权失败");
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
            资源授权
          </Typography.Title>
          <Typography.Text className="oc-muted">维护用户/角色到数据源与会话资源的授权关系</Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void reload()} loading={loading}>
          刷新
        </Button>
      </div>

      {loadFailed ? <Empty className="mb-4" description="资源授权数据加载失败，请检查后端接口" /> : null}

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={columns} dataSource={list} loading={loading} pagination={false} />
      </div>
    </div>
  );
}
