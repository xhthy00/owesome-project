import { Area, Column, Pie, Tiny } from '@ant-design/charts';
import { AlertOutlined, ArrowRightOutlined, CheckCircleOutlined, FileTextOutlined, InfoCircleOutlined, LineChartOutlined, MessageOutlined, ReloadOutlined } from '@ant-design/icons';
import { IndicatorTable, ProfileChart } from './AnalysisDetails';
import { Link } from '@umijs/max';
import { Alert, Button, Card, Empty, Flex, Grid, Progress, Select, Skeleton, Statistic, Tabs, Tag, Tooltip, theme, Typography } from 'antd';
import type { TabsProps } from 'antd';
import { useEffect, useState, type ReactNode } from 'react';
import { educationApi, type AnomalyAlertListResult } from '@/api/education';
import type { ClassExamTrend } from '@/api/educationOverview';

export const formatOverviewNumber = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
export const overviewNumber = (value?: number | null) => value == null ? '—' : formatOverviewNumber.format(value);

type LineStat = { line_key?: string; line_name?: string; label?: string; rate: number; reached: number };
type ReachRow = { id: string; label: string; candidates?: number; by_line?: LineStat[] };
type ReachSummary = { candidates?: number; by_line?: LineStat[] };

export const reachLineKey = (item: { line_key?: string; line_name?: string }) => item.line_key || item.line_name || '';
export const reachLineLabel = (item: { label?: string; line_name?: string }) => item.label || item.line_name || '分数线';

export function OverviewHeader({ title, scope, controls }: { title?: string; scope?: string; controls?: ReactNode }) {
  if (!title && !scope && !controls) return null;
  return <header className="pro-overview-heading">
    {title && <Typography.Title level={1}>{title}</Typography.Title>}
    {scope && <Typography.Text className="pro-overview-scope">{scope}</Typography.Text>}
    {controls && <div className="pro-overview-heading-controls">{controls}</div>}
  </header>;
}

export function OverviewFilters({ children, loading, onReload }: {
  children?: ReactNode; loading: boolean; onReload: () => void;
}) {
  return (
    <Flex className="pro-overview-filters" wrap align="center" gap={8}>
      {children}
      <Button className="pro-overview-reload" aria-label="刷新" title="刷新" icon={<ReloadOutlined />} loading={loading} onClick={onReload} />
    </Flex>
  );
}

export function OverviewAnalysisCard({ items, controls, activeKey, onChange }: {
  items: TabsProps['items']; controls?: ReactNode; activeKey?: string; onChange?: TabsProps['onChange'];
}) {
  const { md } = Grid.useBreakpoint();
  return <Card variant="borderless" className="pro-overview-analysis">
    <Tabs size="large" animated={false} destroyOnHidden activeKey={activeKey} onChange={onChange}
      tabBarExtraContent={md ? controls : undefined}
      renderTabBar={!md && controls ? (props, TabBar) => <div>
        <TabBar {...props} />
        <div className="pro-overview-tab-tools">{controls}</div>
      </div> : undefined}
      items={items?.map(item => ({ ...item, children: <div className="pro-overview-tab-panel">{item.children}</div> }))} />
  </Card>;
}

export function OverviewSideList({ title, entries, ranked }: {
  title: string;
  entries: Array<{ key: string; label: string; value: string; detail?: string }>;
  ranked?: boolean;
}) {
  return <section className="pro-overview-side" aria-label={title}>
    <Typography.Title level={5}>{title}</Typography.Title>
    <ol className="pro-overview-side-list" role="list">
      {entries.map((entry, index) => <li key={entry.key}>
        {ranked && <span className="pro-overview-rank" aria-hidden="true">{index + 1}</span>}
        <span className="pro-overview-side-label">{entry.label}</span>
        <span className="pro-overview-side-value">{entry.value}{entry.detail && <small>{entry.detail}</small>}</span>
      </li>)}
    </ol>
  </section>;
}

type MetricPoint = { label: string; value: number | null; unit?: string; date?: string; sample?: string; marker?: boolean };

