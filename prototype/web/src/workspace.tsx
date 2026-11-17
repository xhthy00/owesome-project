import { useEffect, useState } from 'react';
import { ArrowRightOutlined } from '@ant-design/icons';
import { GridContent, PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import { Alerts, Analysis, Audit, Permissions, Resources } from './admin';
import { Chat } from './chat';
import { Benchmarks, Dashboard } from './dashboard';
import { pages, useWorkspace } from './workspace-chrome';

const navigate = (page: string) => history.push('/' + page);
const openChat = () => navigate('chat');

function Guide() {
  return <div className="page-content">
    <div className="paper guide-list">{[
      { page: 'chat', title: '查询学情数据', description: '选择数据源，从自然语言提问开始，查看结果和 SQL。' },
      { page: 'analysis', title: '生成与查阅报告', description: '按考试、学校和年级设置分析范围，预览报告或查看历史记录。' },
      { page: 'resources', title: '维护分析数据', description: '查看数据源状态，管理学校、试卷、成绩与知识点。' },
      { page: 'permissions', title: '配置成员权限', description: '设置角色的数据范围与功能权限，为成员分配角色。' },
    ].map(item => <button type="button" key={item.page} onClick={() => navigate(item.page)}><span><strong>{item.title}</strong><small>{item.description}</small></span><ArrowRightOutlined /></button>)}</div>
  </div>;
}

export default function Workspace() {
  const { pathname } = useLocation();
  const route = pathname.slice(1);
  const current = route.split('/')[0];
  const [visited, setVisited] = useState(() => new Set([current]));
  const [headerSlot, setHeaderSlot] = useState<HTMLDivElement | null>(null);
  const { taskSlot } = useWorkspace();
  const label = pages.find(item => item.path === '/' + current)?.name;

  useEffect(() => {
    setVisited(previous => previous.has(current) ? previous : new Set([...previous, current]));
  }, [current]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  const show = (page: string) => visited.has(page) || current === page;
  const content = <>
    {current === 'chat' && <div className="chat-page-header" ref={setHeaderSlot} />}
    {show('overview') && <div hidden={current !== 'overview'}><Dashboard navigate={navigate} /></div>}
    <div hidden={current !== 'chat'}><Chat visible={current === 'chat'} headerSlot={headerSlot} taskSlot={taskSlot} onNavigateTask={openChat} /></div>
    {show('analysis') && <div hidden={current !== 'analysis'}><Analysis /></div>}
    {show('benchmark') && <div hidden={current !== 'benchmark'}><Benchmarks /></div>}
    {show('alerts') && <div hidden={current !== 'alerts'}><Alerts route={route} navigate={navigate} /></div>}
    {show('resources') && <div hidden={current !== 'resources'}><Resources /></div>}
    {show('permissions') && <div hidden={current !== 'permissions'}><Permissions /></div>}
    {show('audit') && <div hidden={current !== 'audit'}><Audit /></div>}
    {current === 'docs' && <Guide />}
  </>;

  return <PageContainer title={current === 'overview' || current === 'chat' ? false : label} breadcrumbRender={false}
    className={current === 'chat' ? 'workspace-page workspace-chat' : 'workspace-page'}>
    <GridContent>{content}</GridContent>
  </PageContainer>;
}
