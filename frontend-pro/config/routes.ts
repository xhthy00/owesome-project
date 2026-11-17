const construct = '/construct';
const education = construct + '/education';
const permissions = construct + '/permission';

const pages = [
  [construct, '数据构建'],
  [construct + '/analysis', '学情分析'],
  [construct + '/analysis/history', '报告历史', ''],
  [construct + '/app', '应用管理'],
  [construct + '/database', '数据源'],
  [construct + '/dbgpts', 'DBGPT 管理'],
  [construct + '/docs', '系统文档'],
  [construct + '/flow', '流程管理'],
  [construct + '/knowledge', '知识点'],
  [construct + '/models', '模型管理'],
  [construct + '/prompt', '提示词'],
  [construct + '/skills', '技能管理'],
  ...[
    ['anomaly-alerts', '异常提醒'], ['anomaly-rules', '异常规则'],
    ['exam-library', '试卷库'], ['fraction-bar', '预测分数线'], ['line-reach', '达线看板'],
    ['paper-import', '试卷导入'], ['raw-score-import', '原始成绩导入'], ['schools', '学校'],
    ['score-import', '成绩导入'], ['score-library', '成绩库'],
  ].map(([segment, name]) => [education + '/' + segment, name,
    ['schools', 'score-library', 'exam-library'].includes(segment) ? '/index' : '']),
  ...[
    ['config', '权限配置'], ['data-rules', '数据规则'], ['edu', '教育权限'],
    ['edu-permission', '教育数据权限'], ['members', '成员管理'], ['menu', '菜单权限'],
    ['privacy', '数据脱敏'], ['resources', '资源权限'], ['roles', '角色管理'],
    ['users', '用户管理'], ['workspaces', '工作空间'],
  ].map(([segment, name]) => [permissions + '/' + segment, name, '']),
  ...[
    ['access', '访问日志'], ['login', '登录日志'], ['operation', '操作日志'],
  ].map(([segment, name]) => ['/system/log/' + segment, name, '']),
  [permissions, '权限管理'],
];

export default [
  { path: '/login', component: '@/pages/login', layout: false },
  { path: '/share/:token', component: '@/pages/share/param-token', layout: false },
  { path: '/mobile/chat', name: '移动问数', component: '@/pages/mobile-chat', layout: false },
  { path: '/', name: '学情总览', component: '@/pages/overview' },
  { path: '/overview', redirect: '/' },
  { path: '/chat', name: 'AI 学情分析', component: '@/pages/chat' }, // 与 defaultSettings.CHAT_MENU_TITLE 保持一致
  { path: '/conversations', name: '全部任务', component: '@/pages/conversations/index' },
  { path: construct + '/app/extra', name: '应用详情', component: '@/pages/construct/app/extra', hideInMenu: true },
  { path: construct + '/prompt/:type', name: '提示词详情', component: '@/pages/construct/prompt/param-type/index', hideInMenu: true },
  { path: education + '/exam-library/import', name: '导入试卷', component: '@/pages/construct/education/exam-library/import', hideInMenu: true },
  { path: education + '/exam-library/:id', name: '试卷详情', component: '@/pages/construct/education/exam-library/param-id', hideInMenu: true },
  { path: education + '/score-library/import', name: '导入成绩', component: '@/pages/construct/education/score-library/import', hideInMenu: true },
  ...pages.map(([path, name, suffix = '/index']) => ({ path, name, component: '@/pages' + path + suffix })),
  { path: '*', component: '@/pages/not-found' },
];
