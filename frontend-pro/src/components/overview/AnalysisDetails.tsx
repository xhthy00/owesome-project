import { Line, Pie, Tiny } from '@ant-design/charts';
import { Card, Progress, Statistic, Table, Tabs, Typography, theme } from 'antd';
import { useState } from 'react';

const format = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
const palette = ['#1677ff', '#36cfc9', '#9254de', '#ffc53d', '#69c0ff', '#5cdbd3', '#ff85c0'];

export type DetailPoint = { label: string; value: number };
export type Profile = { key: string; label: string; percent?: number; caption?: string; points: DetailPoint[] };

export function ProfileChart({ title, profiles, unit, axis }: {
  title: string; profiles: Profile[]; unit: string; axis: string;
}) {
  const [selected, select] = useState('');
  const choices = profiles.filter(item => item.points.length);
  const active = choices.find(item => item.key === selected) ?? choices[0];
  if (!active) return null;
  return <Card variant="borderless" title={title} className="pro-overview-section">
    <Tabs activeKey={active.key} onChange={select} items={choices.map(item => ({
      key: item.key,
      label: <div className="edu-profile-tab"><div>{item.label}<small>{item.caption}</small></div>
        {item.percent != null && <Progress type="circle" size={42} percent={item.percent} strokeWidth={8} />}</div>,
    }))} />
    <Typography.Text type="secondary">{axis} · {unit}</Typography.Text>
    <Line key={active.key} data={active.points} height={246} xField="label" yField="value" animate={false}
      axis={{ x: { title: false, labelAutoHide: true }, y: { title: false } }}
      point={{ sizeField: 3, shapeField: 'point' }} style={{ stroke: '#1677ff', lineWidth: 2 }}
      tooltip={{ title: 'label', items: [{ field: 'value', name: axis, valueFormatter: (n: number) => format.format(n) + unit }] }} />
  </Card>;
}

export function DistributionCard({ title, points, unit, center }: {
  title: string; points: DetailPoint[]; unit: string; center: string;
}) {
  const total = points.reduce((sum, item) => sum + item.value, 0);
  if (!total) return null;
  return <Card variant="borderless" title={title} className="pro-overview-section">
    <div className="pro-overview-composition">
      <div className="pro-overview-donut">
        <Pie data={points.filter(p => p.value > 0)} angleField="value" colorField="label" innerRadius={0.73} radius={0.9} height={236}
          animate={false} legend={false} scale={{ color: { domain: points.map(p => p.label), range: palette } }}
          tooltip={{ title: 'label', items: [{ field: 'value', name: center }] }} />
        <div className="pro-overview-donut-value"><Typography.Text type="secondary">{center}</Typography.Text><strong>{format.format(total)} {unit}</strong></div>
      </div>
      <dl className="pro-overview-chart-legend">{points.map((p, i) => <div key={p.label}>
        <dt><span style={{ background: palette[i % palette.length] }} />{p.label}</dt>
        <dd>{format.format(p.value)} {unit} · {format.format(p.value * 100 / total)}%</dd>
      </div>)}</dl>
    </div>
  </Card>;
}

export function IndicatorTable({ title, name, records, metrics }: {
  title: string; name: string; records: Array<{ id: string; label: string; first: number; second: number }>;
  metrics: [{ label: string; unit: string; summary: string; value: number }, { label: string; unit: string; summary: string; value: number }];
}) {
  const { token } = theme.useToken();
  return <Card variant="borderless" title={title} className="pro-overview-section">
    <div className="edu-detail-stats">{metrics.map((m, i) => <div key={m.label}>
      <Statistic title={m.summary} value={format.format(m.value)} suffix={m.unit} />
      {records.length > 1 && <Tiny.Area height={34} data={records} xField="id" yField={i ? 'second' : 'first'} animate={false}
        style={{ fill: i ? token.cyan3 : token.colorPrimaryBorder, fillOpacity: 0.5 }} />}
    </div>)}</div>
    <Table size="small" rowKey="id" dataSource={records} pagination={records.length > 5 && { pageSize: 5, size: 'small', showSizeChanger: false }}
      columns={[{ title: name, dataIndex: 'label' }, ...metrics.map((m, i) => {
        const key = i ? 'second' : 'first';
        return { title: m.label, dataIndex: key, align: 'right' as const, sorter: (a: typeof records[number], b: typeof records[number]) => a[key] - b[key],
          render: (n: number) => format.format(n) + m.unit };
      })]} />
  </Card>;
}