import { Link } from '@umijs/max';
import type { RunTimeLayoutConfig } from '@umijs/max';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { XProvider } from '@ant-design/x';
import { Fragment } from 'react';
import type { ReactNode } from 'react';
import BrandLogo from './components/BrandLogo';
import settings from '../config/defaultSettings';
import { WorkspaceActions, WorkspaceGate, WorkspaceProvider, SiderFooter } from './workspace';
import { TaskMenu } from './task-menu';
import { loadMenu } from './navigation';
import 'antd/dist/reset.css';
import '../styles/workspace.css';

export const rootContainer = (container: ReactNode) =>
  <ConfigProvider locale={zhCN} theme={{ cssVar: { key: 'awesome-data' } }}>
    <XProvider><WorkspaceProvider>{container}</WorkspaceProvider></XProvider>
  </ConfigProvider>;

export const layout: RunTimeLayoutConfig = () => ({
  ...settings,
  logo: <BrandLogo size={30} />,
  menu: { locale: false, request: loadMenu },
  menuItemRender: (item, dom) => item.path ? <Link to={item.path}>{dom}</Link> : dom,
  menuContentRender: ({ collapsed }, dom) => <div className="pro-sider-flow">
    <Fragment key={collapsed ? 'compact' : 'expanded'}>{dom}</Fragment>
    <div className="pro-sider-tasks" hidden={collapsed}><TaskMenu /></div>
    {!collapsed && <SiderFooter />}
  </div>,
  actionsRender: () => [<WorkspaceActions key="workspace" />],
  childrenRender: children => <WorkspaceGate>{children}</WorkspaceGate>,
});
