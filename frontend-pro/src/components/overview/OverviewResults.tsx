import { Bar, Bullet, Column, Line } from '@ant-design/charts';
import { DistributionCard, IndicatorTable, ProfileChart } from './AnalysisDetails';
import { Alert, Card, Empty, Table, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState, type ReactNode } from 'react';
import type { OverviewResult, OverviewRow } from '@/api/educationOverview';
import { OverviewAnalysisCard, OverviewMetric, OverviewSideList, overviewNumber } from './OverviewPanels';

const chartAxis = { x: { title: false }, y: { title: false } } as const;
const classColumns: ColumnsType<OverviewRow> = [
  { title: '名次', dataIndex: 'rank', width: 64, render: overviewNumber },
  { title: '学生编号', dataIndex: 'label', width: 146, ellipsis: true },
  { title: '总分', dataIndex: 'total', width: 80, align: 'right', render: overviewNumber },
  { title: '科类', dataIndex: 'track', width: 96 },
  { title: '班级', dataIndex: 'class_name', width: 120 },
];

type Subject = NonNullable<NonNullable<OverviewResult['secondary']>['subject_avg']>[number];
type ScoreBands = NonNullable<NonNullable<OverviewResult['secondary']>['score_bands']>;

function SubjectScores({ subjects, name, metric = 'avg' }: { subjects: Subject[]; name: string; metric?: 'avg' | 'count' }) {
  const { token } = theme.useToken();
  const suffix = metric === 'avg' ? ' 分' : ' 人';
  return <>
    <div aria-label={name + '条形图'} className="pro-overview-plot" role="img">
      <Bar data={subjects} xField="label" yField={metric} height={Math.max(244, subjects.length * 26 + 26)} animate={false} paddingRight={48}
        scale={{ y: { zero: true, nice: true }, x: { paddingInner: 0.48 } }}
        style={{ fill: metric === 'avg' ? token.colorPrimary : token.cyan6, radiusTopRight: 4, radiusBottomRight: 4 }}
        axis={{ x: { title: false }, y: { title: false, labelAutoRotate: false } }}
        label={{
          text: (subject: Subject) => overviewNumber(subject[metric]),
          position: 'right',
          style: { fill: token.colorText, textAlign: 'left', dx: 6 },
        }}
        tooltip={{ title: 'label', items: [{ field: metric, name, valueFormatter: (value: number) => overviewNumber(value) + suffix }] }} />
    </div>
    <dl className="sr-only">
      {subjects.map(subject => <div key={subject.key}>
        <dt>{subject.label}</dt>
        <dd>{overviewNumber(subject[metric])}{suffix}</dd>
      </div>)}
    </dl>
  </>;
}

