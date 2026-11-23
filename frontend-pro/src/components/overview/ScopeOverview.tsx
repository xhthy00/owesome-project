import { Alert, Card, Empty, Select, Skeleton } from 'antd';
import { useEffect, useState } from 'react';
import { getEducationOverview } from '@/api/educationOverview';
import type { OverviewLevel, OverviewResult } from '@/api/educationOverview';
import { useSession } from '@/workspace';
import { OverviewActions, OverviewAlerts, OverviewAnalysisCard, OverviewFilters, OverviewHeader, ReachComparison, ReachMetrics } from './OverviewPanels';
import { ClassOverviewResults, PersonalOverviewResults } from './OverviewResults';

const LEVEL_TITLES: Record<OverviewLevel, string> = {
  city: '',
  district: '区县学情总览',
  school: '学校学情总览',
  grade: '年级学情总览',
  class: '班级学情总览',
  student: '个人学情总览',
};
const DIMENSION_NAMES: Record<string, string> = { district: '区县', school: '学校', class: '班级' };
const LEVELS = Object.keys(LEVEL_TITLES) as OverviewLevel[];

const selectOptions = (values: string[]) => values.map(value => ({ value, label: value }));

export default function ScopeOverview() {
  const { session } = useSession();
  const [exam, setExam] = useState<string>();
  const [track, setTrack] = useState<string>();
  const [cohort, setCohort] = useState<string>();
  const [classFilter, setClassFilter] = useState<string>();
  const [data, setData] = useState<OverviewResult>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    void getEducationOverview({ exam_name: exam, track, cohort, class_name: classFilter })
      .then(next => { if (active) setData(next); })
      .catch((err: unknown) => {
        if (!active) return;
        setData(undefined);
        setError(err instanceof Error ? err.message : '加载学情总览失败');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [exam, track, cohort, classFilter, refresh, session?.user.id]);

  if (data?.fallback) {
    return <Alert type="warning" showIcon message="当前账号没有分级授权范围，请由管理员在「教育权限」中配置" />;
  }

  const eduScope = session?.user.edu_scope;
  const level = data?.level || LEVELS.find(item => eduScope?.levels?.includes(item));
  const personal = level === 'student';
  const sessionScopeLabel = eduScope?.scope_label ?? '';
  const scopeLabel = level === 'class'
    ? [eduScope?.school_name?.trim(), data?.scope?.label].filter(Boolean).join(' · ') || sessionScopeLabel
    : data?.scope?.label || sessionScopeLabel;
  const filters = data?.filters;
  const exams = filters?.exams ?? [];
  const selectedExam = data?.exam_name ?? undefined;
  const tracks = filters?.tracks ?? [];
  const cohorts = filters?.cohorts ?? [];
  const classes = filters?.classes ?? [];
  const byLine = data?.kpis?.by_line ?? [];
  const rows = data?.chart?.rows ?? [];
  const dimension = DIMENSION_NAMES[data?.chart?.dimension ?? ''];
  const showAlerts = level === 'school' || level === 'grade' || level === 'class';
  const classResults = data?.chart?.dimension === 'student';
  const empty = data?.notices?.find(item => item.code === 'no_data')?.message;
  const scores = data?.secondary?.score_bands;
  const candidateSeries = classResults ? scores?.segments?.map(item => ({ label: item.label, value: item.count })) :
    rows.slice(0, 12).flatMap(item => item.candidates == null ? [] : [{ label: item.label, value: item.candidates }]);
  const filterBar = filters && (
    <>
      {exams.length > 0 && <Select aria-label="考试筛选" placeholder="选择考试"
        value={selectedExam}
        options={personal ? [{ value: '', label: '全部考试' }, ...selectOptions(exams)] : selectOptions(exams)}
        onChange={setExam} className="pro-overview-exam-filter" />}
      {!personal && tracks.length > 0 && <Select aria-label="科类筛选" value={track ?? ''}
        options={[{ value: '', label: '全部科类' }, ...selectOptions(tracks)]}
        onChange={value => setTrack(value || undefined)} />}
      {((level === 'school' && data?.chart?.dimension === 'class') || level === 'class') && cohorts.length > 1 && (
        <Select aria-label="届次筛选" value={cohort ?? ''}
          options={[{ value: '', label: '全部届次' }, ...selectOptions(cohorts)]}
          onChange={value => setCohort(value || undefined)} />
      )}
      {level === 'class' && classes.length > 1 && <Select aria-label="班级筛选" value={classFilter ?? ''}
        options={[{ value: '', label: '全部班级' }, ...selectOptions(classes)]}
        onChange={value => setClassFilter(value || undefined)} />}
    </>
  );
  const controls = <OverviewFilters loading={loading} onReload={() => setRefresh(value => value + 1)}>{filterBar}</OverviewFilters>;
  const support = <div className="pro-overview-support">
    {showAlerts && <OverviewAlerts refresh={refresh} />}
    <OverviewActions actions={[{ label: 'AI 学情分析', href: '/chat' }, ...(data?.actions ?? [])]} />
  </div>;

  return (
    <div className="pro-overview" aria-busy={loading}>
      <OverviewHeader title={level ? LEVEL_TITLES[level] : '学情总览'} scope={level === 'city' ? undefined : scopeLabel}
        controls={!data ? controls : undefined} />

      {error && <Alert type="warning" showIcon title={error} />}
      {loading && !data && <Card className="pro-overview-section"><Skeleton active paragraph={{ rows: 4 }} /></Card>}

      {data && (personal ? (
        <PersonalOverviewResults data={data} controls={controls} />
      ) : <>
        <ReachMetrics candidates={data.kpis?.candidates} lines={byLine} scores={classResults ? scores : undefined} candidateSeries={candidateSeries} exam={selectedExam}
          classTrends={data.secondary?.class_trends} classResults={classResults}
          units={dimension && rows.length > 1 ? { count: rows.length, label: dimension } : undefined} />
        {classResults ? <ClassOverviewResults data={data} support={support} controls={controls} empty={empty} /> :
          dimension ? <ReachComparison dimension={dimension} rows={rows} lines={filters?.lines?.length ? filters.lines : byLine}
            empty={empty || data.notices?.find(item => item.code === 'no_lines')?.message}
            summary={data.kpis} controls={controls} /> :
            <OverviewAnalysisCard controls={controls} items={[{ key: 'overview', label: '成绩分析', children:
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty || '当前筛选下暂无成绩数据'} />,
            }]} />}
        {!classResults && support}
      </>)}
    </div>
  );
}
