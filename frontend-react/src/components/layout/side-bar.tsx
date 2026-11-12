import {
  DownOutlined,
  EditOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined
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
import {
  mainRoutes,
  permissionSubRoutes,
  resourceSubRoutes,
  systemSubRoutes,
  type NavItem
} from "./nav-config";

/** 一级菜单行:展开态与收起态共用同一套激活判定与样式常量 */
const ROW_BASE =
  "flex w-full items-center rounded-lg transition-colors duration-150 focus-visible:outline-none";
const ACTIVE_STYLE = "bg-brand-tint font-medium text-brand-ink";
const IDLE_STYLE = "text-ink-2 hover:bg-surface-2 hover:text-ink-1";

function matchesPath(pathname: string, path: string) {
  return pathname === path || pathname.startsWith(`${path}/`);
}

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
    let list = mainRoutes;
    if (!isPlatformAdmin) {
      list = mainRoutes.filter(
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

  const resourceActive = visibleResourceRoutes.some((item) => matchesPath(pathname, item.path));

  useEffect(() => {
    if (resourceActive) setResourceExpanded(true);
  }, [resourceActive]);

  const startTask = () => {
    window.dispatchEvent(new Event("chat:new-task"));
    if (pathname !== "/chat" || router.asPath !== "/chat") void router.push("/chat");
  };

  const isItemActive = (item: NavItem) =>
    item.key === "resource" ? resourceActive : matchesPath(pathname, item.path);

  const expandedFor = (item: NavItem) =>
    item.key === "resource" ? resourceExpanded : item.key === "permission" ? permissionExpanded : systemExpanded;

  const toggleGroup = (item: NavItem) => {
    if (item.key === "resource") setResourceExpanded((prev) => !prev);
    else if (item.key === "permission") setPermissionExpanded((prev) => !prev);
    else if (item.key === "system") setSystemExpanded((prev) => !prev);
    else void router.push(item.path);
  };

  const renderSubList = (items: NavItem[]) => (
    <div className="ml-11 mt-1 flex flex-col gap-0.5 border-l border-line-1 pl-2">
      {items.map((sub) => {
        const active = matchesPath(pathname, sub.path);
        return (
          <Link
            key={sub.key}
            href={sub.path}
            aria-current={active ? "page" : undefined}
            className={`flex h-9 items-center rounded-md px-3 text-navsub transition-colors ${
              active ? ACTIVE_STYLE : "text-ink-3 hover:bg-surface-2 hover:text-ink-1"
            }`}
          >
            {sub.label}
          </Link>
        );
      })}
    </div>
  );

  /* ---------- 收起态:只有图标 + 提示 ---------- */
  if (!isMenuExpand) {
    return (
      <nav
        aria-label="主导航"
        className="workspace-sidebar flex h-screen w-[64px] flex-col border-r border-line-1 bg-surface-0 pt-3 dark:bg-surface-1"
      >
        <div className="flex shrink-0 flex-col items-center pb-1">
          <Link href="/" aria-label="返回探索广场" className="flex items-center justify-center">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-surface-1 ring-1 ring-line-1 dark:bg-surface-2 dark:ring-line-2">
              <Image src="/logo-mark.svg" alt="" width={28} height={28} />
            </span>
          </Link>
          <Tooltip title="展开侧栏" placement="right">
            <button
              type="button"
              aria-label="展开侧栏"
              onClick={() => setIsMenuExpand(true)}
              className="mt-2 flex h-7 w-7 items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink-1"
            >
              <MenuUnfoldOutlined style={{ fontSize: 14 }} />
            </button>
          </Tooltip>
        </div>
        <Tooltip title="新建任务" placement="right">
          <button
            type="button"
            aria-label="新建任务"
            onClick={startTask}
            className="mx-auto my-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand text-white shadow-1 transition-colors hover:bg-brand-hover"
          >
            <PlusOutlined />
          </button>
        </Tooltip>
        <div className="scrollbar-hide mt-1 flex min-h-0 flex-1 flex-col items-center gap-2 overflow-y-auto overscroll-contain px-1 pb-2">
          {visibleRoutes.map((item) => {
            const active = isItemActive(item);
            const href = item.key === "resource" ? visibleResourceRoutes[0]?.path || item.path : item.path;
            return (
              <Tooltip key={item.key} title={item.label} placement="right">
                <Link
                  href={href}
                  aria-label={item.label}
                  aria-current={active ? "page" : undefined}
                  className={`flex h-11 w-11 items-center justify-center rounded-lg text-lg transition-colors ${
                    active ? ACTIVE_STYLE : IDLE_STYLE
                  }`}
                >
                  {item.icon}
                </Link>
              </Tooltip>
            );
          })}
        </div>
      </nav>
    );
  }

  /* ---------- 展开态 ---------- */
  return (
    <nav
      aria-label="主导航"
      className="workspace-sidebar flex h-screen w-[232px] min-w-[232px] flex-col border-r border-line-1 bg-surface-0 px-3 pt-3 dark:bg-surface-1"
    >
      <div className="flex shrink-0 items-center justify-between gap-1 px-2 pb-4 pt-1">
        <Link href="/" className="flex min-w-0 items-center gap-2.5" aria-label="AI学情分析智能体">
          <Image src="/logo-mark.svg" alt="" width={32} height={32} className="shrink-0 rounded-lg" />
          <span className="min-w-0">
            <span className="block whitespace-nowrap text-[15px] font-semibold text-ink-1">学情分析</span>
            <span className="block truncate whitespace-nowrap text-xs text-ink-3">AI 智能体工作台</span>
          </span>
        </Link>
        <Tooltip title="收起侧栏">
          <button
            type="button"
            aria-label="收起侧栏"
            onClick={() => setIsMenuExpand(false)}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink-1"
          >
            <MenuFoldOutlined style={{ fontSize: 14 }} />
          </button>
        </Tooltip>
      </div>

      <button
        type="button"
        className="mb-3 flex min-h-[40px] w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 text-sm font-medium text-white shadow-1 transition-colors hover:bg-brand-hover"
        onClick={startTask}
      >
        <PlusOutlined className="text-xs" />
        <span>新建任务</span>
      </button>

      <div className="scrollbar-hide flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain">
        <div className="flex flex-col gap-0.5">
          {visibleRoutes.map((item) => {
            const active = isItemActive(item);
            const expanded = expandedFor(item);
            return (
              <div key={item.key}>
                <button
                  type="button"
                  aria-expanded={item.group ? expanded : undefined}
                  aria-current={!item.group && active ? "page" : undefined}
                  onClick={() => toggleGroup(item)}
                  className={`${ROW_BASE} h-10 gap-3 px-3 text-nav ${active ? ACTIVE_STYLE : IDLE_STYLE}`}
                >
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center text-base">{item.icon}</span>
                  <span className="truncate">{item.label}</span>
                  {item.group ? (
                    <DownOutlined
                      className={`ml-auto text-[10px] transition-transform ${expanded ? "rotate-0" : "-rotate-90"}`}
                    />
                  ) : null}
                </button>
                {item.key === "resource" && resourceExpanded ? renderSubList(visibleResourceRoutes) : null}
                {item.key === "permission" && permissionExpanded ? renderSubList(permissionSubRoutes) : null}
                {item.key === "system" && systemExpanded ? renderSubList(systemSubRoutes) : null}
              </div>
            );
          })}
        </div>

        <div className="mb-1 mt-4 flex items-center justify-between px-2">
          <span className="text-xs font-medium text-ink-3">最近任务</span>
          <Link href="/conversations" aria-label="查看全部任务" className="inline-flex items-center p-1">
            <Tooltip title="查看全部">
              <RightOutlined className="text-[11px] text-ink-4 transition-colors hover:text-ink-2" />
            </Tooltip>
          </Link>
        </div>
        <div className="pb-2">
          {historyList.length ? (
            <div className="space-y-2 pb-2">
              {groupedHistory.map((group) => (
                <div key={group.key}>
                  <button
                    type="button"
                    onClick={() => setCollapsedGroups((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
                    aria-expanded={!collapsedGroups[group.key]}
                    className="flex w-full items-center gap-1 rounded px-2 pb-1 text-left"
                  >
                    <span className="text-xs font-medium text-ink-3">{group.label}</span>
                    <DownOutlined
                      className={`text-[10px] text-ink-4 transition-transform ${
                        collapsedGroups[group.key] ? "-rotate-90" : "rotate-0"
                      }`}
                    />
                  </button>
                  {!collapsedGroups[group.key] ? (
                    <div className="space-y-0.5">
                      {group.items.map((item) => {
                        const active = activeConversationId === item.id;
                        const editing = renamingId === item.id;
                        return (
                          <div
                            key={item.id}
                            className={`group relative rounded-lg transition-colors ${
                              active
                                ? "bg-brand-tint text-brand-ink"
                                : "text-ink-2 hover:bg-surface-2 hover:text-ink-1"
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
                                className="m-1 w-[calc(100%-8px)] rounded-md border border-brand-border bg-surface-1 px-2 py-1 text-navsub leading-5 text-ink-1 outline-none focus:ring-2 focus:ring-brand"
                              />
                            ) : (
                              <>
                                <Link
                                  href={{ pathname: "/chat", query: { conversation_id: item.id } }}
                                  className="block rounded-lg py-2 pl-3 pr-8"
                                  title={item.title || `对话 ${item.id}`}
                                  aria-current={active ? "page" : undefined}
                                >
                                  <div className="truncate text-navsub font-medium leading-5">
                                    {item.title || `对话 ${item.id}`}
                                  </div>
                                  <div className="mt-0.5 truncate text-xs text-ink-4">
                                    {item.update_time?.slice(0, 16).replace("T", " ") || "刚刚"}
                                  </div>
                                </Link>
                                <button
                                  type="button"
                                  title="重命名"
                                  aria-label="重命名任务"
                                  className="absolute right-1 top-2 hidden h-6 w-6 items-center justify-center rounded text-ink-3 hover:bg-surface-1 hover:text-ink-1 group-hover:flex"
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
              <div className="mb-2 text-ink-4">
                <MessageOutlined style={{ fontSize: 24 }} />
              </div>
              <p className="text-xs text-ink-3">暂无历史任务</p>
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-line-1 px-2 py-3 text-xs text-ink-3">AI 学情分析工作台</div>
    </nav>
  );
}
