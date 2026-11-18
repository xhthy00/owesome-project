import { Alert, Card, Empty, Select, Skeleton } from 'antd';
import { useEffect, useState } from 'react';
import { educationApi, type LineReachResult } from '@/api/education';
import { useSession } from '@/workspace';
import { OverviewActions, OverviewAlerts, OverviewFilters, OverviewHeader, ReachComparison, ReachMetrics } from './OverviewPanels';

const actions = [
  { label: '开始学情分析', href: '/chat' },
  { label: '分析工具', href: '/construct/analysis' },
];

export default function LegacyOverview() {
  const { session } = useSession();
  const scope = session?.user.edu_scope;
  const role = scope?.edu_role;
  const personal = role === 'student';
  const local = role === 'teacher' || role === 'school_admin';
  const districtComparison = role === 'bureau_admin' || session?.user.is_platform_admin;
  const title = personal ? '个人学情总览' : role === 'teacher' ? '班级学情总览' :
    role === 'school_admin' ? '学校学情总览' : districtComparison ? undefined : '学情总览';
  const [exam, setExam] = useState<string>();
  const [track, setTrack] = useState<string>();
  const [result, setResult] = useState<LineReachResult>();
  const [loading, setLoading] = useState(!personal);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (personal) return;
    let active = true;
    setLoading(true);
    setError('');
    void educationApi.getLineReach({ exam_name: exam, track })
      .then(next => { if (active) setResult(next); })
      .catch((reason: unknown) => {
        if (!active) return;
        setResult(undefined);
        setError(reason instanceof Error ? reason.message : '加载达线数据失败');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [exam, track, personal, refresh, session?.user.id]);

  const data = !personal && result?.accessible ? result : undefined;
  const scopeLabel = scope?.scope_label || scope?.school_name || '';
  const notice = error || (result?.accessible === false ? result.message || '当前账号无权查看达线数据' : '');
  const rows = data ? districtComparison ? data.districts.map(item => ({
    id: item.district, label: item.district, candidates: item.candidates, by_line: item.by_line,
  })) : data.districts.flatMap(district => district.schools.map(item => ({
    id: item.school_id, label: item.school_name, candidates: item.candidates, by_line: item.by_line,
  }))) : [];
  const summaryRows = data && local && !rows.length ? [{
    id: 'scope', label: scopeLabel || (role === 'teacher' ? '当前授权班级' : '本校'),
    candidates: data.kpis.candidates, by_line: data.kpis.by_line,
  }] : [];
  const dimension = districtComparison ? '区县' : summaryRows.length ? '授权范围' : '学校';
  const filters = data && (
    <>
      {data.exams.length > 0 && <Select aria-label="考试筛选" value={exam ?? data.exam_name}
        options={data.exams.map(value => ({ value }))} onChange={setExam} className="pro-overview-exam-filter" />}
      {data.tracks.length > 0 && <Select aria-label="科类筛选" value={track ?? data.track ?? ''}
        options={[{ value: '', label: '全部科类' }, ...data.tracks.filter(Boolean).map(value => ({ value }))]}
        onChange={value => setTrack(value || undefined)} />}
    </>
  );
  const controls = <OverviewFilters loading={loading} onReload={() => setRefresh(value => value + 1)}>{filters}</OverviewFilters>;

  return (
    <div className="pro-overview" aria-busy={loading}>
      <OverviewHeader title={title} scope={title ? scopeLabel : undefined} controls={data || personal ? undefined : controls} />
      {notice && <Alert type="warning" showIcon title={notice} />}
      {personal && <Card className="pro-overview-section">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前账号暂无可展示的个人成绩" />
      </Card>}
      {loading && !data && <Card className="pro-overview-section"><Skeleton active paragraph={{ rows: 4 }} /></Card>}
      {data && <>
        <ReachMetrics candidates={data.kpis.candidates} lines={data.kpis.by_line} exam={data.exam_name}
          candidateSeries={rows.slice(0, 12).flatMap(row => row.candidates == null ? [] : [{ label: row.label, value: row.candidates }])}
          units={rows.length > 1 ? { count: rows.length, label: dimension } : undefined} />
        <ReachComparison dimension={dimension}
          rows={summaryRows.length ? summaryRows : rows} lines={data.lines.length ? data.lines : data.kpis.by_line}
          summary={data.kpis} controls={controls} />
      </>}
      <div className="pro-overview-support">
        {!personal && !districtComparison && <OverviewAlerts refresh={refresh} />}
        <OverviewActions actions={personal ? actions.slice(0, 1) : actions} />
      </div>
    </div>
  );
}
