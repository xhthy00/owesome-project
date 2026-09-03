import {
  ArrowLeftOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  HistoryOutlined,
  ReloadOutlined
} from "@ant-design/icons";
import { Button, Card, Modal, Popconfirm, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  educationApi,
  type ReportHistoryItem
} from "@/api/education";
import { sanitizeFileName } from "@/utils/exportReportWord";
import { bindIframeWatermark, stampHtmlWatermark } from "@/utils/userWatermark";

export default function AnalysisReportHistoryPage() {
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<ReportHistoryItem[]>([]);
  const [preview, setPreview] = useState<ReportHistoryItem | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await educationApi.listReportHistory(100);
      if (!res.ok || !res.data) {
        message.error(res.message || "加载失败");
        return;
      }
      setItems(res.data.items || []);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openPreview = async (row: ReportHistoryItem) => {
    setPreviewLoading(true);
    try {
      const res = await educationApi.getReportHistoryDetail(row.record_id);
      if (!res.ok || !res.data) {
        message.error(res.message || "加载详情失败");
        return;
      }
      setPreview(res.data);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载详情失败");
    } finally {
      setPreviewLoading(false);
    }
  };

  const downloadHtml = (row: ReportHistoryItem) => {
    const html = row.html || "";
    if (!html.trim()) {
      message.error("暂无报告内容");
      return;
    }
    const title = sanitizeFileName(row.title || "report");
    const blob = new Blob([stampHtmlWatermark(html)], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title}.html`;
    a.click();
    URL.revokeObjectURL(url);
    message.success("HTML 已下载");
  };

  const onDelete = async (row: ReportHistoryItem) => {
    try {
      const res = await educationApi.deleteReportHistory(row.conversation_id);
      if (!res.ok) {
        message.error(res.message || "删除失败");
        return;
      }
      message.success("已删除");
      if (preview?.conversation_id === row.conversation_id) {
        setPreview(null);
      }
      void load();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const columns: ColumnsType<ReportHistoryItem> = [
    {
      title: "报告标题",
      dataIndex: "title",
      key: "title",
      ellipsis: true,
      render: (v: string) => v || "-"
    },
    {
      title: "类型",
      dataIndex: "report_type_label",
      key: "report_type_label",
      width: 140,
      render: (v: string, row) => v || row.report_type || "-"
    },
    {
      title: "数据源",
      dataIndex: "datasource_name",
      key: "datasource_name",
      width: 160,
      ellipsis: true,
      render: (v: string) => v || "-"
    },
    {
      title: "保存时间",
      dataIndex: "create_time",
      key: "create_time",
      width: 180,
      render: (v: string | null) => v || "-"
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      render: (_: unknown, row) => (
        <Space size="small" wrap>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            loading={previewLoading && preview?.record_id === row.record_id}
            onClick={() => void openPreview(row)}
          >
            预览
          </Button>
          <Popconfirm title="确认删除该报告历史？" onConfirm={() => void onDelete(row)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      )
    }
  ];

  return (
    <div className="dbgpt-ui-font flex h-[calc(100vh-3.5rem)] min-h-0 flex-col overflow-hidden px-4 pb-3 pt-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Link href="/construct/analysis">
            <Button type="link" icon={<ArrowLeftOutlined />} style={{ paddingLeft: 0 }}>
              返回分析工具
            </Button>
          </Link>
          <Typography.Title level={4} style={{ margin: 0 }}>
            <HistoryOutlined style={{ marginRight: 8 }} />
            报告历史
          </Typography.Title>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
      </div>

      <Card className="min-h-0 flex-1 overflow-hidden rounded-2xl" styles={{ body: { padding: 12 } }}>
        <Table
          rowKey={(r) => String(r.record_id)}
          loading={loading}
          columns={columns}
          dataSource={items}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{ emptyText: "暂无已保存的分析报告，请先在分析工具中生成并「保存到任务历史」" }}
          size="middle"
        />
      </Card>

      <Modal
        title={preview?.title || "报告预览"}
        open={!!preview}
        onCancel={() => setPreview(null)}
        width="90vw"
        styles={{ body: { height: "75vh", padding: 0 } }}
        footer={
          <Space wrap>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => preview && downloadHtml(preview)}
              disabled={!preview?.html}
            >
              下载 HTML
            </Button>
            <Button onClick={() => setPreview(null)}>关闭</Button>
          </Space>
        }
      >
        {preview?.html ? (
          <iframe
            title={preview.title}
            srcDoc={preview.html}
            sandbox="allow-scripts allow-same-origin"
            referrerPolicy="no-referrer"
            style={{ width: "100%", height: "75vh", border: 0 }}
            onLoad={(e) => bindIframeWatermark(e.currentTarget)}
          />
        ) : (
          <div style={{ padding: 24, color: "rgba(0,0,0,0.45)" }}>暂无内容</div>
        )}
      </Modal>
    </div>
  );
}
