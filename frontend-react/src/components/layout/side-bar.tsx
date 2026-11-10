import {
  AlertOutlined,
  DownOutlined,
  BarChartOutlined,
  ControlOutlined,
  FolderOutlined,
  FundViewOutlined,
  GlobalOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  EditOutlined,
  PlusOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { permissionApi } from "@/api/permission";
import { getCurrentUser, type CurrentUser } from "@/api/auth";
import { Tooltip } from "antd";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/router";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { ChatContext } from "@/app/chat-context";
import { Conversation, listConversations, updateConversation } from "@/api/adapter/chatAdapter";
import { CONVERSATION_CHANGED_EVENT } from "@/utils/conversationTitle";

const routes = [
  { key: "explore", path: "/", label: "探索广场", icon: <GlobalOutlined /> },
  { key: "analysis", path: "/construct/analysis", label: "分析工具", icon: <BarChartOutlined /> },
  { key: "line-reach", path: "/construct/education/line-reach", label: "达线看板", icon: <FundViewOutlined /> },
  { key: "report-history", path: "/construct/analysis/history", label: "报告历史", icon: <HistoryOutlined /> },
  { key: "anomaly-alerts", path: "/construct/education/anomaly-alerts", label: "异常提醒", icon: <AlertOutlined /> },
  { key: "anomaly-rules", path: "/construct/education/anomaly-rules", label: "异常规则", icon: <ControlOutlined /> },
  { key: "resource", path: "/construct/database", label: "数据管理", icon: <FolderOutlined /> },
  { key: "permission", path: "/construct/permission", label: "权限管理", icon: <SafetyCertificateOutlined /> },
  { key: "system", path: "/system", label: "日志管理", icon: <SettingOutlined /> },
  { key: "docs", path: "/construct/docs", label: "系统文档", icon: <FileTextOutlined /> },
];

const resourceSubRoutes = [
  { key: "datasource", path: "/construct/database", label: "数据源" },
  { key: "school", path: "/construct/education/schools", label: "学校" },
  { key: "exam-library", path: "/construct/education/exam-library", label: "试卷库" },
  { key: "raw-score-import", path: "/construct/education/score-library", label: "成绩库" },
  { key: "knowledge", path: "/construct/knowledge", label: "知识点" },
  { key: "fraction-bar", path: "/construct/education/fraction-bar", label: "预测分数线" }
];

const permissionSubRoutes = [
  { key: "permission-config", path: "/construct/permission/config", label: "权限配置" },
  { key: "permission-edu", path: "/construct/permission/edu", label: "教育权限" },
  { key: "permission-users", path: "/construct/permission/users", label: "用户管理" },
  { key: "permission-workspaces", path: "/construct/permission/workspaces", label: "工作空间" },
  { key: "permission-members", path: "/construct/permission/members", label: "成员管理" },
  { key: "permission-privacy", path: "/construct/permission/privacy", label: "数据脱敏" },
  { key: "permission-menu", path: "/construct/permission/menu", label: "菜单权限管理" }
];

const systemSubRoutes = [
  { key: "system-log-access", path: "/system/log/access", label: "访问日志" },
  { key: "system-log-operation", path: "/system/log/operation", label: "操作日志" },
  { key: "system-log-login", path: "/system/log/login", label: "登录日志" }
];

export default function SideBar() {
  const { isMenuExpand, setIsMenuExpand } = useContext(ChatContext);
  const router = useRouter();
  const pathname = router.pathname;
  const [historyList, setHistoryList] = useState<Conversation[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [permissionExpanded, setPermissionExpanded] = useState(false);
  const [resourceExpanded, setResourceExpanded] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [systemExpanded, setSystemExpanded] = useState(false);
  const [menuVisibility, setMenuVisibility] = useState<Record<string, boolean>>({});
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const skipRenameBlurRef = useRef(false);
  const isPlatformAdmin = useMemo(
    () => Boolean(currentUser?.is_platform_admin || currentUser?.is_super_admin),
    [currentUser]
  );

  useEffect(() => {
    let active = true;
    void getCurrentUser()
      .then((u) => {
        if (active) setCurrentUser(u);
      })
      .catch(() => {
        /* ignore */
      });
    void permissionApi
      .getMenuVisibility()
      .then((map) => {
        if (active) setMenuVisibility(map ?? {});
      })
      .catch(() => {
        /* ignore */
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    void listConversations(50).then((items) => {
      if (active) setHistoryList(items);
    });
    return () => {
      active = false;
    };
  }, [pathname]);

  useEffect(() => {
    const reloadHistory = () => {
      void listConversations(50).then((items) => setHistoryList(items));
    };
    window.addEventListener("workspace:changed", reloadHistory);
    window.addEventListener(CONVERSATION_CHANGED_EVENT, reloadHistory);
    return () => {
      window.removeEventListener("workspace:changed", reloadHistory);
      window.removeEventListener(CONVERSATION_CHANGED_EVENT, reloadHistory);
    };
  }, []);

  const activeConversationId = useMemo(() => {
    const raw = router.query.conversation_id;
    const id = Array.isArray(raw) ? raw[0] : raw;
    return id ? Number(id) : undefined;
  }, [router.query.conversation_id]);

  const startRename = (item: Conversation) => {
    skipRenameBlurRef.current = false;
    setRenamingId(item.id);
    setRenameDraft(item.title || "");
  };

  const commitRename = async (item: Conversation) => {
    if (skipRenameBlurRef.current) {
      skipRenameBlurRef.current = false;
      return;
    }
    const title = renameDraft.trim().slice(0, 64);
    setRenamingId(null);
    if (!title || title === (item.title || "")) return;
    try {
      const updated = await updateConversation(item.id, { title });
      setHistoryList((prev) => prev.map((c) => (c.id === item.id ? { ...c, title: updated.title } : c)));
    } catch {
      void listConversations(50).then((items) => setHistoryList(items));
    }
  };

  const cancelRename = () => {
    skipRenameBlurRef.current = true;
    setRenamingId(null);
  };

  const groupedHistory = useMemo(() => {
    const toDate = (value?: string) => {
      if (!value) return null;
      const d = new Date(value);
      return Number.isNaN(d.getTime()) ? null : d;
    };
    const toDateKey = (d: Date) => {
      const y = d.getFullYear();
      const m = `${d.getMonth() + 1}`.padStart(2, "0");
      const day = `${d.getDate()}`.padStart(2, "0");
      return `${y}-${m}-${day}`;
    };
    const now = new Date();
    const today = toDateKey(now);
    const yesterdayDate = new Date(now);
    yesterdayDate.setDate(now.getDate() - 1);
    const yesterday = toDateKey(yesterdayDate);

    const sorted = [...historyList].sort((a, b) => {
      const da = toDate(a.update_time || a.create_time);
      const db = toDate(b.update_time || b.create_time);
      if (da && db) return db.getTime() - da.getTime();
      return (b.id ?? 0) - (a.id ?? 0);
    });

    const groups = new Map<string, Conversation[]>();
    sorted.forEach((item) => {
      const d = toDate(item.update_time || item.create_time);
      const key = d ? toDateKey(d) : "unknown";
      const list = groups.get(key) ?? [];
      list.push(item);
      groups.set(key, list);
    });

    return Array.from(groups.entries()).map(([key, items]) => ({
      key,
      label: key === today ? "今天" : key === yesterday ? "昨天" : key === "unknown" ? "更早" : key,
      items
    }));
  }, [historyList]);

  useEffect(() => {
    setCollapsedGroups((prev) => {
      const next = { ...prev };
      groupedHistory.forEach((group) => {
        if (!(group.key in next)) {
          next[group.key] = !(group.label === "今天" || group.label === "昨天");
        }
      });
      return next;
    });
  }, [groupedHistory]);

  const visibleResourceRoutes = useMemo(() => {
    const eduRole = currentUser?.edu_scope?.edu_role || "";
    const canSeeScoreLibrary =
      isPlatformAdmin ||
      Boolean(currentUser?.is_super_admin) ||
      eduRole === "bureau_admin" ||
      eduRole === "school_admin";
    if (!isPlatformAdmin && menuVisibility.resource === false) return [];
    return resourceSubRoutes.filter((item) => {
      if (!isPlatformAdmin && menuVisibility[item.key] === false) return false;
      if (!canSeeScoreLibrary && item.key === "raw-score-import") return false;
      if (!isPlatformAdmin && item.key === "school") return false;
      if (eduRole === "student" && (item.key === "exam-library" || item.key === "fraction-bar")) {
        return false;
      }
      return true;
    });
  }, [
    isPlatformAdmin,
    menuVisibility,
    currentUser?.edu_scope?.edu_role,
    currentUser?.is_super_admin
  ]);

  const visibleRoutes = useMemo(() => {
    const eduRole = currentUser?.edu_scope?.edu_role || "";
    const hideAnomalyAlerts = eduRole === "bureau_admin" || eduRole === "student";
    let list = routes;
    if (!isPlatformAdmin) {
      list = routes.filter(
        (item) => item.key !== "anomaly-rules" && menuVisibility[item.key] !== false
      );
    }
    if (visibleResourceRoutes.length === 0) {
      list = list.filter((item) => item.key !== "resource");
    }
    if (hideAnomalyAlerts) {
      list = list.filter((item) => item.key !== "anomaly-alerts");
    }
    if (eduRole === "student") {
      list = list.filter((item) => item.key !== "line-reach");
    }
    return list;
  }, [
    isPlatformAdmin,
    menuVisibility,
    currentUser?.edu_scope?.edu_role,
    visibleResourceRoutes.length
  ]);

  const resourceActive = visibleResourceRoutes.some(
    (item) => pathname === item.path || pathname.startsWith(item.path + "/")
  );

  useEffect(() => {
    if (resourceActive) setResourceExpanded(true);
  }, [resourceActive]);

  const startTask = () => {
    window.dispatchEvent(new Event("chat:new-task"));
    if (pathname !== "/chat" || router.asPath !== "/chat") void router.push("/chat");
  };

  if (!isMenuExpand) {
    return (
      <div className="workspace-sidebar flex h-screen w-[64px] flex-col border-r border-slate-200 bg-slate-50 pt-4 text-slate-600 dark:border-slate-700 dark:bg-[#111827] dark:text-slate-300">
        <div className="flex shrink-0 flex-col items-center pb-2">
          <Link href="/" className="flex items-center justify-center pb-2">
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-white shadow-sm">
              <Image src="/logo-mark.svg" alt="logo" width={34} height={34} />
            </span>
          </Link>
          <Tooltip title="展开侧栏" placement="right">
            <button type="button" aria-label="展开侧栏"
              onClick={() => setIsMenuExpand(true)}
              className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
            >
              <MenuUnfoldOutlined style={{ fontSize: 14 }} />
            </button>
          </Tooltip>
        </div>
        <Tooltip title="新建任务" placement="right">
          <button type="button" aria-label="新建任务" onClick={startTask}
            className="mx-auto my-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white transition-colors hover:bg-blue-700">
            <PlusOutlined />
          </button>
        </Tooltip>
        <div className="mt-2 flex min-h-0 flex-1 flex-col items-center gap-4 overflow-y-auto overscroll-contain px-1 pb-2 [scrollbar-gutter:stable] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#9aa8c0]/70 [&::-webkit-scrollbar-track]:bg-transparent">
          {visibleRoutes.map((item) => {
            const active =
              item.key === "resource"
                ? resourceActive
                : item.key === "analysis"
                  ? pathname === "/construct/analysis"
                  : pathname === item.path || pathname.startsWith(item.path + "/");
            const href = item.key === "resource" ? visibleResourceRoutes[0]?.path || item.path : item.path;
            return (
              <Link key={item.key} href={href} className="flex h-12 shrink-0 items-center">
                <Tooltip title={item.label} placement="right">
                  <div
                    className={`mx-auto flex h-12 w-12 items-center justify-center rounded-xl text-xl transition-colors ${
                      active ? "bg-blue-50 text-blue-600 shadow-sm dark:bg-blue-900/20 dark:text-blue-400" : "hover:bg-blue-50/50 dark:hover:bg-blue-900/10"
                    }`}
                  >
                    {item.icon}
                  </div>
                </Tooltip>
              </Link>
            );
          })}
        </div>
        <div className="shrink-0 py-4">
          <Tooltip title="展开侧栏" placement="right">
            <button type="button" aria-label="展开侧栏"
              className="mx-auto flex h-12 w-12 cursor-pointer items-center justify-center rounded-xl text-xl transition-colors hover:bg-blue-50/50 dark:hover:bg-blue-900/10"
              onClick={() => setIsMenuExpand(true)}
            >
              <MenuUnfoldOutlined />
            </button>
          </Tooltip>
        </div>
      </div>
    );
  }

  return (
    <div className="workspace-sidebar flex h-screen w-[232px] min-w-[232px] flex-col border-r border-slate-200 bg-slate-50 px-3 pt-3 dark:border-slate-700 dark:bg-[#111827]">
      <div className="dbgpt-ui-font flex shrink-0 items-center justify-between gap-1 px-2 pb-5 pt-1">
        <Link href="/" className="flex min-w-0 items-center gap-2.5" aria-label="AI学情分析智能体">
          <Image src="/logo-mark.svg" alt="" width={32} height={32} className="shrink-0 rounded-lg" />
          <span className="min-w-0"><span className="block whitespace-nowrap text-[15px] font-semibold text-slate-900 dark:text-white">学情分析</span><span className="block text-[11px] text-slate-500 dark:text-slate-400">AI 智能体工作台</span></span>
        </Link>
        <Tooltip title="收起侧栏">
          <button type="button" aria-label="收起侧栏"
            onClick={() => setIsMenuExpand(false)}
            className="flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-slate-700 dark:hover:text-slate-200"
          >
            <MenuFoldOutlined style={{ fontSize: 14 }} />
          </button>
        </Tooltip>
      </div>

      <button
        type="button"
        className="w-full shrink-0 border-0 bg-transparent p-0"
        onClick={startTask}
      >
        <div className="dbgpt-ui-font mb-4 flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 dark:bg-blue-600 dark:text-white dark:hover:bg-blue-500">
          <PlusOutlined className="text-xs" />
          <span>新建任务</span>
        </div>
      </button>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain [scrollbar-gutter:stable] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#9aa8c0]/70 dark:[&::-webkit-scrollbar-thumb]:bg-[#5a6578] [&::-webkit-scrollbar-track]:bg-transparent">
        <div className="flex flex-col gap-1">
          {visibleRoutes.map((item) => {
            const isResourceRoot = item.key === "resource";
            const active =
              isResourceRoot
                ? resourceActive
                : item.key === "analysis"
                  ? pathname === "/construct/analysis"
                  : pathname === item.path || pathname.startsWith(item.path + "/");
            const isPermissionRoot = item.key === "permission";
            const isSystemRoot = item.key === "system";
            const expanded = isResourceRoot
              ? resourceExpanded
              : isPermissionRoot
                ? permissionExpanded
                : systemExpanded;
            return (
              <div key={item.key}>
                <button
                  type="button"
                  onClick={() => {
                    if (isResourceRoot) {
                      setResourceExpanded((prev) => !prev);
                    } else if (isPermissionRoot) {
                      setPermissionExpanded((prev) => !prev);
                    } else if (isSystemRoot) {
                      setSystemExpanded((prev) => !prev);
                    } else {
                      void router.push(item.path);
                    }
                  }}
                  className={`flex h-11 w-full items-center rounded-lg px-3 transition-colors hover:bg-slate-100 dark:hover:bg-slate-800 ${
                    active ? "rounded-xl bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400" : "text-[#2e3a52] dark:text-gray-300"
                  }`}
                >
                  <div className="mr-3">{item.icon}</div>
                  <span className="dbgpt-ui-font text-sm">{item.label}</span>
                  {isResourceRoot || isPermissionRoot || isSystemRoot ? (
                    <DownOutlined
                      className={`ml-auto text-xs transition-transform ${expanded ? "rotate-0" : "-rotate-90"}`}
                    />
                  ) : null}
                </button>
                {isResourceRoot && resourceExpanded ? (
                  <div className="ml-11 mt-1 flex flex-col gap-1">
                    {visibleResourceRoutes.map((sub) => {
                      const subActive = pathname === sub.path || pathname.startsWith(sub.path + "/");
                      return (
                        <Link
                          key={sub.key}
                          href={sub.path}
                          className={`flex h-10 items-center rounded-lg px-3 text-sm transition-colors ${
                            subActive
                              ? "bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400"
                              : "text-[#3d4a64] hover:bg-blue-50/50 dark:text-gray-400 dark:hover:bg-blue-900/10"
                          }`}
                        >
                          {sub.label}
                        </Link>
                      );
                    })}
                  </div>
                ) : null}
                {isPermissionRoot && permissionExpanded ? (
                  <div className="ml-11 mt-1 flex flex-col gap-1">
                    {permissionSubRoutes.map((sub) => {
                      const subActive = pathname === sub.path || pathname.startsWith(sub.path + "/");
                      return (
                        <Link
                          key={sub.key}
                          href={sub.path}
                          className={`flex h-10 items-center rounded-lg px-3 text-sm transition-colors ${
                            subActive
                              ? "bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400"
                              : "text-[#3d4a64] hover:bg-blue-50/50 dark:text-gray-400 dark:hover:bg-blue-900/10"
                          }`}
                        >
                          {sub.label}
                        </Link>
                      );
                    })}
                  </div>
                ) : null}
                {isSystemRoot && systemExpanded ? (
                  <div className="ml-11 mt-1 flex flex-col gap-1">
                    {systemSubRoutes.map((sub) => {
                      const subActive = pathname === sub.path || pathname.startsWith(sub.path + "/");
                      return (
                        <Link
                          key={sub.key}
                          href={sub.path}
                          className={`flex h-10 items-center rounded-lg px-3 text-sm transition-colors ${
                            subActive
                              ? "bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400"
                              : "text-[#3d4a64] hover:bg-blue-50/50 dark:text-gray-400 dark:hover:bg-blue-900/10"
                          }`}
                        >
                          {sub.label}
                        </Link>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>

        <div className="mb-2 mt-4 px-1">
          <div className="flex items-center justify-between">
            <span className="dbgpt-ui-font text-xs font-medium text-slate-500 dark:text-slate-400">最近任务</span>
            <Link href="/conversations" className="inline-flex items-center">
              <Tooltip title="查看全部">
                <RightOutlined className="cursor-pointer text-xs text-gray-400 transition-colors hover:text-gray-600 dark:hover:text-gray-300" />
              </Tooltip>
            </Link>
          </div>
        </div>
        <div className="pb-2">
          {historyList.length ? (
            <div className="space-y-2 px-1 pb-2">
              {groupedHistory.map((group) => (
                <div key={group.key}>
                  <button
                    onClick={() => setCollapsedGroups((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
                    className="flex w-full items-center px-2 pb-1 text-left"
                  >
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-[#98a2b3]">{group.label}</span>
                    <DownOutlined
                      className={`ml-1 text-[10px] text-[#98a2b3] transition-transform ${
                        collapsedGroups[group.key] ? "-rotate-90" : "rotate-0"
                      }`}
                    />
                  </button>
                  {!collapsedGroups[group.key] ? (
                    <div className="space-y-1">
                      {group.items.map((item) => {
                        const active = activeConversationId === item.id;
                        const editing = renamingId === item.id;
                        return (
                          <div
                            key={item.id}
                            className={`group relative rounded-lg transition ${
                              active
                                ? "border border-[#dbeafe] bg-[#f5f9ff] text-[#1d4ed8] shadow-[0_1px_2px_rgba(15,23,42,0.04)] dark:border-[#2f4b75] dark:bg-[#1d2940] dark:text-[#93c5fd]"
                                : "border border-transparent hover:bg-white/80 dark:hover:bg-[#2a3040]"
                            }`}
                          >
                            {editing ? (
                              <input
                                autoFocus
                                value={renameDraft}
                                maxLength={64}
                                onChange={(e) => setRenameDraft(e.target.value)}
                                onBlur={() => {
                                  void commitRename(item);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") {
                                    e.preventDefault();
                                    (e.target as HTMLInputElement).blur();
                                  } else if (e.key === "Escape") {
                                    e.preventDefault();
                                    cancelRename();
                                  }
                                }}
                                className="m-1 w-[calc(100%-8px)] rounded border border-[#93c5fd] bg-white px-2 py-1 text-[13px] leading-5 text-[#1c2533] outline-none dark:border-[#2f4b75] dark:bg-[#1d2940] dark:text-[#93c5fd]"
                              />
                            ) : (
                              <>
                                <Link
                                  href={{ pathname: "/chat", query: { conversation_id: item.id } }}
                                  className="block rounded-lg py-2 pl-3 pr-8"
                                  title={item.title || `对话 ${item.id}`}
                                >
                                  <div className="truncate text-[13px] font-medium leading-5">
                                    {item.title || `对话 ${item.id}`}
                                  </div>
                                  <div className="mt-0.5 truncate text-[10px] text-[#98a2b3]">
                                    {item.update_time?.slice(0, 16).replace("T", " ") || "刚刚"}
                                  </div>
                                </Link>
                                <button
                                  type="button"
                                  title="重命名"
                                  className="absolute right-1 top-2 hidden h-6 w-6 items-center justify-center rounded text-[#98a2b3] hover:bg-white hover:text-[#3d4a64] group-hover:flex dark:hover:bg-[#34384a] dark:hover:text-gray-200"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    startRename(item);
                                  }}
                                >
                                  <EditOutlined className="text-xs" />
                                </button>
                              </>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="px-3 py-8 text-center">
              <div className="mb-2 text-gray-300 dark:text-gray-600">
                <MessageOutlined style={{ fontSize: 24 }} />
              </div>
              <p className="dbgpt-ui-font text-xs text-gray-400">暂无历史任务</p>
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-slate-200 px-2 py-3 text-[11px] text-slate-500 dark:border-slate-700 dark:text-slate-400">AI 学情分析工作台</div>
    </div>
  );
}