function ScoreDistribution({ bands, rows, controls, empty }: { bands?: ScoreBands; rows: OverviewRow[]; controls?: ReactNode; empty?: string }) {
  const { token } = theme.useToken();
  const [view, setView] = useState<'bands' | 'percentiles'>('bands');
  const segments = bands?.segments ?? [];
  const scores = rows.flatMap(row => row.total != null && Number.isFinite(row.total) && row.total > 0 ? [row.total] : [])
    .sort((a, b) => a - b);
  const percentiles = scores.length > 1 ? [10, 25, 50, 75, 90].map(rank => {
    const position = (scores.length - 1) * rank / 100;
    const lower = Math.floor(position);
    const score = scores[lower] + (scores[Math.ceil(position)] - scores[lower]) * (position - lower);
    return { rank, label: `${rank}%`, score };
  }) : [];
  const peakCount = Math.max(0, ...segments.map(segment => segment.count));
  const peakBands = segments.filter(segment => peakCount > 0 && segment.count === peakCount);
  const peak = peakBands.length === 1 ? peakBands[0] : undefined;
  const showPercentiles = view === 'percentiles' && percentiles.length > 0;
  const activeKey = showPercentiles ? 'percentiles' : 'bands';
  const validBands = segments.some(segment => segment.count > 0);
  const content = validBands || showPercentiles ? <div className="pro-overview-analysis-grid">
      <section className="pro-overview-plot" aria-label={showPercentiles ? '总分分位数折线图' : '各分数段人数柱状图'}>
        <Typography.Title level={5}>{showPercentiles ? '总分分位分布' : '总分区间分布'}</Typography.Title>
        {showPercentiles ? (
          <Line data={percentiles} xField="label" yField="score" height={264} animate={false}
            point={{ shapeField: 'point', sizeField: 4 }} style={{ stroke: token.colorPrimary, lineWidth: 2 }}
            scale={{ y: { nice: true, zero: false } }} axis={chartAxis}
            tooltip={{ title: 'label', items: [{ field: 'score', name: '分位得分', valueFormatter: (value: number) => overviewNumber(value) + ' 分' }] }} />
        ) : (
          <Column data={segments.map(segment => ({ ...segment, tone: segment.count > 0 && segment.count === peakCount ? 'peak' : 'base' }))}
            xField="label" yField="count" colorField="tone" height={264} animate={false}
            scale={{ x: { paddingInner: 0.38 }, color: { domain: ['base', 'peak'], range: [token.colorPrimaryBorder, token.colorPrimary] } }}
            legend={false} style={{ radiusTopLeft: 4, radiusTopRight: 4 }}
            axis={{ ...chartAxis, x: { title: false, labelAutoHide: true } }}
            label={{ text: (segment: { count: number }) => segment.count > 0 ? overviewNumber(segment.count) : '', position: 'top', style: { fill: token.colorText } }}
            tooltip={{ title: 'label', items: [
              { field: 'count', name: '人数', valueFormatter: (value: number) => overviewNumber(value) + ' 人' },
              { field: 'ratio', name: '占比', valueFormatter: (value: number) => overviewNumber(value) + '%' },
            ] }} />
        )}
      </section>
      <div>
        <OverviewSideList ranked={!showPercentiles} title={showPercentiles ? '关键分位数' : '分数段人数排名'}
          entries={showPercentiles ? percentiles.map(item => ({ key: item.label,
            label: item.rank === 50 ? '中位数' : `${item.label} 分位`, value: `${overviewNumber(item.score)} 分` })) :
            [...segments].sort((a, b) => b.count - a.count).map(item => ({ key: item.label, label: item.label, value: `${overviewNumber(item.count)} 人`, detail: `${overviewNumber(item.ratio)}%` }))} />
        {peak && !showPercentiles && <Typography.Text type="secondary" className="pro-overview-side-note">
          人数最集中在 <strong>{peak.label} 分</strong>
        </Typography.Text>}
      </div>
    </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty || '当前考试暂无有效总分'} />;

  return <OverviewAnalysisCard activeKey={activeKey} onChange={key => setView(key === 'percentiles' ? 'percentiles' : 'bands')}
    controls={controls} items={[
      { key: 'bands', label: '分数段', children: showPercentiles ? null : content },
      ...(percentiles.length > 0 ? [{ key: 'percentiles', label: '分位数', children: showPercentiles ? content : null }] : []),
    ]} />;
}

function SubjectIndicators({ subjects }: { subjects: Subject[] }) {
  if (!subjects.length) return null;
  return <IndicatorTable title="学科成绩分析" name="学科"
    records={subjects.map(s => ({ id: s.key, label: s.label, first: s.avg, second: s.count }))}
    metrics={[
      { label: '均分', unit: '分', summary: '最高学科均分', value: Math.max(...subjects.map(s => s.avg)) },
      { label: '有效人数', unit: '人', summary: '各科有效成绩合计', value: subjects.reduce((sum, s) => sum + s.count, 0) },
    ]} />;
}

