import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AppstoreOutlined, ArrowRightOutlined, AuditOutlined, BookOutlined, DashboardOutlined, DatabaseOutlined, FileSearchOutlined, FundOutlined, SearchOutlined, SettingOutlined, ThunderboltFilled, WarningOutlined } from '@ant-design/icons';
import { App, Button, ConfigProvider, Input, Modal, Tooltip } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { XProvider } from '@ant-design/x';
import { history, useLocation } from '@umijs/max';

export const menu = [
  { path: '/insights', name: '分析与洞察', icon: <DashboardOutlined />, children: [
    { path: '/overview', name: '学情总览', icon: <AppstoreOutlined /> },
    { path: '/analysis', name: '学情分析', icon: <FileSearchOutlined /> },
    { path: '/benchmark', name: '达线看板', icon: <FundOutlined /> },
    { path: '/alerts', name: '异常提醒', icon: <WarningOutlined /> },
  ] },
  { path: '/management', name: '工作台管理', icon: <SettingOutlined />, children: [
    { path: '/resources', name: '数据管理', icon: <DatabaseOutlined /> },
    { path: '/permissions', name: '权限管理', icon: <SettingOutlined /> },
    { path: '/audit', name: '系统日志', icon: <AuditOutlined /> },
  ] },
  { path: '/chat', name: 'AI 问数', icon: <ThunderboltFilled /> },
  { path: '/docs', name: '使用帮助', icon: <BookOutlined /> },
];

export const pages = menu.flatMap(item => item.children ?? [item]);
const WorkspaceContext = createContext<{
  taskSlot: HTMLDivElement | null;
  setTaskSlot: (node: HTMLDivElement | null) => void;
  openSearch: () => void;
}>({ taskSlot: null, setTaskSlot: () => {}, openSearch: () => {} });

export const useWorkspace = () => useContext(WorkspaceContext);

export function SidebarContent({ children, showTasks, closeAfterNavigation }: {
  children: ReactNode;
  showTasks: boolean;
  closeAfterNavigation?: () => void;
}) {
  const { setTaskSlot } = useWorkspace();
  const { pathname } = useLocation();
  const previousPath = useRef(pathname);
  useEffect(() => {
    if (previousPath.current !== pathname) closeAfterNavigation?.();
    previousPath.current = pathname;
  }, [pathname, closeAfterNavigation]);
  return <div className="pro-sider-content">{children}{showTasks && <div className="pro-task-slots" ref={setTaskSlot} />}</div>;
}

export function SearchAction() {
  const { openSearch } = useWorkspace();
  return <Tooltip title="搜索功能 (Ctrl K)"><Button type="text" icon={<SearchOutlined />} aria-label="搜索功能" onClick={openSearch} /></Tooltip>;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [taskSlot, setTaskSlot] = useState<HTMLDivElement | null>(null);
  const [search, setSearch] = useState(false);
  const [query, setQuery] = useState('');
  const matches = pages.filter(item => item.name.toLowerCase().includes(query.trim().toLowerCase()));

  function openSearch() {
    setQuery('');
    setSearch(true);
  }

  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openSearch();
      }
    }
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, []);

  function select(path: string) {
    history.push(path);
    setSearch(false);
  }

  return <ConfigProvider locale={zhCN} theme={{ cssVar: { key: 'awesome-data' } }}>
    <XProvider><App className="workspace-theme">
      <WorkspaceContext.Provider value={{ taskSlot, setTaskSlot, openSearch }}>
        {children}
        <Modal title="搜索功能" open={search} onCancel={() => setSearch(false)} footer={null} destroyOnHidden>
          <Input autoFocus aria-label="搜索功能名称" prefix={<SearchOutlined />} placeholder="输入功能名称" value={query} onChange={event => setQuery(event.target.value)} onPressEnter={() => { if (matches[0]) select(matches[0].path); }} />
          <div className="search-results">{matches.length ? matches.map(item => <button type="button" key={item.path} onClick={() => select(item.path)}>{item.icon}<span>{item.name}</span><ArrowRightOutlined /></button>) : <p>未找到匹配的功能。</p>}</div>
        </Modal>
      </WorkspaceContext.Provider>
    </App></XProvider>
  </ConfigProvider>;
}
