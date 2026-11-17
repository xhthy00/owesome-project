import { FundProjectionScreenOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Empty,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  educationApi,
  type LineReachDistrictRow,
  type LineReachLineMeta,
  type LineReachLineStat,
  type LineReachResult,
  type LineReachSchoolRow,
  type LineReachView
} from "@/api/education";
import G2Chart from "@/components/chat/G2Chart";

type ScopeTab = "all" | "physics" | "history";

const PANEL_CARD =
  "overflow-hidden rounded-2xl border border-[#e2e8f0] bg-white shadow-[0_2px_14px_rgba(15,23,42,0.06)] dark:border-[#334155] dark:bg-[#141923]";

const KPI_SURFACE =
  "h-full rounded-2xl border border-[#e2e8f0] bg-gradient-to-br from-white via-[#fafcff] to-[#f1f6ff] p-4 shadow-[0_2px_14px_rgba(15,23,42,0.06)] dark:border-[#334155] dark:from-[#141923] dark:via-[#11161f] dark:to-[#0f141c]";

const SECTION_CARD_STYLES = {
  header: {
    borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
    padding: "12px 16px",
    minHeight: 52
  },
  body: { padding: "12px 16px 16px" }
} as const;

const SCOPE_OPTIONS: { label: string; value: ScopeTab }[] = [
  { label: "全市（物理+历史）", value: "all" },
  { label: "物理", value: "physics" },
  { label: "历史", value: "history" }
];

function trackForTab(tab: ScopeTab): string | undefined {
  if (tab === "physics") return "物理类";
  if (tab === "history") return "历史类";
  return undefined;
}

function lineKey(line: { line_key?: string; line_name: string; threshold: number }): string {
  return line.line_key || `${line.line_name}-${line.threshold}`;
}

function lineLabel(line: { label?: string; line_name: string }): string {
  return line.label || line.line_name;
}

function lineStat(byLine: LineReachLineStat[], line: LineReachLineMeta): LineReachLineStat | undefined {
  const key = lineKey(line);
  return byLine.find((x) => lineKey(x) === key) || byLine.find((x) => x.line_name === line.line_name);
}

