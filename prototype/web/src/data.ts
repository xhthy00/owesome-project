export const schools = ['全部学校', '滨江实验中学', '文澜中学', '江南中学', '启明中学'];
export const exams = ['八年级期中联考', '八年级月度质量监测', '七年级期中联考'];

export const subjectData = [
  { name: '语文', current: 79.4, previous: 74.8 },
  { name: '数学', current: 82.6, previous: 77.2 },
  { name: '英语', current: 86.8, previous: 80.5 },
  { name: '科学', current: 78.3, previous: 75.6 },
  { name: '道法', current: 88.2, previous: 84.1 },
  { name: '历史', current: 83.5, previous: 79.3 },
];

export function subjectRates(exam: string, school: string) {
  const adjustment = Math.max(0, exams.indexOf(exam)) * 2.3 + Math.max(0, schools.indexOf(school)) * 1.1;
  return subjectData.map((subject, index) => ({
    ...subject,
    current: +(subject.current - adjustment * (index % 2 ? 1 : -1)).toFixed(1),
    previous: +(subject.previous - adjustment / 2).toFixed(1),
  }));
}

export const alerts = [
  { title: '七年级数学计算题失分偏高', school: '滨江实验中学', time: '今天 09:42', level: '重点关注', description: '有理数运算题组的失分率高于同年级均值 12.4 个百分点，建议核对基础运算和答题步骤。' },
  { title: '八年级英语阅读理解差异扩大', school: '文澜中学', time: '今天 08:56', level: '一般提醒', description: '不同班级的阅读理解得分率差距达到 9.2 个百分点，建议结合班级报告复盘。' },
  { title: '九年级科学优秀率环比下降', school: '江南中学', time: '昨天 16:30', level: '一般提醒', description: '科学优秀率比上次监测下降 3.1 个百分点，实验探究题是主要失分项。' },
  { title: '八年级语文作文得分波动', school: '启明中学', time: '昨天 15:10', level: '一般提醒', description: '作文平均得分下降，建议按立意、结构和语言三个维度核对评分记录。' },
  { title: '七年级英语达线率低于预期', school: '江南中学', time: '昨天 14:20', level: '重点关注', description: '本次监测英语达线率低于区域目标，建议关注基础词汇与听力模块。' },
  { title: '九年级数学应用题班级差异扩大', school: '文澜中学', time: '昨天 11:00', level: '一般提醒', description: '各班应用题得分率出现差异，建议对比建模与答题步骤。' },
  { title: '八年级科学实验题达线率走低', school: '滨江实验中学', time: '昨天 10:40', level: '一般提醒', description: '实验题达线率较上期走低，建议核对实验过程记录和证据推理题。' },
];

export type DataRecord = {
  id: string;
  name: string;
  category: string;
  scope: string;
  status: string;
  date: string;
};