export function ClassOverviewResults({ data, support, controls, empty }: {
  data: OverviewResult; support?: ReactNode; controls?: ReactNode; empty?: string;
}) {
  const { token } = theme.useToken();
  const rows = data.table?.rows ?? data.chart?.rows ?? [];
  const bands = data.secondary?.score_bands;
  const subjects = data.secondary?.subject_avg ?? [];
  const baseline = data.secondary?.grade_baseline;
  const baselineNotice = data.notices?.find(item => ['insufficient_sample', 'grade_baseline_unavailable'].includes(item.code));
  const tableNotice = data.notices?.find(item => item.code === 'truncated');
  const studentColumns: ColumnsType<OverviewRow> = [
    ...classColumns,
    ...subjects.map(subject => ({
      title: subject.label, key: subject.key, align: 'right' as const, width: 76,
      render: (_: unknown, row: OverviewRow) => overviewNumber(row.subjects?.[subject.key]),
    })),
  ];

  return <>
    <ScoreDistribution bands={bands} rows={rows} controls={controls} empty={empty} />
    <div className="pro-overview-insight-grid">
      <SubjectIndicators subjects={subjects} />
      <DistributionCard title="总分区间占比" center="有效成绩" unit="人"
        points={(bands?.segments ?? []).map(s => ({ label: s.label, value: s.count }))} />
    </div>

    <ProfileChart title={tableNotice ? '已展示学生的学科分布' : '各科学生成绩分布'} axis="分位得分" unit="分" profiles={subjects.map(subject => {
      const values = rows.map(row => row.subjects?.[subject.key]).filter((n): n is number => n != null && Number.isFinite(n)).sort((a, b) => a - b);
      return { key: subject.key, label: subject.label, caption: `均分 ${overviewNumber(subject.avg)} 分 · 有效率`,
        percent: data.kpis?.candidates ? Math.round(subject.count * 100 / data.kpis.candidates) : undefined,
        points: values.length ? [0, 10, 25, 50, 75, 90, 100].map(q => {
          const index = q * (values.length - 1) / 100;
          const low = Math.floor(index);
          return { label: `P${q}`, value: values[low] + (values[Math.ceil(index)] - values[low]) * (index - low) };
        }) : [] };
    })} />
    {baseline ? (
      <Card variant="borderless" size="small" title="年级均分对照" className="pro-overview-section">
        <div className="pro-overview-metrics pro-overview-baseline">
          <OverviewMetric label={data.scope?.multi_class ? '当前班级范围均分' : '本班均分'} value={baseline.own_avg} unit="分" compact />
          <OverviewMetric label="年级均分" value={baseline.avg} unit="分" compact
            detail={baseline.n != null && baseline.units != null ? `${baseline.n} 人 · ${baseline.units} 个班` : undefined} />
          <OverviewMetric label="与年级分差" value={baseline.gap} unit="分" compact />
        </div>
        {baseline.avg != null && baseline.own_avg != null && (
          <div className="pro-overview-benchmark" role="img" aria-label="班级均分与年级均分基准图">
            <Bullet data={[{ title: '总分均分', measures: baseline.own_avg, targets: baseline.avg }]}
              height={88} animate={false} legend={false} tooltip={false}
              color={{ measures: token.colorPrimary, targets: token.colorTextSecondary }}
              axis={{ x: false, y: { title: false, labelAutoRotate: false, labelFormatter: (value: string) => value + ' 分' } }} />
            <Typography.Text type="secondary">条形为班级均分，刻线为年级均分</Typography.Text>
          </div>
        )}
      </Card>
    ) : baselineNotice?.message && <Alert showIcon type="info" title={baselineNotice.message} />}

    {support}
    <Card variant="borderless" size="small" title="学生成绩明细" className="pro-overview-section"
      extra={rows.length > 0 ? <Typography.Text type="secondary">{overviewNumber(rows.length)} 条记录</Typography.Text> : undefined}>
      {tableNotice?.message && <Alert showIcon type="info" title={tableNotice.message} className="pro-overview-table-notice" />}
      <Table<OverviewRow> rowKey="id" size="small" columns={studentColumns} dataSource={rows}
        pagination={rows.length > 10 && { pageSize: 10, showSizeChanger: false, size: 'small' }}
        scroll={{ x: 'max-content' }} locale={{ emptyText: '当前考试暂无学生成绩' }} />
    </Card>
  </>;
}

