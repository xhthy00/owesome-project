import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { permissionApi, type ResourceGrant } from "@/api/permission";

const fallbackData: ResourceGrant[] = [
  {
    id: 1,
    principal_type: "role",
    principal: "ws_admin",
    resource_type: "datasource",
    resource_ids: [1, 2, 3]
  },
  {
    id: 2,
    principal_type: "user",
    principal: "demo.user",
    resource_type: "chat",
    resource_ids: [10001, 10002]
  }
];

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
  const [list, setList] = useState<ResourceGrant[]>(fallbackData);
  const [loading, setLoading] = useState(false);
  const [usingFallback, setUsingFallback] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const res = await permissionApi.listResourceGrants();
      setList(res);
      setUsingFallback(false);
    } catch (err) {
      setList(fallbackData);
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
            资源授权
          </Typography.Title>
          <Typography.Text className="oc-muted">维护用户/角色到数据源与会话资源的授权关系</Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void reload()} loading={loading}>
          刷新
        </Button>
      </div>

      {usingFallback ? <Alert className="mb-4" type="warning" showIcon message="当前为示例数据，请联调资源授权接口后切换为真实数据。" /> : null}

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={columns} dataSource={list} loading={loading} pagination={false} />
      </div>
    </div>
  );
}
