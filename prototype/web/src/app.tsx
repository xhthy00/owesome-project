import { Button, Dropdown, Tooltip } from 'antd';
import { BellOutlined, BookOutlined, ReadOutlined } from '@ant-design/icons';
import { history, Link } from '@umijs/max';
import type { RunTimeLayoutConfig } from '@umijs/max';
import type { ReactNode } from 'react';
import settings from '../config/defaultSettings';
import { WorkspaceProvider, SidebarContent, SearchAction, menu } from './workspace-chrome';
import 'antd/dist/reset.css';
import './styles.css';

export const rootContainer = (container: ReactNode) => <WorkspaceProvider>{container}</WorkspaceProvider>;

export const layout: RunTimeLayoutConfig = () => ({
  ...settings,
  bgLayoutImgList: [
    {
      src: 'https://mdn.alipayobjects.com/yuyan_qk0oxh/afts/img/D2LWSqNny4sAAAAAAAAAAAAAFl94AQBr',
      left: 85,
      bottom: 100,
      height: '303px',
    },
    {
      src: 'https://mdn.alipayobjects.com/yuyan_qk0oxh/afts/img/F6vSTbj8KpYAAAAAAAAAAAAAFl94AQBr',
      left: 0,
      bottom: 0,
      width: '331px',
    },
  ],
  logo: <span className="brand-mark" aria-hidden="true"><ReadOutlined /></span>,
  menu: { locale: false, autoClose: false },
  menuDataRender: () => menu,
  menuItemRender: (item, dom) => item.path ? <Link to={item.path}>{dom}</Link> : dom,
  onMenuHeaderClick: () => history.push('/overview'),
  menuContentRender: (props, dom) => <SidebarContent showTasks={!props.collapsed}
    closeAfterNavigation={props.isMobile ? () => props.onCollapse?.(true) : undefined}>{dom}</SidebarContent>,
  actionsRender: () => [
    <SearchAction key="search" />,
    <Tooltip title="异常提醒" key="alerts"><Button type="text" aria-label="异常提醒" icon={<BellOutlined />} onClick={() => history.push('/alerts')} /></Tooltip>,
    <Tooltip title="使用帮助" key="docs"><Button type="text" aria-label="使用帮助" icon={<BookOutlined />} onClick={() => history.push('/docs')} /></Tooltip>,
  ],
  avatarProps: {
    title: '林老师', children: '林', className: 'workspace-avatar',
    render: (_, dom) => <Dropdown trigger={['click']} menu={{ items: [{ key: 'info', label: '区域教研中心 · 教研管理员', disabled: true }] }}><button type="button" className="user-button" aria-label="林老师的账户菜单">{dom}</button></Dropdown>,
  },
});
