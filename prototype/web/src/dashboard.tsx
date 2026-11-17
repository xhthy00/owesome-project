import { lazy, Suspense, useState } from 'react';
import { ArrowRightOutlined, ArrowUpOutlined, FileTextOutlined, FundOutlined, InfoCircleOutlined, SafetyCertificateOutlined, ThunderboltFilled } from '@ant-design/icons';
import { Button, Card, Col, Grid, Progress, Row, Select, Tabs, Tooltip } from 'antd';
import { alerts, exams, schools, subjectRates } from './data';
import type { ExamPeriod } from './charts';

const Plot = lazy(() => import('./charts'));
const MetricPlot = lazy(() => import('./charts').then(module => ({ default: module.MetricPlot })));

const metrics = [
  { label: '覆盖学生', unit: '人', trend: '8.4%', definition: '参加本次考试的学生人数' },
  { label: '平均达线率', unit: '%', trend: '3.2 个百分点', definition: '达到当前考试达标线的学生比例' },
  { label: '优秀率', unit: '%', trend: '1.8 个百分点', definition: '达到当前考试优秀线的学生比例' },
  { label: '待关注异常', unit: '项', definition: '当前学校范围内待复核的学情提醒' },
];

export function SubjectChart({ exam, school, compact = false, period }: { exam: string; school: string; compact?: boolean; period?: ExamPeriod }) {
  const height = compact ? 218 : 300;
  return <Suspense fallback={<div className="chart-loading" style={{ minHeight: height }} role="status">加载图表…</div>}>
    <Plot exam={exam} school={school} height={height} period={period} />
  </Suspense>;
}