export function PersonalOverviewResults({ data, controls }: { data: OverviewResult; controls?: ReactNode }) {
  const { token } = theme.useToken();
  const [tab, setTab] = useState('total');
  const rows = (data.chart?.rows ?? []).filter(row => row.total != null);
  const subjects = data.secondary?.subject_avg ?? [];
  // 科目成绩对应接口返回的最后一场考试，不能按考试名称推断时间顺序。
  const subjectExam = data.chart?.rows?.at(-1);
  const scoreExam = subjectExam?.total != null ? subjectExam : rows.at(-1);
  const activeTab = subjects.length && (tab === 'subject' || !rows.length) ? 'subject' : 'total';
  if (!rows.length && !subjects.length) return (
    <OverviewAnalysisCard controls={controls} items={[{ key: 'results', label: '个人成绩', children:
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={data.notices?.find(item => item.code === 'no_data')?.message || '暂无个人成绩'} />,
    }]} />
  );
  const totalChart = activeTab === 'total' && <div className="pro-overview-analysis-grid">
    <section className="pro-overview-plot" aria-label="考试总分柱状图">
      <Typography.Title level={5}>考试总分对比</Typography.Title>
      <Column data={rows} xField="label" yField="total" height={264} animate={false}
        style={{ fill: token.colorPrimary }} axis={{ ...chartAxis, x: { title: false, labelAutoHide: true } }}
        tooltip={{ items: [{ field: 'total', name: '总分', valueFormatter: (value: number) => overviewNumber(value) + ' 分' }] }} />
    </section>
    <OverviewSideList title="考试成绩" entries={rows.slice(-7).reverse().map(row => ({
      key: row.id, label: row.label, value: `${overviewNumber(row.total)} 分`,
    }))} />
  </div>;
  const subjectChart = activeTab === 'subject' && <div className="pro-overview-analysis-grid">
    <div>{subjectExam?.label && <Typography.Title level={5}>{subjectExam.label}</Typography.Title>}
      <SubjectScores subjects={subjects} name="成绩" />
    </div>
    <OverviewSideList title="学科成绩" entries={subjects.map(subject => ({
      key: subject.key, label: subject.label, value: `${overviewNumber(subject.avg)} 分`,
    }))} />
  </div>;
  return <>
    <div className="pro-overview-metrics">
      {scoreExam?.total != null && <OverviewMetric label="考试总分" value={scoreExam.total} unit="分" detail={scoreExam.label}
        description="当前展示考试的个人总分" comparison={subjects.length > 0 && `本场共有 ${subjects.length} 门有效学科`} />}
      {subjects.length > 0 && <OverviewMetric label="有效学科" value={subjects.length} unit="科" detail={subjectExam?.label}
        description="本场考试返回成绩的学科数" series={subjects.map(s => ({ label: s.label, value: s.avg, unit: '分' }))} area />}
      {rows.length > 0 && <OverviewMetric label="有成绩的考试" value={rows.length} unit="场" detail="当前筛选范围"
        series={rows.map(r => ({ label: r.label, value: r.total!, unit: '分' }))} />}
      {subjects.length > 0 && <OverviewMetric label="最高单科得分" unit="分" value={Math.max(...subjects.map(s => s.avg))}
        detail={subjects.filter(s => s.avg === Math.max(...subjects.map(t => t.avg))).map(s => s.label).join('、')} />}
    </div>

    <OverviewAnalysisCard controls={controls} activeKey={activeTab} onChange={setTab} items={[
      ...(rows.length ? [{ key: 'total', label: '考试总分', children: totalChart }] : []),
      ...(subjects.length ? [{ key: 'subject', label: '各科成绩', children: subjectChart }] : []),
    ]} />

    {subjects.length > 0 && <div className="pro-overview-insight-grid">
      <Card variant="borderless" title="单科成绩明细" className="pro-overview-section">
        <Table size="small" rowKey="key" pagination={false} dataSource={subjects} columns={[
          { title: '学科', dataIndex: 'label' }, { title: '得分', dataIndex: 'avg', align: 'right', render: overviewNumber },
        ]} />
      </Card>
      <DistributionCard title="学科得分构成" center="各科合计" unit="分" points={subjects.map(s => ({ label: s.label, value: s.avg }))} />
    </div>}
    {subjects.length > 1 && <ProfileChart title="个人成绩对照" axis="得分" unit="分" profiles={[
      { key: 'subject', label: '各科成绩', caption: subjectExam?.label, points: subjects.map(s => ({ label: s.label, value: s.avg })) },
      ...(rows.length > 1 ? [{ key: 'exam', label: '各次总分', points: rows.map(r => ({ label: r.label, value: r.total! })) }] : []),
    ]} />}
    {rows.length > 1 && <Card variant="borderless" size="small" title="考试成绩记录" className="pro-overview-section">
      <Table<OverviewRow> rowKey="id" size="small" dataSource={rows}
        columns={[
          { title: '考试', dataIndex: 'label' },
          { title: '总分', dataIndex: 'total', width: 90, align: 'right', render: overviewNumber },
        ]}
        pagination={rows.length > 10 && { pageSize: 10, showSizeChanger: false, size: 'small' }} />
    </Card>}
  </>;
}