export function OverviewMetric({ label, valueLabel, value, unit, detail, progress, description, series, area, trend, comparison, compact, chart }: {
  label: string; value?: number | null; unit?: string; detail?: string; progress?: number;
  description?: string; series?: MetricPoint[]; area?: boolean; trend?: boolean; comparison?: ReactNode; compact?: boolean;
  valueLabel?: string; chart?: ReactNode;
}) {
  const { token } = theme.useToken();
  const MiniPlot = trend ? Area : area ? Tiny.Area : Tiny.Column;
  const values = trend ? series?.flatMap(point => point.value == null ? [] : [point.value]) ?? [] : [];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const margin = Math.max(1, (high - low) * 0.2);
  const trendData = trend ? series?.map(point => ({
    ...point,
    heightRatio: point.value == null ? null : unit === '%' ? point.value / 100 :
      (point.value - low + margin) / (high - low + 2 * margin),
  })) : undefined;
  return (
    <Card className={`pro-overview-metric${compact ? ' pro-overview-metric-compact' : ''}`} variant="borderless" size="small" title={compact ? undefined : label}>
      {description && <Tooltip title={description} trigger={['hover', 'focus']}>
        <Button type="text" size="small" className="pro-overview-metric-info" icon={<InfoCircleOutlined />} aria-label={description} />
      </Tooltip>}
      <Statistic title={valueLabel ?? label} value={overviewNumber(value)} suffix={value == null ? undefined : unit} />
      {!compact && <div className="pro-overview-metric-chart">
        {chart}
        {series && series.length > 1 && <div className="pro-overview-mini-chart"
          role={trend ? 'group' : 'img'}
          aria-label={trend ? `${label}，${series.length} 场考试` : series.map(item => `${item.label} ${overviewNumber(item.value)}${item.unit ?? unit ?? ''}`).join('，')}>
          <MiniPlot data={trendData ?? series} xField="label" yField={trend ? 'heightRatio' : 'value'} height={trend ? 44 : 33}
            {...(trend ? { axis: false, shapeField: 'smooth', margin: 0, padding: 3, inset: 0, point: {
              shapeField: 'point',
              sizeField: 2.5,
              tooltip: false,
              style: {
                fill: token.purple5,
                opacity: (point: MetricPoint) => point.marker ? 1 : 0,
              },
            } } : {})}
            animate={false} style={trend ? {
              fill: `linear-gradient(-90deg, ${token.purple1} 0%, ${token.purple5} 100%)`,
              fillOpacity: 0.6,
              connect: false,
            } :
              { fill: area ? token.purple4 : token.colorPrimary }}
            tooltip={trend ? false : { title: 'label', items: [(point: MetricPoint) => ({
              name: label,
              value: `${overviewNumber(point.value)}${point.unit ?? unit ?? ''}`,
            })] }}
            interaction={{ tooltip: { shared: true } }}
            scale={{ x: trend ? { type: 'point', padding: 0.5 } : { paddingInner: 0.45 },
              y: trend ? { domain: [0, 1], nice: false } : { zero: true } }} />
          {trend && <div className="pro-overview-trend-targets">
            {series.map(point => {
              const summary = point.value == null ? '该场暂无数据' : `${label} ${overviewNumber(point.value)}${unit ?? ''}`;
              return <Tooltip key={point.label} trigger={['hover', 'focus']} title={<>
                <div>{point.label} · {point.date}</div>
                <div>{summary}</div>
                {point.sample && <div>{point.sample}</div>}
              </>}>
                <button type="button" aria-label={`${point.label}，${summary}`} />
              </Tooltip>;
            })}
          </div>}
        </div>}
        {comparison}
        {progress != null && <Progress percent={Math.max(0, Math.min(100, progress))}
          aria-label={label} status="normal" strokeColor={token.colorPrimary} size="small" showInfo={false} />}
      </div>}
      {detail && <Typography.Text className="pro-overview-metric-detail" type="secondary">{detail}</Typography.Text>}
    </Card>
  );
}

function metricTrend(points: MetricPoint[], suffix: string) {
  const current = points.at(-1)?.value;
  const previous = points.slice(0, -1).findLast(point => point.value != null)?.value;
  if (current == null || previous == null) return { comparison: '暂无可比历史' };
  const delta = Math.round((current - previous) * 100) / 100;
  const comparison = delta === 0 ? '与上次可比考试持平' :
    `较上次可比考试 ${delta > 0 ? '+' : '−'}${overviewNumber(Math.abs(delta))} ${suffix}`;
  return {
    series: points.map((point, index) => ({
      ...point,
      marker: index === points.length - 1 ||
        (points[index - 1]?.value == null && points[index + 1]?.value == null),
    })),
    detail: comparison,
  };
}