function KpiCard({
  title,
  extra,
  value,
  rate
}: {
  title: string;
  extra?: string;
  value: number;
  rate?: number;
}) {
  const barWidth = rate == null ? 0 : Math.max(0, Math.min(rate, 100));
  return (
    <div className={KPI_SURFACE}>
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0 text-[13px] font-semibold leading-snug text-[#0f172a] dark:text-[#f1f5f9]">
          {title}
        </div>
        {extra ? (
          <span className="shrink-0 text-[11px] tabular-nums text-[#64748b] dark:text-[#94a3b8]">{extra}</span>
        ) : null}
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-[26px] font-semibold leading-none tabular-nums text-[#0f172a] dark:text-[#f8fafc]">
          {value.toLocaleString()}
        </span>
        <span className="text-sm text-[#64748b] dark:text-[#94a3b8]">人</span>
      </div>
      {rate != null ? (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between text-[12px] tabular-nums text-[#2563eb] dark:text-[#93c5fd]">
            <span>达线率</span>
            <span>{rate}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[#e8eef5] dark:bg-[#1e3a5f]">
            <div
              className="h-full rounded-full bg-[#2563eb] dark:bg-[#60a5fa]"
              style={{ width: `${barWidth}%` }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function LineReachPage() {
  const [examName, setExamName] = useState<string>("");
  const [scopeTab, setScopeTab] = useState<ScopeTab>("all");
  const [chartLine, setChartLine] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [denied, setDenied] = useState("");
  const [exams, setExams] = useState<string[]>([]);
  const [views, setViews] = useState<Record<ScopeTab, LineReachView> | null>(null);

  const result: LineReachResult | null = views
    ? {
        accessible: true,
        exam_name: examName,
        track: trackForTab(scopeTab) || "",
        exams,
        tracks: [],
        ...views[scopeTab]
      }
    : null;

  const loadData = useCallback(async () => {
    setLoading(true);
    setDenied("");
    try {
      const data = await educationApi.getLineReach({
        exam_name: examName || undefined
      });
      setExams(data.exams || []);
      const nextViews: Record<ScopeTab, LineReachView> = {
        all: data.views?.all || { lines: data.lines, kpis: data.kpis, districts: data.districts },
        physics: data.views?.physics || { lines: data.lines, kpis: data.kpis, districts: data.districts },
        history: data.views?.history || { lines: data.lines, kpis: data.kpis, districts: data.districts }
      };
      setViews(nextViews);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "加载达线看板失败";
      if (msg.includes("无权") || msg.includes("学生")) {
        setDenied(msg);
        setViews(null);
      } else {
        message.error(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [examName]);

  useEffect(() => {
    void loadData();
  }, [examName, loadData]);

  const lines = result?.lines || [];

  useEffect(() => {
    const current = views?.[scopeTab]?.lines || [];
    if (!current.length) return;
    setChartLine((prev) =>
      prev && current.some((l) => lineKey(l) === prev) ? prev : lineKey(current[0])
    );
  }, [scopeTab, views]);

  const districtColumns: ColumnsType<LineReachDistrictRow> = useMemo(() => {
    const cols: ColumnsType<LineReachDistrictRow> = [
      { title: "区县", dataIndex: "district", width: 140, fixed: "left" },
      { title: "参考人数", dataIndex: "candidates", width: 100 }
    ];
    for (const line of lines) {
      const key = lineKey(line);
      const title = lineLabel(line);
      cols.push({
        title: `${title}人数`,
        key: `${key}-n`,
        width: 110,
        render: (_: unknown, row) => lineStat(row.by_line, line)?.reached ?? 0
      });
      cols.push({
        title: `${title}率`,
        key: `${key}-r`,
        width: 110,
        render: (_: unknown, row) => {
          const rate = lineStat(row.by_line, line)?.rate;
          return rate == null ? "—" : `${rate}%`;
        }
      });
    }
    return cols;
  }, [lines]);

  const schoolColumns: ColumnsType<LineReachSchoolRow> = useMemo(() => {
    const cols: ColumnsType<LineReachSchoolRow> = [
      { title: "学校", dataIndex: "school_name", width: 160 },
      { title: "参考人数", dataIndex: "candidates", width: 100 }
    ];
    for (const line of lines) {
      const key = lineKey(line);
      const title = lineLabel(line);
      cols.push({
        title: `${title}人数`,
        key: `${key}-n`,
        width: 110,
        render: (_: unknown, row) => lineStat(row.by_line, line)?.reached ?? 0
      });
      cols.push({
        title: `${title}率`,
        key: `${key}-r`,
        width: 110,
        render: (_: unknown, row) => {
          const rate = lineStat(row.by_line, line)?.rate;
          return rate == null ? "—" : `${rate}%`;
        }
      });
    }
    return cols;
  }, [lines]);

  const chartRows = useMemo(() => {
    if (!result || !chartLine) return [];
    const selected = lines.find((l) => lineKey(l) === chartLine);
    if (!selected) return [];
    return result.districts.map((d) => [d.district, lineStat(d.by_line, selected)?.rate ?? 0]);
  }, [result, chartLine, lines]);

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eff6ff] text-[16px] text-[#2563eb] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
            <FundProjectionScreenOutlined />
          </span>
          <div className="min-w-0">
            <Typography.Title level={4} className="!mb-1">
              达线看板
            </Typography.Title>
            <Typography.Text className="oc-muted">
              按预测分数线统计各区县达线人数与达线率
            </Typography.Text>
          </div>
        </div>
        <Space wrap>
          <Select
            allowClear
            style={{ minWidth: 220 }}
            placeholder="考试"
            value={examName || exams[0] || undefined}
            options={exams.map((e) => ({ value: e, label: e }))}
            onChange={(v) => setExamName(v || "")}
            loading={loading}
          />
          <Segmented
            value={scopeTab}
            options={SCOPE_OPTIONS}
            onChange={(v) => setScopeTab(v as ScopeTab)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void loadData()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      {denied ? (
        <Alert type="warning" showIcon message={denied} />
      ) : (
        <Spin spinning={loading}>
          {!result || result.kpis.candidates === 0 ? (
            <div className={`${PANEL_CARD} py-16`}>
              <Empty
                description={
                  loading ? "正在统计达线数据…" : result ? "当前筛选下暂无成绩" : "暂无数据"
                }
              />
            </div>
          ) : (
            <>
              <div className="mb-4 grid grid-cols-5 gap-3">
                <KpiCard title="参考人数" value={result.kpis.candidates} />
                {result.kpis.by_line.map((item) => (
                  <KpiCard
                    key={lineKey(item)}
                    title={lineLabel(item)}
                    extra={item.threshold_note || `≥${item.threshold}`}
                    value={item.reached}
                    rate={item.rate}
                  />
                ))}
              </div>

              <Card
                variant="borderless"
                className={`${PANEL_CARD} mb-4`}
                styles={SECTION_CARD_STYLES}
                title={<span className="text-[15px] font-semibold text-[#0f172a] dark:text-[#f1f5f9]">各区达线率</span>}
                extra={
                  lines.length > 1 ? (
                    <Select
                      size="small"
                      style={{ minWidth: 140 }}
                      value={chartLine || undefined}
                      options={lines.map((l) => ({ value: lineKey(l), label: lineLabel(l) }))}
                      onChange={setChartLine}
                    />
                  ) : null
                }
              >
                {chartRows.length > 0 ? (
                  <G2Chart
                    type="column"
                    columns={["区县", "达线率"]}
                    rows={chartRows}
                    showLabel
                    accentColor="#2563eb"
                    valueSuffix="%"
                    height={220}
                  />
                ) : (
                  <Empty description="暂无图表数据" />
                )}
              </Card>

              <Card
                variant="borderless"
                className={PANEL_CARD}
                styles={SECTION_CARD_STYLES}
                title={<span className="text-[15px] font-semibold text-[#0f172a] dark:text-[#f1f5f9]">区县达线明细</span>}
              >
                <Table<LineReachDistrictRow>
                  size="small"
                  rowKey="district"
                  columns={districtColumns}
                  dataSource={result.districts}
                  pagination={false}
                  scroll={{ x: true }}
                  expandable={{
                    rowExpandable: (row) => (row.schools || []).length > 0,
                    expandedRowRender: (row) => (
                      <div className="rounded-xl border border-[#e8eef5] bg-[#f8fafc] p-2 dark:border-[#2f3d52] dark:bg-[#0f172a]/60">
                        <Table<LineReachSchoolRow>
                          size="small"
                          rowKey={(r) => r.school_id || r.school_name}
                          columns={schoolColumns}
                          dataSource={row.schools}
                          pagination={false}
                        />
                      </div>
                    )
                  }}
                />
              </Card>
            </>
          )}
        </Spin>
      )}
    </div>
  );
}