export function Dashboard({ navigate }: { navigate: (page: string) => void }) {
  const [exam, setExam] = useState(exams[0]);
  const [school, setSchool] = useState(schools[0]);
  const { lg } = Grid.useBreakpoint();
  const offset = exams.indexOf(exam) + schools.indexOf(school);
  const subjects = subjectRates(exam, school);
  const scopedAlerts = alerts.filter(alert => school === schools[0] || alert.school === school);
  const urgentCount = scopedAlerts.filter(alert => alert.level === '重点关注').length;
  const values = [12846 - offset * 1136, 83.6 - offset * 0.8, 28.4 + offset * 0.7, scopedAlerts.length];
  const coverage = subjects.map(subject => subject.current);
  const excellence = [-4.1, 0.8, 5.6, -5.2, 4.3, -1.4].map(delta => values[2] + delta);
  const filters = <div className="filter-actions overview-filters">
    <Select aria-label="考试筛选" value={exam} onChange={setExam} options={exams.map(value => ({ value }))} />
    <Select aria-label="学校筛选" value={school} onChange={setSchool} options={schools.map(value => ({ value }))} />
  </div>;
  return <div className="page-content overview">
    <Row gutter={[24, 24]} aria-label="关键指标">{metrics.map((metric, index) => <Col xs={24} sm={12} xl={6} key={metric.label}>
      <Card variant="borderless" className="overview-metric" title={<h2>{metric.label}</h2>}>
        <div className="metric-meta"><span>{metric.label}</span><Tooltip title={metric.definition}><InfoCircleOutlined aria-label={metric.definition} /></Tooltip></div>
        <div className="metric-total">{values[index].toLocaleString('zh-CN', { minimumFractionDigits: metric.unit === '%' ? 1 : 0, maximumFractionDigits: 1 })}{metric.unit}</div>
        <div className="metric-visual">
          {index === 0 && <span>对比上次考试 <strong>{metric.trend}</strong> <ArrowUpOutlined className="metric-up" /></span>}
          {index === 1 && <Suspense fallback={null}><MetricPlot kind="area" values={coverage} /></Suspense>}
          {index === 2 && <Suspense fallback={null}><MetricPlot kind="column" values={excellence} /></Suspense>}
          {index === 3 && <Progress percent={scopedAlerts.length ? Math.round(100 * urgentCount / scopedAlerts.length) : 0} strokeColor={{ from: '#108ee9', to: '#87d068' }} size="small" aria-label="重点关注占比" />}
        </div>
        <div className="metric-footer">{index === 0 ? <>统计范围 <strong>{school}</strong></> : index === 3
          ? <>重点关注 <strong>{urgentCount} 项</strong></>
          : <>较上次 <strong>{metric.trend} <ArrowUpOutlined className="metric-up" /></strong></>}</div>
      </Card>
    </Col>)}</Row>
    <Card variant="borderless" className="overview-analysis" styles={{ body: { padding: 0 } }}>
      <Tabs size="large" destroyOnHidden tabBarExtraContent={lg ? filters : undefined} items={([
        { key: 'current', label: '本次考试' },
        { key: 'previous', label: '上次考试' },
      ] as const).map(({ key, label }) => ({
        key, label, children: <>{!lg && <div className="overview-mobile-filters">{filters}</div>}<Row>
            <Col xs={24} md={15} xl={16} className="analysis-chart"><SubjectChart exam={exam} school={school} period={key} /></Col>
            <Col xs={24} md={9} xl={8} className="subject-ranking">
              <h3>学科达线率排名</h3>
              <ol className="ranking-update" key={JSON.stringify([exam, school, key])}>{[...subjects].sort((first, second) => second[key] - first[key]).map((subject, index) => <li key={subject.name}><span className="rank-index" aria-hidden="true">{index + 1}</span><span>{subject.name}</span><strong>{subject[key].toFixed(1)}%</strong></li>)}</ol>
              <Button type="link" onClick={() => navigate('benchmark')}>达线看板 <ArrowRightOutlined /></Button>
            </Col>
          </Row></>,
      }))} />
    </Card>
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={12}><Card variant="borderless" title="学情异常" extra={<Button type="link" onClick={() => navigate('alerts')}>查看全部 <ArrowRightOutlined /></Button>}>
          <div className="alert-list">{scopedAlerts.slice(0, 4).map(alert => <button className="alert-row" type="button" key={alert.title} onClick={() => navigate(`alerts/${alerts.indexOf(alert)}`)}>
            <i className={alert.level === '重点关注' ? 'high' : ''} /><span><strong>{alert.title}</strong><small>{alert.school} · {alert.time}</small></span><ArrowRightOutlined className="alert-arrow" />
          </button>)}</div>
      </Card></Col>
      <Col xs={24} xl={12}><Card variant="borderless" title="常用操作"><div className="overview-links" role="group" aria-label="常用操作">
      {[
        { label: '开始问数', icon: <ThunderboltFilled />, page: 'chat' },
        { label: '生成学情报告', icon: <FileTextOutlined />, page: 'analysis' },
        { label: '管理数据源', icon: <FundOutlined />, page: 'resources' },
        { label: '配置角色权限', icon: <SafetyCertificateOutlined />, page: 'permissions' },
      ].map(link => (
        <Button type="text" key={link.page} onClick={() => navigate(link.page)}>
          <span className="shortcut-icon" aria-hidden="true">{link.icon}</span>
          <span>{link.label}</span>
          <ArrowRightOutlined aria-hidden="true" />
        </Button>
      ))}
      </div></Card></Col>
    </Row>
  </div>;
}

export function Benchmarks() {
  const [exam, setExam] = useState(exams[0]);
  const [school, setSchool] = useState(schools[0]);
  return <div className="page-content">
    <section className="paper chart-card">
      <div className="card-heading"><h2>学科达线对比</h2><div className="filter-actions"><Select aria-label="考试" value={exam} onChange={setExam} options={exams.map(value => ({ value }))} /><Select aria-label="学校" value={school} onChange={setSchool} options={schools.map(value => ({ value }))} /></div></div>
      <SubjectChart exam={exam} school={school} />
    </section>
  </div>;
}