const reachBreakdownLabels = ['本科线', '特控线', '985线', '南大线', '清北线'];

function ClassReachBreakdown({ lines, candidates }: { lines: LineStat[]; candidates?: number }) {
  const { token } = theme.useToken();
  const points = reachBreakdownLabels.map(label => ({
    label,
    stat: candidates === 0 ? undefined : lines.find(line => reachLineLabel(line) === label),
  }));
  return <div className="pro-overview-mini-chart" role="group" aria-label="各分数线达线率">
    <Tiny.Column data={points.map(({ label, stat }) => ({ label, rate: stat?.rate ?? null }))}
      xField="label" yField="rate" height={42} animate={false} tooltip={false}
      scale={{ x: { domain: reachBreakdownLabels, paddingInner: 0.48, paddingOuter: 0.24 }, y: { domain: [0, 100] } }}
      style={{ fill: (point: { label: string }) => point.label === '特控线' ? token.colorPrimary : token.colorPrimaryBorder,
        radiusTopLeft: 3, radiusTopRight: 3 }} />
    <div className="pro-overview-reach-labels">
      {points.map(({ label, stat }) => {
        const summary = stat ? `${overviewNumber(stat.rate)}%，达线 ${overviewNumber(stat.reached)} 人` : '暂无达线数据';
        return <Tooltip key={label} title={`${label} · ${summary}`} trigger={['hover', 'focus']}>
          <button type="button" aria-label={`${label}，${summary}`} data-empty={!stat}>{label}</button>
        </Tooltip>;
      })}
    </div>
  </div>;
}

export function ReachMetrics({ candidates, lines, scores, units, candidateSeries, exam, classTrends, classResults }: {
  candidates?: number; lines: LineStat[];
  scores?: { avg?: number | null; min?: number | null; max?: number | null; count?: number };
  units?: { count: number; label: string };
  candidateSeries?: MetricPoint[]; exam?: string;
  classTrends?: ClassExamTrend[];
  classResults?: boolean;
}) {
  if (candidates == null && !lines.length) return null;
  const history = classTrends ?? [];
  const avgTrend = metricTrend(history.map(point => ({
    label: point.exam, date: point.date, value: point.avg, sample: `${overviewNumber(point.valid_count)} 人有效成绩`,
  })), '分');
  const special = candidates === 0 ? undefined : lines.find(item => reachLineLabel(item) === '特控线');
  const otherLines = classResults ? lines.filter(item => reachLineLabel(item) === '本科线').slice(0, 1) : lines.slice(0, 2);
  const unitsMetric = units && <OverviewMetric label={`参评${units.label}数`} value={units.count} unit="个"
    description={`当前筛选范围内出现在对比结果中的${units.label}数`} detail="当前筛选范围" />;
  return (
    <section className="pro-overview-metrics" aria-label="核心指标">
      {units?.label === '区县' && unitsMetric}
      {candidates != null && <OverviewMetric label="参考人数" value={candidates} unit="人" series={scores ? undefined : candidateSeries} area comparison={scores && scores.min != null && scores.max != null ? `最低 ${overviewNumber(scores.min)} · 最高 ${overviewNumber(scores.max)} 分` : undefined} detail={exam}
        description="当前考试及筛选范围内的参考人数" />}
      {scores?.avg != null && <OverviewMetric label="班级均分趋势" value={scores.avg} unit="分" trend
        description="当前范围内有效总分的平均值"
        detail={scores.count != null ? `有效总分 ${overviewNumber(scores.count)} 人` : undefined} {...avgTrend} />}
      {units?.label !== '区县' && unitsMetric}
      {classResults && <OverviewMetric label="达线情况" valueLabel="特控线达线率" value={special?.rate} unit="%"
        description="柱状图按达线率对比当前考试的各类分数线"
        chart={<ClassReachBreakdown lines={lines} candidates={candidates} />}
        detail={special ? `特控线达线 ${overviewNumber(special.reached)} 人` : '暂无特控线数据'} />}
      {otherLines.map(item => (
        <OverviewMetric key={reachLineKey(item)} label={`${reachLineLabel(item)}达线率`} value={item.rate} unit="%"
          description={`达到${reachLineLabel(item)}的考生占比`}
          detail={`达线 ${overviewNumber(item.reached)} 人`} progress={classResults || item === lines[1] || !candidates ? item.rate : undefined}
          series={!classResults && item === lines[0] && candidates ? [{ label: '达线人数', value: item.reached, unit: '人' }, { label: '未达线人数', value: candidates - item.reached, unit: '人' }] : undefined} />
      ))}
    </section>
  );
}

