import {
  AlertOutlined,
  BarChartOutlined,
  ControlOutlined,
  FileTextOutlined,
  FolderOutlined,
  FundViewOutlined,
  GlobalOutlined,
  HistoryOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  SettingOutlined
} from "@ant-design/icons";
import type { ReactNode } from "react";

export type NavItem = {
  key: string;
  path: string;
  label: string;
  icon: ReactNode;
  /** 分组型入口:点击只展开子菜单,不跳转 */
  group?: boolean;
};

/**
 * 导航与页面标题的唯一数据源。
 * 侧栏菜单和顶栏面包屑都从这里取,避免出现「菜单叫 A、顶栏叫 B」。
 */
export const mainRoutes: NavItem[] = [
  { key: "explore", path: "/", label: "探索广场", icon: <GlobalOutlined /> },
  { key: "analysis", path: "/construct/analysis", label: "分析工具", icon: <BarChartOutlined /> },
  {
    key: "line-reach",
    path: "/construct/education/line-reach",
    label: "达线看板",
    icon: <FundViewOutlined />
  },
  {
    key: "report-history",
    path: "/construct/analysis/history",
    label: "报告历史",
    icon: <HistoryOutlined />
  },
  {
    key: "anomaly-alerts",
    path: "/construct/education/anomaly-alerts",
    label: "异常提醒",
    icon: <AlertOutlined />
  },
  {
    key: "anomaly-rules",
    path: "/construct/education/anomaly-rules",
    label: "异常规则",
    icon: <ControlOutlined />
  },
  { key: "resource", path: "/construct/database", label: "数据管理", icon: <FolderOutlined />, group: true },
  {
    key: "permission",
    path: "/construct/permission",
    label: "权限管理",
    icon: <SafetyCertificateOutlined />,
    group: true
  },
  { key: "system", path: "/system", label: "日志管理", icon: <SettingOutlined />, group: true },
  { key: "docs", path: "/construct/docs", label: "系统文档", icon: <FileTextOutlined /> }
];

export const resourceSubRoutes: NavItem[] = [
  { key: "datasource", path: "/construct/database", label: "数据源", icon: null },
  { key: "school", path: "/construct/education/schools", label: "学校", icon: null },
  { key: "exam-library", path: "/construct/education/exam-library", label: "试卷库", icon: null },
  { key: "raw-score-import", path: "/construct/education/score-library", label: "成绩库", icon: null },
  { key: "knowledge", path: "/construct/knowledge", label: "知识点", icon: null },
  { key: "fraction-bar", path: "/construct/education/fraction-bar", label: "预测分数线", icon: null }
];

export const permissionSubRoutes: NavItem[] = [
  { key: "permission-config", path: "/construct/permission/config", label: "权限配置", icon: null },
  { key: "permission-edu", path: "/construct/permission/edu", label: "教育权限", icon: null },
  { key: "permission-users", path: "/construct/permission/users", label: "用户管理", icon: null },
  { key: "permission-workspaces", path: "/construct/permission/workspaces", label: "工作空间", icon: null },
  { key: "permission-members", path: "/construct/permission/members", label: "成员管理", icon: null },
  { key: "permission-privacy", path: "/construct/permission/privacy", label: "数据脱敏", icon: null },
  { key: "permission-menu", path: "/construct/permission/menu", label: "菜单权限管理", icon: null }
];

export const systemSubRoutes: NavItem[] = [
  { key: "system-log-access", path: "/system/log/access", label: "访问日志", icon: null },
  { key: "system-log-operation", path: "/system/log/operation", label: "操作日志", icon: null },
  { key: "system-log-login", path: "/system/log/login", label: "登录日志", icon: null }
];

/** 不在侧栏出现、但需要正确标题的页面 */
const extraRoutes: NavItem[] = [
  { key: "chat", path: "/chat", label: "AI 对话", icon: <MessageOutlined /> },
  { key: "conversations", path: "/conversations", label: "全部任务", icon: null },
  { key: "construct", path: "/construct", label: "数据构建", icon: null }
];

const allRoutes = [
  ...mainRoutes,
  ...resourceSubRoutes,
  ...permissionSubRoutes,
  ...systemSubRoutes,
  ...extraRoutes
];

export function pageTitleFor(pathname: string): string {
  if (pathname === "/") return "探索广场";
  const hit = allRoutes
    .filter((item) => item.path !== "/")
    .filter((item) => pathname === item.path || pathname.startsWith(`${item.path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0];
  return hit?.label ?? "学情分析工作台";
}
