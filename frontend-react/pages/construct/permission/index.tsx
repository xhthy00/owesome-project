import { InfoCircleOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Card, Col, Row, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";

type SummaryRow = {
  key: string;
  module: string;
  status: "ready" | "todo";
  description: string;
};

const summaryRows: SummaryRow[] = [
  {
    key: "role",
    module: "角色管理",
    status: "ready",
    description: "支持查看系统角色定义与职责边界"
  },
  {
    key: "resource",
    module: "资源授权",
    status: "ready",
    description: "支持查看用户/角色对数据源与对话资源的授权关系"
  },
  {
    key: "data-rule",
    module: "数据权限",
    status: "ready",
    description: "支持查看行级与列级权限规则，待后端接口联调"
  }
];

const columns: ColumnsType<SummaryRow> = [
  { title: "模块", dataIndex: "module", width: 150 },
  {
    title: "状态",
    dataIndex: "status",
    width: 120,
    render: (_, row) => (
      <Tag color={row.status === "ready" ? "success" : "default"}>
        {row.status === "ready" ? "已开发" : "待开发"}
      </Tag>
    )
  },
  { title: "说明", dataIndex: "description" }
];

export default function ConstructPermissionIndexPage() {
  const stat = useMemo(
    () => ({
      readyCount: summaryRows.filter((row) => row.status === "ready").length,
      totalCount: summaryRows.length
    }),
    []
  );

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            权限管理
          </Typography.Title>
          <Typography.Text className="oc-muted">
            用于承接 SQLBot 的角色、资源授权与数据权限能力
          </Typography.Text>
        </div>
        <SafetyCertificateOutlined className="text-2xl text-[#1677ff]" />
      </div>

      <Alert
        showIcon
        icon={<InfoCircleOutlined />}
        type="info"
        className="mb-4"
        message="当前页面提供管理入口与状态总览。详情页：角色管理、资源授权、数据权限。"
      />

      <Row gutter={16} className="mb-4">
        <Col span={8}>
          <Card>
            <Statistic title="已完成模块" value={stat.readyCount} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="模块总数" value={stat.totalCount} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="完成进度" suffix="%" value={Math.round((stat.readyCount / stat.totalCount) * 100)} />
          </Card>
        </Col>
      </Row>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table rowKey="key" columns={columns} dataSource={summaryRows} pagination={false} />
      </div>
    </div>
  );
}