export function ReachComparison({ dimension, rows, lines, empty, summary, controls }: {
  dimension: string;
  rows: ReachRow[];
  lines: Array<{ line_key?: string; line_name?: string; label?: string }>;
  empty?: string;
  summary?: ReachSummary;
  controls?: ReactNode;
}) {
  const { token } = theme.useToken();
  const [line, setLine] = useState('');
  const [view, setView] = useState<'rate' | 'reached'>('rate');
  const activeLine = lines.find(item => reachLineKey(item) === line) ?? lines[0];
  const selectedKey = activeLine && reachLineKey(activeLine);
  const ranked = rows.flatMap(row => {
    const stat = row.by_line?.find(item => reachLineKey(item) === selectedKey);
    return stat ? [{ ...row, rate: stat.rate, reached: stat.reached }] : [];
  }).sort((left, right) => right[view] - left[view]);
  const comparison = ranked.length > 1;
  const suffix = view === 'rate' ? '%' : ' 人';
  const metricName = view === 'rate' ? '达线率' : '达线人数';
  const top = ranked.slice(0, 7);
  const scopeLine = summary?.by_line?.find(item => reachLineKey(item) === selectedKey);
  const candidates = summary?.candidates ?? 0;
  const composition = candidates > 0 && scopeLine && scopeLine.reached >= 0 && scopeLine.reached <= candidates
    ? { candidates, reached: scopeLine.reached, rate: scopeLine.rate } : undefined;
  const content = ranked.length > 0 ? (
    <div className="pro-overview-analysis-grid">
      <section className="pro-overview-plot" aria-label={`${dimension}${metricName}柱状图`}>
        <Typography.Title level={5}>{dimension}达线{comparison ? '对比' : '情况'}</Typography.Title>
        <Column data={top.map(item => ({ ...item, value: item[view] }))}
          xField="id" yField="value" height={264} animate={false}
          scale={{ x: { paddingInner: 0.45 }, y: view === 'rate' ? { domain: [0, 100] } : { zero: true, nice: true } }}
          style={{ fill: token.colorPrimary }}
          axis={{ x: { title: false, labelAutoHide: true, labelFormatter: (id: string) => top.find(item => item.id === id)?.label ?? id },
            y: { title: false, labelFormatter: (value: string) => view === 'rate' ? value + '%' : value } }}
          tooltip={{ title: 'label', items: [{ field: 'value', name: metricName, valueFormatter: (value: number) => overviewNumber(value) + suffix }] }} />
      </section>
      <OverviewSideList title={comparison ? `${dimension}${metricName}排名` : `${dimension}达线指标`} ranked={comparison}
        entries={top.map(item => ({ key: item.id, label: item.label, value: overviewNumber(item[view]) + suffix }))} />
    </div>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty || '当前筛选下暂无达线数据'} />;
  return <>
    <OverviewAnalysisCard activeKey={view} onChange={key => setView(key === 'rate' ? 'rate' : 'reached')}
      controls={<Flex className="pro-overview-analysis-tools" gap={8} wrap align="center">
        {lines.length > 1 && <Select aria-label="分析分数线" className="pro-overview-line-filter" value={selectedKey}
          options={lines.map(item => ({ value: reachLineKey(item), label: reachLineLabel(item) }))} onChange={setLine} />}
        {controls}
      </Flex>}
      items={[
        { key: 'rate', label: '达线率', children: view === 'rate' ? content : null },
        { key: 'reached', label: '达线人数', children: view === 'reached' ? content : null },
      ]} />
    {ranked.length > 0 && <div className="pro-overview-insight-grid">
      <IndicatorTable title={`${dimension}达线明细`} name={dimension}
        records={ranked.map(row => ({ id: row.id, label: row.label, first: row.reached, second: row.rate }))}
        metrics={[
          { label: '达线人数', unit: '人', summary: '合计达线人数', value: scopeLine?.reached ?? ranked.reduce((n, r) => n + r.reached, 0) },
          { label: '达线率', unit: '%', summary: '最高达线率', value: Math.max(...ranked.map(row => row.rate)) },
        ]} />
      {composition && <ReachComposition {...composition} />}
    </div>}
    <ProfileChart title={`${dimension}分数线对照`} axis="达线率" unit="%" profiles={rows.map(row => ({
      key: row.id, label: row.label, caption: `参考 ${overviewNumber(row.candidates)} 人 · ${activeLine ? reachLineLabel(activeLine) : ''}达线率`,
      percent: row.by_line?.find(item => reachLineKey(item) === selectedKey)?.rate,
      points: (row.by_line ?? []).map(item => ({ label: reachLineLabel(item), value: item.rate })),
    }))} />
  </>;
}

