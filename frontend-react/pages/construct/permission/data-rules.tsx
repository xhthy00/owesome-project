import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { permissionApi, type DataRuleItem } from "@/api/permission";

const fallbackRules: DataRuleItem[] = [
  {
    id: 1,
    scope: "row",
    datasource_id: 1,
    table_name: "orders",
    rule: "owner_account = ${current_user.account}",
    enabled: true
  },
  {
    id: 2,
    scope: "column",
    datasource_id: 1,
    table_name: "users",
    rule: "mask:phone,email",
    enabled: true
  }
];

const columns: ColumnsType<DataRuleItem> = [
  {
    title: "范围",
    dataIndex: "scope",
    width: 120,
    render: (_, row) => <Tag color={row.scope === "row" ? "green" : "blue"}>{row.scope}</Tag>
  },
  { title: "数据源ID", dataIndex: "datasource_id", width: 140 },
  { title: "表名", dataIndex: "table_name", width: 180 },
  { title: "规则", dataIndex: "rule" },
  {
    title: "状态",
    dataIndex: "enabled",
    width: 110,
    render: (_, row) => <Tag color={row.enabled ? "success" : "default"}>{row.enabled ? "启用" : "停用"}</Tag>
  }
];

export default function ConstructPermissionDataRulesPage() {
  const [list, setList] = useState<DataRuleItem[]>(fallbackRules);
  const [loading, setLoading] = useState(false);
  const [usingFallback, setUsingFallback] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const res = await permissionApi.listDataRules();
      setList(res);
      setUsingFallback(false);
    } catch (err) {
      setList(fallbackRules);
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
            数据权限
          </Typography.Title>
          <Typography.Text className="oc-muted">管理行级过滤与列级可见规则，承接 SQLBot 数据权限能力</Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void reload()} loading={loading}>
          刷新
        </Button>
      </div>

      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="建议后端优先提供权限预览接口（effective permission）以便页面展示最终生效结果。"
      />
      {usingFallback ? <Alert className="mb-4" type="warning" showIcon message="当前为示例数据，请联调数据权限接口后切换为真实数据。" /> : null}

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="id" columns={columns} dataSource={list} loading={loading} pagination={false} />
      </div>
    </div>
  );
}