export const resources: Record<string, { singular: string; categories: string[]; rows: DataRecord[] }> = {
  '数据源': { singular: '数据源', categories: ['PostgreSQL', 'MySQL', 'SQLite'], rows: [
    { id: 'DS-001', name: '区级学情分析库', category: 'PostgreSQL', scope: '区域教研中心', status: '已连接', date: '2026-11-16 09:42' },
    { id: 'DS-002', name: '滨江实验中学成绩库', category: 'PostgreSQL', scope: '滨江实验中学', status: '已连接', date: '2026-11-16 08:15' },
    { id: 'DS-003', name: '学科知识点库', category: 'MySQL', scope: '区域教研中心', status: '已连接', date: '2026-11-15 16:36' },
    { id: 'DS-004', name: '历史联考归档', category: 'SQLite', scope: '区域教研中心', status: '待校验', date: '2026-11-14 11:20' },
  ] },
  '学校': { singular: '学校', categories: ['初中', '九年一贯制'], rows: schools.slice(1).map((name, index) => ({ id: `SCH-00${index + 1}`, name, category: index === 3 ? '九年一贯制' : '初中', scope: '滨江区', status: '启用', date: '2026-11-14 14:00' })) },
  '试卷库': { singular: '试卷', categories: ['数学', '语文', '英语', '科学'], rows: subjectData.slice(0, 4).map((subject, index) => ({ id: `P-00${index + 1}`, name: `八年级期中联考 · ${subject.name}`, category: subject.name, scope: '八年级', status: '已入库', date: '2026-11-12 10:30' })) },
  '成绩库': { singular: '成绩批次', categories: ['联考', '质量监测'], rows: exams.map((name, index) => ({ id: `S-00${index + 1}`, name, category: index === 1 ? '质量监测' : '联考', scope: index === 2 ? '七年级' : '八年级', status: index === 1 ? '待校验' : '已入库', date: '2026-11-16 09:30' })) },
  '知识点': { singular: '知识点', categories: ['数学', '语文', '英语', '科学'], rows: ['有理数运算', '现代文阅读', '阅读理解', '实验探究'].map((name, index) => ({ id: `KP-00${index + 1}`, name, category: subjectData[index].name, scope: '七至九年级', status: '启用', date: '2026-11-10 14:30' })) },
  '预测分数线': { singular: '预测分数线', categories: ['优秀线', '达标线'], rows: ['八年级联考优秀线', '八年级联考达标线'].map((name, index) => ({ id: `L-00${index + 1}`, name, category: index ? '达标线' : '优秀线', scope: '八年级期中联考', status: '启用', date: '2026-11-16 08:00' })) },
};

export type Report = { id: string; name: string; exam: string; scope: string; type: string; date: string };
export const initialReports: Report[] = [
  { id: 'R-003', name: '八年级期中联考学情报告', exam: exams[0], scope: '全部学校', type: '区域诊断', date: '2026-11-16' },
  { id: 'R-002', name: '滨江实验中学数学质量分析', exam: exams[0], scope: schools[1], type: '学校教研', date: '2026-11-14' },
  { id: 'R-001', name: '七年级期中联考学科对比', exam: exams[2], scope: '全部学校', type: '学科对比', date: '2026-11-12' },
];

export type Role = { id: string; name: string; scope: string; memberCount: number; permissions: string[] };
export const initialRoles: Role[] = [
  { id: 'district', name: '区级教研员', scope: '全部学校', memberCount: 16, permissions: ['查看问数', '生成报告', '导出报告', '查看数据'] },
  { id: 'school', name: '校级管理员', scope: '本校', memberCount: 28, permissions: ['查看问数', '生成报告', '查看数据'] },
  { id: 'teacher', name: '学科教师', scope: '任教学科与班级', memberCount: 86, permissions: ['查看问数', '查看数据'] },
];

export const auditRows = [
  { id: 'LOG-006', user: '林老师', action: '生成区域学情报告', target: '八年级期中联考', result: '成功', type: '操作日志', date: '2026-11-16 10:42' },
  { id: 'LOG-005', user: '陈老师', action: '导出学情报告', target: '滨江实验中学', result: '成功', type: '操作日志', date: '2026-11-16 10:18' },
  { id: 'LOG-004', user: '林老师', action: '访问工作台', target: '学情总览', result: '成功', type: '访问日志', date: '2026-11-16 09:45' },
  { id: 'LOG-003', user: '陈老师', action: '账号登录', target: '区域教研中心', result: '成功', type: '登录日志', date: '2026-11-16 09:02' },
  { id: 'LOG-002', user: '周老师', action: '账号登录', target: '区域教研中心', result: '失败', type: '登录日志', date: '2026-11-15 16:30' },
  { id: 'LOG-001', user: '周老师', action: '访问报告', target: '报告历史', result: '成功', type: '访问日志', date: '2026-11-15 15:03' },
];

export const prompts = ['对比八年级各科达线率', '哪些班级的数学成绩需要关注？', '看看各校的英语成绩分布'];
