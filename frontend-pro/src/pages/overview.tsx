import { Area, Column } from '@ant-design/charts';
import { ArrowRightOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { Link } from '@umijs/max';
import { Alert, Card, Col, Empty, Grid, Row, Select, Space, Tabs, theme, Tooltip } from 'antd';
import { useEffect, useState } from 'react';
import { educationApi } from '@/api/education';
import type { AnomalyAlertListResult, LineReachResult } from '@/api/education';
import { useSession } from '@/workspace';

type View = 'rate' | 'reached';
const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
const lineKey = (item: { line_key?: string; line_name: string }) => item.line_key || item.line_name;

export default function Overview() {
  const { session } = useSession();
  const { lg } = Grid.useBreakpoint();
  const { token } = theme.useToken();
  const [exam, setExam] = useState<string>();
  const [track, setTrack] = useState<string>();
  const [line, setLine] = useState<string>();
  const [result, setResult] = useState<LineReachResult>();
  const [alerts, setAlerts] = useState<AnomalyAlertListResult>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [alertError, setAlertError] = useState('');
  const role = session?.user.edu_scope?.edu_role;
  const showAlerts = role !== 'bureau_admin' && role !== 'student';
  useEffect(() => {
    if (role === 'student') { setLoading(false); return; }
    let active = true;
    setLoading(true);
    setError('');
    void educationApi.getLineReach({ exam_name: exam, track }).then(data => {
      if (active) setResult(data);
    }).catch(error => {
      if (active) {
        setResult(undefined);
        setError(error instanceof Error ? error.message : '加载达线数据失败');
      }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [exam, track, role]);

  useEffect(() => {
    if (!showAlerts) return;
    let active = true;
    void educationApi.listAnomalyAlerts({ status: 'pending', limit: 5 }).then(data => {
      if (active) setAlerts(data);
    }).catch(error => {
      if (active) setAlertError(error instanceof Error ? error.message : '加载异常提醒失败');
    });
    return () => { active = false; };
  }, [showAlerts]);

  const data = result?.accessible ? result : undefined;
  const lines = data?.kpis.by_line ?? [];
  const selectedLine = lines.find(item => lineKey(item) === line) ?? lines[0];
  const schools = (data?.districts ?? []).flatMap(district => district.schools);
  const series = (key: string) => schools.flatMap(school => {
    const stat = school.by_line.find(item => lineKey(item) === key);
    return stat ? [{ id: school.school_id, school: school.school_name, rate: stat.rate, reached: stat.reached }] : [];
  });
  const rows = selectedLine ? series(lineKey(selectedLine)) : [];
  const pendingCount = showAlerts && alerts?.accessible ? alerts.total : undefined;
  // 单校账号拿不到跨校排名：用专门的空态文案，避免看起来像"数据没加载出来"。
  const singleSchoolScope = Boolean(data) && schools.length <= 1;
  const filters = <Space className="pro-overview-filters" wrap>
    <Select aria-label="考试筛选" placeholder="考试" value={exam ?? data?.exam_name} options={result?.exams.map(value => ({ value }))} onChange={setExam} />
    <Select aria-label="科类筛选" placeholder="科类" value={track ?? data?.track} options={result?.tracks.map(value => ({ value }))} onChange={setTrack} />
    <Select aria-label="分数线筛选" placeholder="分数线" value={selectedLine && lineKey(selectedLine)} options={lines.map(item => ({ value: lineKey(item), label: item.label || item.line_name }))} onChange={setLine} />
  </Space>;
  const cards = [
    { title: '覆盖学生', hint: '当前考试与科类下的考生人数', value: data?.kpis.candidates, unit: '人', footer: data?.exam_name, visual: data ? <span>统计科类 <strong>{data.track || '全部科类'}</strong></span> : null },
    ...[0, 1].map(index => {
      const metric = lines[index];
      const mini = metric ? series(lineKey(metric)).slice(0, 16) : [];
      const Plot = index === 0 ? Area : Column;
      const rate = Math.max(0, Math.min(100, Number(metric?.rate) || 0));
      return {
        title: metric?.label || metric?.line_name || '达线率', hint: '当前范围内达到该分数线的考生比例',
        value: metric?.rate, unit: '%', footer: metric ? `达线人数 ${number.format(metric.reached)} 人` : undefined,
        // 多所学校时画迷你趋势；单校账号没有跨校序列，用同色系的单条比例条兜底（不再用 antd 默认绿色进度条）。
        visual: mini.length > 1 ? <div aria-hidden="true" className="pro-overview-spark">
          <Plot data={mini} xField="id" yField="rate" height={46} padding={0} axis={false} tooltip={false}
            animate={false} style={{ fill: index === 0 ? 'l(270) 0:#b691ed 1:#f7f1ff' : token.colorPrimary }} />
        </div> : metric ? <div aria-hidden="true" className="pro-overview-single">
          <span style={{ width: `${rate}%` }} />
        </div> : null,
      };
    }),
    { title: '待关注异常', hint: '待确认学情异常汇总', value: pendingCount, unit: '项',
      visual: pendingCount === undefined ? null : <Link to="/construct/education/anomaly-alerts">查看异常 <ArrowRightOutlined /></Link> },
  ];
  const tabs: { key: View; label: string }[] = [{ key: 'rate', label: '达线率' }, { key: 'reached', label: '达线人数' }];
  return <div className="pro-overview">
    {(error || result?.accessible === false) && <Alert type="warning" showIcon title={error || result?.message || '当前账户无权查看达线数据'} />}
    <Row gutter={[24, 24]}>
      {cards.map((card, index) => <Col key={index} xs={24} sm={12} xl={6}>
        <Card className="pro-overview-metric" title={card.title} variant="borderless" loading={index < 3 && loading}>
          <div className="pro-metric-meta"><span>{card.title}</span><Tooltip title={card.hint}><InfoCircleOutlined aria-label={card.hint} /></Tooltip></div>
          <div className="pro-metric-value">{card.value === undefined ? '—' : number.format(card.value) + card.unit}</div>
          <div className="pro-metric-visual">{card.visual}</div>
          <div className="pro-metric-footer" title={card.footer}>{card.footer || '—'}</div>
        </Card>
      </Col>)}
    </Row>
    <Card className="pro-overview-analysis" variant="borderless" styles={{ body: { padding: 0 } }}>
      <Tabs size="large" destroyOnHidden tabBarExtraContent={lg ? filters : undefined} items={tabs.map(tab => {
        const ranked = [...rows].sort((a, b) => b[tab.key] - a[tab.key]).slice(0, 10);
        const suffix = tab.key === 'rate' ? '%' : '人';
        return { ...tab, children: <>
          {!lg && <div className="pro-overview-mobile-filters">{filters}</div>}
          {ranked.length === 0 ? <Empty className="pro-overview-empty" description={loading ? '加载达线数据…' : singleSchoolScope ? '当前账号仅覆盖本校数据，学校排名需要多所学校范围' : '暂无可展示的达线数据'} /> :
            <Row className="pro-overview-analysis-content" gutter={[24, 24]} aria-busy={loading}>
              <Col xs={24} lg={16}><Column data={ranked} xField="school" yField={tab.key} height={300}
                scale={tab.key === 'rate' ? { y: { domain: [0, 100] } } : undefined}
                style={{ fill: token.colorPrimary }}
                axis={{ x: { title: false }, y: { title: false, labelFormatter: (value: string) => value + suffix } }} /></Col>
              <Col xs={24} lg={8}><h3>学校{tab.label}排名</h3><ol className="pro-overview-ranking">
                {ranked.map((item, index) => <li key={item.id}><span aria-hidden="true">{index + 1}</span><span>{item.school}</span><strong>{number.format(item[tab.key])}{suffix}</strong></li>)}
              </ol><Link to="/construct/education/line-reach">达线看板 <ArrowRightOutlined /></Link></Col>
            </Row>}
        </> };
      })} />
    </Card>
    <Row gutter={[24, 24]}>
    {showAlerts && <Col xs={24} xl={12}><Card title="学情异常" variant="borderless" extra={<Link to="/construct/education/anomaly-alerts">查看全部</Link>}>
      {alertError ? <Alert type="warning" title={alertError} /> : alerts?.items.length ? alerts.items.map(item =>
        <p key={item.id}>{item.title}</p>) : <Empty description={alerts?.message || '暂无待关注异常'} />}
    </Card></Col>}
    <Col xs={24} xl={12}><Card title="常用操作" variant="borderless">
      <Space wrap><Link className="pro-overview-shortcut" to="/chat">开始学情分析 <ArrowRightOutlined /></Link>
        <Link className="pro-overview-shortcut" to="/construct/analysis">学情分析 <ArrowRightOutlined /></Link></Space>
    </Card></Col>
    </Row>
  </div>;
}
