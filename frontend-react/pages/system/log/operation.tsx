import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  DatePicker,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";
import { auditApi, type AuditOperationLogItem, type OperationLogQuery } from "@/api/audit";
import { getCurrentUser, type CurrentUser } from "@/api/auth";

const { RangePicker } = DatePicker;

type TimeRange = [Dayjs | null, Dayjs | null] | null;

interface TablePagination {
  current: number;
  pageSize: number;
}

function formatDateTime(value: string | number | null | undefined): string {
  if (!value) return "-";
  const d = new Date(typeof value === "string" && /^\d+$/.test(value) ? Number(value) : value);
  if (Number.isNaN(d.getTime())) return String(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function tryPrettyJson(value: string | null | undefined): string {
  if (!value) return "";
  const trimmed = value.trim();
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) return value;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return value;
  }
}

export default function OperationLogPage() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [userLoading, setUserLoading] = useState(true);

  const [items, setItems] = useState<AuditOperationLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState<TablePagination>({ current: 1, pageSize: 10 });

  const [timeRange, setTimeRange] = useState<TimeRange>(null);
  const [operationType, setOperationType] = useState<string | undefined>(undefined);
  const [success, setSuccess] = useState<string>("all");

  useEffect(() => {
    let cancelled = false;
    setUserLoading(true);
    getCurrentUser()
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .catch((err) => {
        message.error(err instanceof Error ? err.message : "获取当前用户失败");
      })
      .finally(() => {
        if (!cancelled) setUserLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const operationTypeOptions = useMemo(
    () => [
      { label: "全部", value: undefined },
      ...Array.from(new Set(items.map((i) => i.operation_type).filter(Boolean))).map((v) => ({
        label: v,
        value: v
      }))
    ],
    [items]
  );

  const resourceTypeOptions = useMemo(
    () => [
      { label: "全部", value: undefined },
      ...Array.from(new Set(items.map((i) => i.resource_type).filter(Boolean))).map((v) => ({
        label: v,
        value: v
      }))
    ],
    [items]
  );

  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(
    async (
      page: number,
      pageSize: number,
      cancelledRef: { current: boolean }
    ) => {
      if (!user) return;
      setLoading(true);
      try {
        const query: OperationLogQuery = {};
        if (timeRange?.[0]) query.start_time = timeRange[0].valueOf();
        if (timeRange?.[1]) query.end_time = timeRange[1].valueOf();
        if (operationType) query.operation_type = operationType;
        if (success !== "all") query.success = success === "true";
        const res = await auditApi.pagerOperationLog(page, pageSize, query);
        if (!cancelledRef.current) {
          setItems(res.items || []);
          setTotal(res.total || 0);
        }
      } catch (err) {
        if (!cancelledRef.current) {
          message.error(err instanceof Error ? err.message : "加载操作日志失败");
        }
      } finally {
        if (!cancelledRef.current) {
          setLoading(false);
        }
      }
    },
    [user, timeRange, operationType, success]
  );

  useEffect(() => {
    if (!user) return;
    const cancelledRef = { current: false };
    void load(pagination.current, pagination.pageSize, cancelledRef);
    return () => {
      cancelledRef.current = true;
    };
  }, [user, pagination.current, pagination.pageSize, refreshKey, load]);

  const handleSearch = () => {
    setPagination((prev) => ({ current: 1, pageSize: prev.pageSize }));
    setRefreshKey((k) => k + 1);
  };

  const handleReset = () => {
    setTimeRange(null);
    setOperationType(undefined);
    setSuccess("all");
    setPagination({ current: 1, pageSize: 10 });
    setRefreshKey((k) => k + 1);
  };

  const columns: ColumnsType<AuditOperationLogItem> = [
    {
      title: "日志ID",
      dataIndex: "id",
      width: 80,
      render: (v) => v ?? "-"
    },
    {
      title: "请求ID",
      dataIndex: "trace_id",
      width: 160,
      ellipsis: true,
      render: (v: string | null) => v || "-"
    },
    {
      title: "操作时间",
      dataIndex: "created_at",
      width: 180,
      render: (v) => formatDateTime(v)
    },
    {
      title: "用户名",
      dataIndex: "user_account",
      width: 160,
      render: (v: string | null) => v || "-"
    },
    {
      title: "IP",
      dataIndex: "ip",
      width: 140,
      render: (v: string | null) => v || "-"
    },
    {
      title: "请求路径",
      dataIndex: "request_path",
      ellipsis: true,
      render: (v) => v || "-"
    },
    {
      title: "操作类型",
      dataIndex: "operation_type",
      width: 120,
      render: (v: string) => <Tag color="blue">{v}</Tag>
    },
    {
      title: "耗时ms",
      dataIndex: "elapsed_ms",
      width: 110,
      render: (v) => (v == null ? "-" : v)
    },
    {
      title: "状态",
      dataIndex: "success",
      width: 100,
      render: (v: boolean) =>
        v ? <Tag color="success">成功</Tag> : <Tag color="error">失败</Tag>
    }
  ];

  const isReady = !userLoading && !!user;
  const emptyText = !isReady ? "加载中..." : "暂无数据";

  return (
    <div className="dbgpt-ui-font h-full overflow-y-auto p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            操作日志
          </Typography.Title>
          <Typography.Text className="text-[#64748b]">
            记录系统用户执行的写入操作（新增、更新、删除等），用于审计与追溯
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => setRefreshKey((k) => k + 1)} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <Card className="mb-4 rounded-2xl" styles={{ body: { padding: 16 } }}>
        <Space wrap className="w-full" align="start">
          <RangePicker
            showTime
            value={timeRange}
            onChange={(vals) => setTimeRange(vals as TimeRange)}
            placeholder={["开始时间", "结束时间"]}
          />
          <Select
            value={operationType}
            onChange={setOperationType}
            options={operationTypeOptions}
            placeholder="操作类型"
            style={{ width: 140 }}
            allowClear
          />
          <Select
            value={success}
            onChange={setSuccess}
            options={[
              { label: "全部", value: "all" },
              { label: "成功", value: "true" },
              { label: "失败", value: "false" }
            ]}
            style={{ width: 120 }}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
            查询
          </Button>
          <Button onClick={handleReset}>重置</Button>
        </Space>
      </Card>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={items}
          loading={loading || userLoading}
          pagination={
            isReady
              ? {
                  current: pagination.current,
                  pageSize: pagination.pageSize,
                  total,
                  showSizeChanger: true,
                  pageSizeOptions: [10, 20, 50],
                  showTotal: (t) => `共 ${t} 条`
                }
              : false
          }
          onChange={(p) => {
            setPagination({ current: p.current || 1, pageSize: p.pageSize || 10 });
          }}
          expandable={{
            expandedRowRender: (row) => {
              const detailText = tryPrettyJson(row.detail);
              return (
                <div className="space-y-2 py-2 text-sm">
                  <div>
                    <span className="font-medium text-[#64748b]">操作详情：</span>
                    {detailText ? (
                      <pre className="mt-1 max-w-3xl overflow-auto rounded bg-[#f8fafc] p-3 text-xs">
                        {detailText}
                      </pre>
                    ) : (
                      <span>-</span>
                    )}
                  </div>
                  <div>
                    <span className="font-medium text-[#64748b]">IP：</span>
                    <span>{row.ip || "-"}</span>
                  </div>
                  <div>
                    <span className="font-medium text-[#64748b]">User-Agent：</span>
                    <span className="break-all">{row.user_agent || "-"}</span>
                  </div>
                  {row.error_msg ? (
                    <div>
                      <span className="font-medium text-[#64748b]">错误信息：</span>
                      <span className="break-all text-red-600">{row.error_msg}</span>
                    </div>
                  ) : null}
                </div>
              );
            }
          }}
          locale={{
            emptyText: emptyText
          }}
          scroll={{ x: 1000 }}
        />
      </div>
    </div>
  );
}
