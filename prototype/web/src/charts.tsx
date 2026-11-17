import { useEffect, useMemo, useState } from 'react';
import { Area, Column } from '@ant-design/charts';
import type { ColumnConfig } from '@ant-design/charts';
import { theme } from 'antd';
import { subjectRates } from './data';

const periods = ['本次考试', '上次考试'];

export type ExamPeriod = 'current' | 'previous';

export default function SubjectChart({ exam, school, height, period }: { exam: string; school: string; height: number; period?: ExamPeriod }) {
  const { token } = theme.useToken();
  const query = '(prefers-reduced-motion: reduce)';
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const preference = window.matchMedia(query);
    const sync = () => setReducedMotion(preference.matches);
    preference.addEventListener('change', sync);
    sync();
    return () => preference.removeEventListener('change', sync);
  }, []);
  const data = useMemo(() => subjectRates(exam, school).flatMap(subject => [
    { subject: subject.name, period: periods[0], rate: subject.current },
    { subject: subject.name, period: periods[1], rate: subject.previous },
  ].filter(item => !period || item.period === periods[period === 'current' ? 0 : 1])), [exam, school, period]);

  const config: ColumnConfig = {
    data,
    height,
    xField: 'subject',
    yField: 'rate',
    colorField: 'period',
    group: !period,
    animate: period && !reducedMotion ? {
      enter: { type: 'scaleInY', duration: 240 },
      update: { duration: 240 },
    } : false,
    paddingRight: 4,
    scale: {
      x: { padding: 0.34 },
      y: { domain: [0, 100], tickCount: 5 },
      color: { domain: periods, range: [token.colorPrimary, period ? token.colorPrimaryHover : token.colorPrimaryBorder] },
    },
    style: { maxWidth: period ? 48 : 26 },
    axis: {
      x: { title: false, labelFill: token.colorTextSecondary, labelOpacity: 1, labelFontSize: token.fontSizeSM, tick: false },
      y: { title: false, labelFormatter: (value: string) => value + '%', labelFill: token.colorTextSecondary, labelOpacity: 1, labelFontSize: token.fontSizeSM, grid: true, gridStroke: token.colorBorderSecondary },
    },
    legend: period ? false : { color: { position: 'top', colPadding: 16, size: 25, layout: { justifyContent: 'flex-end' }, itemLabelFill: token.colorTextSecondary, itemLabelFontSize: token.fontSizeSM } },
    tooltip: { title: 'subject', items: [(datum: (typeof data)[number]) => ({ name: datum.period, value: datum.rate + '%' })] },
  };
  return <div className="chart-plot" aria-label={exam + ' · ' + school + ' · 学科达线率'}><Column {...config} /></div>;
}

export function MetricPlot({ kind, values }: { kind: 'area' | 'column'; values: number[] }) {
  const { token } = theme.useToken();
  const config = {
    data: values.map((value, index) => ({ index: String(index), value })),
    xField: 'index',
    yField: 'value',
    height: 46,
    padding: 0,
    axis: false as const,
    tooltip: false as const,
    animate: false as const,
  };
  return <div className="chart-plot" aria-hidden="true">{kind === 'area'
    ? <Area {...config} shapeField="smooth" scale={{ y: { domain: [Math.min(...values) - 3, Math.max(...values) + 2] } }} style={{ fill: 'linear-gradient(-90deg, #ffffff 0%, #975fe4 100%)', fillOpacity: 0.6 }} />
    : <Column {...config} scale={{ x: { paddingInner: 0.4 }, y: { domain: [Math.min(...values) - 3, Math.max(...values) + 2] } }} style={{ fill: token.colorPrimary }} />}
  </div>;
}
