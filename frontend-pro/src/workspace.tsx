import { FileTextOutlined } from '@ant-design/icons';
import { App, Alert, Avatar, Button, Dropdown, Space, Spin, Tooltip, Watermark } from 'antd';
import { PageContainer } from '@ant-design/pro-components';
import { history, useLocation } from '@umijs/max';
import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getCurrentUser } from '@/api/auth';
import type { CurrentUser } from '@/api/auth';
import { permissionApi } from '@/api/permission';
import { clearAuthSession, getAccessToken } from '@/auth/session';
import { setCachedWatermarkText, userWatermarkText } from '@/utils/userWatermark';

type Session = { token: string; user: CurrentUser; canReadDocs: boolean };
const SessionContext = createContext<{
  session?: Session;
  setSession?: (session?: Session) => void;
}>({});

export const useSession = () => useContext(SessionContext);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [loadedSession, setSession] = useState<Session>();
  const session = loadedSession?.token === getAccessToken() ? loadedSession : undefined;
  return <App><SessionContext.Provider value={{ session, setSession }}>{children}</SessionContext.Provider></App>;
}

export function WorkspaceGate({ children }: { children: ReactNode }) {
  const { session, setSession } = useSession();
  const { pathname, search } = useLocation();
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState('');
  const token = getAccessToken();
  const chatPage = pathname === '/chat' || pathname === '/mobile/chat';
  // 问数整页铺满：给 body 打标记去掉布局内边距，并记录真实顶栏高度供 CSS 计算高度。
  useEffect(() => {
    if (!chatPage) return;
    document.body.classList.add('pro-chat-page');
    const header = document.querySelector<HTMLElement>('.ant-pro-layout-header, .ant-pro-layout .ant-layout-header');
    const sync = () => {
      const height = header?.getBoundingClientRect().height;
      if (height) document.documentElement.style.setProperty('--pro-chat-header-height', `${Math.round(height)}px`);
    };
    sync();
    const observer = header ? new ResizeObserver(sync) : undefined;
    if (header && observer) observer.observe(header);
    window.addEventListener('resize', sync);
    return () => {
      document.body.classList.remove('pro-chat-page');
      document.documentElement.style.removeProperty('--pro-chat-header-height');
      observer?.disconnect();
      window.removeEventListener('resize', sync);
    };
  }, [chatPage]);
  useEffect(() => {
    if (!token) {
      history.replace('/login?redirect=' + encodeURIComponent(pathname + search));
    }
  }, [token, pathname, search]);
  useEffect(() => {
    if (!token) return;
    let active = true;
    setError('');
    window.localStorage.removeItem('frontend_react_workspace_oid');
    void Promise.all([
      getCurrentUser(),
      permissionApi.getMenuVisibility().catch((): Record<string, boolean> => ({})),
    ]).then(([user, visibility]) => {
      if (!active) return;
      setCachedWatermarkText(userWatermarkText(user.account, user.name));
      const canReadDocs = Boolean(user.is_platform_admin || user.is_super_admin) || visibility?.docs !== false;
      setSession?.({ token, user, canReadDocs });
    }).catch(error => {
      if (active) setError(error instanceof Error ? error.message : '无法加载工作空间');
    });
    return () => { active = false; };
  }, [token, attempt, setSession]);
  if (!token) return null;
  if (error) return <Alert className="pro-session-screen" type="error" title={error}
    action={<Button onClick={() => setAttempt(previous => previous + 1)}>重试</Button>} />;
  if (!session) return <Spin className="pro-session-screen" />;
  return <Watermark content={userWatermarkText(session.user.account, session.user.name)}>
    {chatPage ? children : <PageContainer pageHeaderRender={false} className="pro-workspace-page"><div className="pro-business-page">{children}</div></PageContainer>}
  </Watermark>;
}

export function SiderFooter() {
  const { session } = useSession();
  if (!session) return null;
  const { user } = session;
  const name = user.name || user.account || '未登录';
  return <div className="pro-sider-footer">
    <Avatar size={24} className="pro-sider-avatar">{(name || '?').slice(0, 1)}</Avatar>
    <span className="pro-sider-user" title={name}>{name}</span>
  </div>;
}

export function WorkspaceActions() {
  const { session, setSession } = useSession();
  if (!session) return null;
  const { user } = session;
  return <Space>
    {session.canReadDocs && <Tooltip title="系统文档">
      <Button type="text" aria-label="系统文档" icon={<FileTextOutlined />} onClick={() => history.push('/construct/docs')} />
    </Tooltip>}
    <Dropdown trigger={['click']} menu={{ items: [{ key: 'logout', label: '退出登录', onClick: () => {
      setCachedWatermarkText('');
      clearAuthSession();
      setSession?.(undefined);
      history.replace('/login');
    } }] }}>
      <Button type="text" className="pro-account" aria-label="账户菜单"
        icon={<Avatar size={26}>{(user.name || user.account).slice(0, 1)}</Avatar>}>
        <span className="pro-account-name">{user.name || user.account}</span>
      </Button>
    </Dropdown>
  </Space>;
}
