import { ApartmentOutlined, LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { App as AntApp, ConfigProvider, Dropdown, Select, theme } from "antd";
import type { MenuProps } from "antd";
import type { AppProps } from "next/app";
import { useRouter } from "next/router";
import { useContext, useEffect, useMemo, useState } from "react";
import { getCurrentUser } from "@/api/auth";
import { ChatContext, ChatContextProvider } from "@/app/chat-context";
import { systemApi } from "@/api/system";
import { clearAccessToken, getAccessToken } from "@/auth/session";
import SideBar from "@/components/layout/side-bar";
import "../styles/globals.css";

const APP_MODE_KEY = "frontend_react_mode";
const WORKSPACE_OID_KEY = "frontend_react_workspace_oid";

function LayoutWrapper({
  children,
  pathname
}: {
  children: React.ReactNode;
  pathname: string;
}) {
  const { mode, setMode } = useContext(ChatContext);
  const router = useRouter();
  const [workspaceOptions, setWorkspaceOptions] = useState<Array<{ value: number; label: string }>>([]);
  const [workspaceOid, setWorkspaceOid] = useState<number | undefined>(undefined);
  const [loginAccount, setLoginAccount] = useState("");
  /** 仅在用户切换工作空间时递增，用于强制主内容区 remount，刷新当前页状态与接口数据 */
  const [mainContentKey, setMainContentKey] = useState(0);
  const isBypassLayout =
    pathname === "/login" ||
    pathname.startsWith("/mobile") ||
    pathname.startsWith("/share") ||
    pathname === "/construct/app/extra";
  const userMenuItems: MenuProps["items"] = [
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录"
    }
  ];

  const onUserMenuClick: MenuProps["onClick"] = ({ key }) => {
    if (key !== "logout") return;
    clearAccessToken();
    const redirect = encodeURIComponent(router.asPath || "/");
    void router.replace(`/login?redirect=${redirect}`);
  };

  useEffect(() => {
    const savedMode = window.localStorage.getItem(APP_MODE_KEY);
    if (savedMode === "dark" || savedMode === "light") {
      setMode(savedMode);
    }
  }, [setMode]);

  useEffect(() => {
    document.body.classList.toggle("dark", mode === "dark");
    document.body.classList.toggle("light", mode !== "dark");
    window.localStorage.setItem(APP_MODE_KEY, mode);
  }, [mode]);

  useEffect(() => {
    if (isBypassLayout) return;
    let active = true;
    void Promise.all([systemApi.listWorkspaces(), getCurrentUser()])
      .then(([workspaces, user]) => {
        if (!active) return;
        const opts = workspaces.map((w) => ({ value: Number(w.id), label: w.name || `工作空间 ${w.id}` }));
        setWorkspaceOptions(opts);
        setLoginAccount(String(user.account || "").trim());
        const fromStorage = Number(window.localStorage.getItem(WORKSPACE_OID_KEY) || "");
        const hasStorage = Number.isFinite(fromStorage) && opts.some((o) => o.value === fromStorage);
        const preferred = hasStorage ? fromStorage : Number(user.oid);
        const fallback = opts[0]?.value;
        const resolved = opts.some((o) => o.value === preferred) ? preferred : fallback;
        setWorkspaceOid(resolved);
        if (resolved != null) {
          window.localStorage.setItem(WORKSPACE_OID_KEY, String(resolved));
          window.dispatchEvent(new CustomEvent("workspace:changed", { detail: { oid: resolved } }));
        }
      })
      .catch(() => {
        if (!active) return;
        setWorkspaceOptions([]);
        setLoginAccount("");
      });
    return () => {
      active = false;
    };
  }, [isBypassLayout]);

  const themeConfig = useMemo(
    () => ({
      token: {
        colorPrimary: "#0C75FC",
        borderRadius: 4
      },
      algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm
    }),
    [mode]
  );

  return (
    <ConfigProvider theme={themeConfig}>
      <AntApp>
        {isBypassLayout ? (
          children
        ) : (
          <div className="flex h-screen w-screen overflow-hidden">
            <div className="hidden shrink-0 md:block">
              <SideBar />
            </div>
            <div className="relative flex flex-1 flex-col overflow-hidden">
              <div className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white/80 px-8 text-sm text-[#3a465d] backdrop-blur dark:border-gray-800 dark:bg-[#111217]/80">
                <span />
                <div className="flex items-center gap-4 text-[#7d8ba2]">
                  {workspaceOptions.length ? (
                    <div className="flex h-9 items-center gap-2 rounded-full border border-[#d9e2f0] bg-[#f7faff] px-3 pr-2 shadow-sm dark:border-[#2f3a52] dark:bg-[#1a2130]">
                      <ApartmentOutlined className="text-[13px] text-[#4f6fa8] dark:text-[#8aa8de]" />
                      <Select
                        size="middle"
                        variant="borderless"
                        value={workspaceOid}
                        style={{ width: 190 }}
                        options={workspaceOptions}
                        placeholder="选择工作空间"
                        popupMatchSelectWidth={240}
                        onChange={(value) => {
                          setWorkspaceOid(value);
                          window.localStorage.setItem(WORKSPACE_OID_KEY, String(value));
                          window.dispatchEvent(new CustomEvent("workspace:changed", { detail: { oid: value } }));
                          setMainContentKey((k) => k + 1);
                        }}
                      />
                    </div>
                  ) : null}
                  <Dropdown menu={{ items: userMenuItems, onClick: onUserMenuClick }} trigger={["click"]} placement="bottomRight">
                    <button
                      type="button"
                      className="flex h-9 items-center gap-2 rounded-full border border-[#d9e2f0] bg-[#f7faff] pl-1.5 pr-3 text-[#3a465d] shadow-sm transition-colors hover:bg-[#eef4ff] dark:border-[#2f3a52] dark:bg-[#1a2130] dark:text-gray-200 dark:hover:bg-[#222b3d]"
                      aria-label="用户菜单"
                    >
                      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-500 text-[15px] text-white">
                        <UserOutlined />
                      </span>
                      {loginAccount ? (
                        <span className="max-w-[140px] truncate text-sm font-medium">{loginAccount}</span>
                      ) : null}
                    </button>
                  </Dropdown>
                </div>
              </div>
              <div key={mainContentKey} className="min-h-0 flex-1 overflow-hidden">
                {children}
              </div>
            </div>
          </div>
        )}
      </AntApp>
    </ConfigProvider>
  );
}

export default function MyApp({ Component, pageProps, router }: AppProps) {
  useEffect(() => {
    if (!router.isReady) return;

    const path = router.pathname;
    const isPublicPage = path === "/login" || path.startsWith("/share") || path.startsWith("/mobile");
    const token = getAccessToken();

    if (!token && !isPublicPage) {
      void router.replace(`/login?redirect=${encodeURIComponent(router.asPath)}`);
      return;
    }

    if (token && path === "/login") {
      void router.replace("/");
      return;
    }

    if (!token || isPublicPage) return;

    void getCurrentUser().catch(() => {
      clearAccessToken();
      void router.replace(`/login?redirect=${encodeURIComponent(router.asPath)}`);
    });
  }, [router]);

  return (
    <ChatContextProvider>
      <LayoutWrapper pathname={router.pathname}>
        <Component {...pageProps} />
      </LayoutWrapper>
    </ChatContextProvider>
  );
}
