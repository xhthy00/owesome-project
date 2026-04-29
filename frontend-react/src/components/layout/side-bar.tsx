import {
  ApartmentOutlined,
  DownOutlined,
  DatabaseOutlined,
  GlobalOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
  ReadOutlined,
  RightOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { Tooltip } from "antd";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/router";
import { useContext, useEffect, useMemo, useState } from "react";
import { ChatContext } from "@/app/chat-context";
import { Conversation, listConversations } from "@/api/adapter/chatAdapter";

const routes = [
  { key: "explore", path: "/", label: "探索广场", icon: <GlobalOutlined /> },
  { key: "skills", path: "/construct/skills", label: "技能", icon: <ThunderboltOutlined /> },
  { key: "datasource", path: "/construct/database", label: "数据源", icon: <DatabaseOutlined /> },
  { key: "knowledge", path: "/construct/knowledge", label: "知识库", icon: <ReadOutlined /> },
  { key: "app", path: "/construct/app", label: "应用管理", icon: <ApartmentOutlined /> }
];

export default function SideBar() {
  const { isMenuExpand, setIsMenuExpand } = useContext(ChatContext);
  const router = useRouter();
  const pathname = router.pathname;
  const [historyList, setHistoryList] = useState<Conversation[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let active = true;
    void listConversations(50).then((items) => {
      if (active) setHistoryList(items);
    });
    return () => {
      active = false;
    };
  }, [pathname]);

  const activeConversationId = useMemo(() => {
    const raw = router.query.conversation_id;
    const id = Array.isArray(raw) ? raw[0] : raw;
    return id ? Number(id) : undefined;
  }, [router.query.conversation_id]);

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

  if (!isMenuExpand) {
    return (
      <div className="flex h-screen flex-col justify-between bg-[#d7dfed] pt-4 dark:bg-[#232734]">
        <div>
          <div className="flex flex-col items-center pb-2">
            <Link href="/" className="flex items-center justify-center pb-2">
              <span className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-white shadow-sm">
                <Image src="/logo-mark.svg" alt="logo" width={34} height={34} />
              </span>
            </Link>
            <Tooltip title="展开侧栏" placement="right">
              <div
                onClick={() => setIsMenuExpand(true)}
                className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
              >
                <MenuUnfoldOutlined style={{ fontSize: 14 }} />
              </div>
            </Tooltip>
          </div>
          <div className="mt-2 flex flex-col items-center gap-4">
            {routes.map((item) => {
              const active = pathname === item.path || pathname.startsWith(item.path + "/");
              return (
                <Link key={item.key} href={item.path} className="h-12 flex items-center">
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
        </div>
        <div className="py-4">
          <Tooltip title="展开侧栏" placement="right">
            <div
              className="mx-auto flex h-12 w-12 cursor-pointer items-center justify-center rounded-xl text-xl transition-colors hover:bg-blue-50/50 dark:hover:bg-blue-900/10"
              onClick={() => setIsMenuExpand(true)}
            >
              <MenuUnfoldOutlined />
            </div>
          </Tooltip>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-[240px] min-w-[240px] flex-col border-r border-[#cfd8e8] bg-[#d7dfed] px-4 pt-4 dark:border-[#34384a] dark:bg-[#232734]">
      <div className="dbgpt-ui-font flex items-center justify-between p-2 pb-4">
        <Link href="/" className="flex items-center gap-2">
          <Image src="/logo-mark.svg" alt="logo" width={40} height={40} className="rounded-md" />
          <span className="text-sm font-semibold tracking-[0.03em] text-[#243149] dark:text-white">启明AI</span>
        </Link>
        <Tooltip title="收起侧栏">
          <div
            onClick={() => setIsMenuExpand(false)}
            className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
          >
            <MenuFoldOutlined style={{ fontSize: 14 }} />
          </div>
        </Tooltip>
      </div>

      <Link href="/chat">
        <div className="dbgpt-ui-font mb-4 flex cursor-pointer items-center justify-center gap-2 rounded-xl bg-black px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 dark:bg-white dark:text-black">
          <PlusOutlined className="text-xs" />
          <span>新建任务</span>
        </div>
      </Link>

      <div className="flex flex-col gap-1">
        {routes.map((item) => {
          const active = pathname === item.path || pathname.startsWith(item.path + "/");
          return (
            <Link
              href={item.path}
              className={`flex h-12 w-full items-center px-4 transition-colors hover:rounded-xl hover:bg-blue-50/50 dark:hover:bg-blue-900/10 ${
                active ? "rounded-xl bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400" : "text-[#2e3a52] dark:text-gray-300"
              }`}
              key={item.key}
            >
              <div className="mr-3">{item.icon}</div>
              <span className="dbgpt-ui-font text-sm">{item.label}</span>
            </Link>
          );
        })}
      </div>

      <div className="mb-2 mt-4 px-1">
        <div className="flex items-center justify-between">
          <span className="dbgpt-ui-font text-xs font-semibold uppercase tracking-wider text-gray-400">所有任务</span>
          <Link href="/conversations" className="inline-flex items-center">
            <Tooltip title="查看全部">
              <RightOutlined className="cursor-pointer text-xs text-gray-400 transition-colors hover:text-gray-600 dark:hover:text-gray-300" />
            </Tooltip>
          </Link>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-hide">
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
                      return (
                        <Link
                          key={item.id}
                          href={{ pathname: "/chat", query: { conversation_id: item.id } }}
                          className={`block rounded-lg px-3 py-2 transition ${
                            active
                              ? "border border-[#dbeafe] bg-[#f5f9ff] text-[#1d4ed8] shadow-[0_1px_2px_rgba(15,23,42,0.04)] dark:border-[#2f4b75] dark:bg-[#1d2940] dark:text-[#93c5fd]"
                              : "border border-transparent hover:bg-white/80 dark:hover:bg-[#2a3040]"
                          }`}
                        >
                          <div className="truncate text-[13px] font-medium leading-5">{item.title || `对话 ${item.id}`}</div>
                          <div className="mt-0.5 truncate text-[10px] text-[#98a2b3]">{item.update_time?.slice(0, 16) || "刚刚"}</div>
                        </Link>
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

      <div className="pb-2 pt-4">
        <div className="mt-2 flex items-center justify-around border-t border-dashed border-gray-200 py-4 dark:border-gray-700">
          <div className="flex-1 cursor-pointer text-center text-xl text-[#52617d] dark:text-gray-300" onClick={() => setIsMenuExpand(false)}>
            <MenuFoldOutlined />
          </div>
        </div>
      </div>
    </div>
  );
}