function ReachComposition({ candidates, reached, rate }: { candidates: number; reached: number; rate: number }) {
  const { token } = theme.useToken();
  const groups = [
    { label: '已达线', count: reached, color: token.colorPrimary },
    { label: '未达线', count: candidates - reached, color: token.colorFillSecondary },
  ];
  return (
    <Card variant="borderless" size="small" title="达线构成" className="pro-overview-section" aria-label="达线构成环形图">
      <div className="pro-overview-composition">
        <div className="pro-overview-donut">
          <Pie data={groups} angleField="count" colorField="label" innerRadius={0.74} radius={0.88} height={220} animate={false}
            scale={{ color: { domain: groups.map(item => item.label), range: groups.map(item => item.color) } }}
            legend={false} label={false}
            tooltip={{ title: 'label', items: [{ field: 'count', name: '人数', valueFormatter: (value: number) => overviewNumber(value) + ' 人' }] }} />
          <div className="pro-overview-donut-value">
            <strong>{overviewNumber(rate)}%</strong>
            <Typography.Text type="secondary">达线率</Typography.Text>
          </div>
        </div>
        <dl className="pro-overview-chart-legend">
          {groups.map(group => <div key={group.label}>
            <dt><span aria-hidden="true" style={{ background: group.color }} />{group.label}</dt>
            <dd>{overviewNumber(group.count)} 人</dd>
          </div>)}
        </dl>
      </div>
    </Card>
  );
}

export function OverviewAlerts({ refresh }: { refresh: number }) {
  const [alerts, setAlerts] = useState<AnomalyAlertListResult>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setError('');
    void educationApi.listAnomalyAlerts({ status: 'pending', limit: 5 })
      .then(next => { if (active) setAlerts(next); })
      .catch(() => { if (active) setError('加载异常提醒失败'); });
    return () => { active = false; };
  }, [refresh]);
  if (alerts?.accessible === false) return null;
  return (
    <Card variant="borderless" size="small" title="待关注异常" className="pro-overview-section"
      extra={alerts?.accessible ? <Link to="/construct/education/anomaly-alerts">查看全部{alerts.total > 0 ? `（${alerts.total}）` : ''}</Link> : undefined}>
      {error ? <Alert type="warning" showIcon title={error} /> : !alerts ? <Skeleton active paragraph={{ rows: 2 }} title={false} /> :
        alerts.items.length === 0 ? <div className="pro-overview-alert-empty">
          <CheckCircleOutlined aria-hidden="true" />
          <Typography.Text type="secondary">{alerts.message || '暂无待关注异常'}</Typography.Text>
        </div> :
          <ul className="pro-overview-alerts">
            {alerts.items.map(item => <li key={item.id}>
              <AlertOutlined aria-hidden="true" />
              <Typography.Text className="pro-overview-alert-title">{item.title}</Typography.Text>
              {item.anomaly_type_label && <Tag variant="filled" color="warning">{item.anomaly_type_label}</Tag>}
            </li>)}
          </ul>}
    </Card>
  );
}

export function OverviewActions({ actions }: { actions: Array<{ label: string; href: string }> }) {
  if (!actions.length) return null;
  return (
    <Card variant="borderless" size="small" title="常用操作" className="pro-overview-section">
      <div className="pro-overview-actions">
        {actions.map(action => <Link to={action.href} key={action.href}>
          <span aria-hidden="true">{action.href === '/chat' ? <MessageOutlined /> :
            action.href.endsWith('/line-reach') ? <LineChartOutlined /> : <FileTextOutlined />}</span>
          {action.label}<ArrowRightOutlined aria-hidden="true" />
        </Link>)}
      </div>
    </Card>
  );
}
