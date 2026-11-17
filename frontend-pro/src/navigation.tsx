import { DashboardOutlined, MessageOutlined } from '@ant-design/icons';
import type { MenuDataItem } from '@ant-design/pro-components';
import { getCurrentUser } from '@/api/auth';
import { permissionApi } from '@/api/permission';
import { getAccessToken } from '@/auth/session';
import { mainRoutes, permissionSubRoutes, resourceSubRoutes, systemSubRoutes } from '@/components/layout/nav-config';
import { CHAT_MENU_TITLE } from '../config/defaultSettings';

export async function loadMenu(): Promise<MenuDataItem[]> {
  const token = getAccessToken();
  if (!token) return [];
  const [user, visibility] = await Promise.all([
    getCurrentUser(),
    permissionApi.getMenuVisibility().catch((): Record<string, boolean> => ({})),
  ]);
  if (getAccessToken() !== token) return [];
  const admin = Boolean(user.is_platform_admin || user.is_super_admin);
  const role = user.edu_scope?.edu_role;
  const allowed = (key: string) => admin || visibility?.[key] !== false;
  const canSeeScore = admin || role === 'bureau_admin' || role === 'school_admin';
  const resources = resourceSubRoutes.filter(item => allowed(item.key)
    && (item.key !== 'school' || admin)
    && (item.key !== 'raw-score-import' || canSeeScore)
    && !(role === 'student' && ['exam-library', 'fraction-bar'].includes(item.key)));
  const itemOf = ({ key, path, label, icon }: typeof mainRoutes[number]) => ({ key, path, name: label, icon });
  const insights = mainRoutes.filter(item => ['analysis', 'line-reach', 'report-history', 'anomaly-alerts', 'anomaly-rules'].includes(item.key))
    .filter(item => allowed(item.key)
      && (item.key !== 'anomaly-rules' || admin)
      && (item.key !== 'line-reach' || role !== 'student')
      && (item.key !== 'anomaly-alerts' || (role !== 'student' && role !== 'bureau_admin')));
  const menu: MenuDataItem[] = [
    ...(allowed('overview') && role !== 'student' ? [itemOf(mainRoutes[0])] : []),
    { key: 'insights', name: '分析与洞察', icon: <DashboardOutlined />, children: insights.map(itemOf) },
    ...(allowed('resource') && resources.length ? [{ key: 'resource', name: '数据管理', icon: mainRoutes.find(item => item.key === 'resource')?.icon, children: resources.map(itemOf) }] : []),
    ...(allowed('permission') ? [{ key: 'permission', name: '权限管理', icon: mainRoutes.find(item => item.key === 'permission')?.icon, children: permissionSubRoutes.map(itemOf) }] : []),
    ...(allowed('system') ? [{ key: 'system', name: '系统日志', icon: mainRoutes.find(item => item.key === 'system')?.icon, children: systemSubRoutes.map(itemOf) }] : []),
    { name: CHAT_MENU_TITLE, path: '/chat', icon: <MessageOutlined /> },
  ];
  return menu.filter(item => !item.children || item.children.length);
}
