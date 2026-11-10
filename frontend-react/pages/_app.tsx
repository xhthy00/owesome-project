import { ApartmentOutlined } from "@ant-design/icons";
import { App as AntApp, ConfigProvider, Select, Watermark, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import type { AppProps } from "next/app";
import { useRouter } from "next/router";
import { useContext, useEffect, useMemo, useState } from "react";
import { getCurrentUser } from "@/api/auth";
import { ChatContext, ChatContextProvider } from "@/app/chat-context";
import { permissionApi } from "@/api/permission";
import { systemApi } from "@/api/system";
import { clearAccessToken, clearAuthSession, getAccessToken } from "@/auth/session";
import SideBar from "@/components/layout/side-bar";
import UserAvatarMenu from "@/components/layout/user-menu";
import { setCachedWatermarkText, userWatermarkText } from "@/utils/userWatermark";
import "../styles/globals.css";

dayjs.locale("zh-cn");

const APP_MODE_KEY = "frontend_react_mode";
const WORKSPACE_OID_KEY = "frontend_react_workspace_oid";

function looksLikeSchoolToken(value: string): boolean {
  const tok = value.trim();
  if (!tok || /[\u4e00-\u9fff]/.test(tok)) return false;
  return /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(tok);
}

function schoolHeaderLabel(value: string): string {
  const text = value.trim();
  if (/^[A-Za-z]\d{2}/.test(text) && /[\u4e00-\u9fff]/.test(text)) {
    return text.slice(3).trim() || text;
  }
  return text;
}

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
  const [hideWorkspaceSwitcher, setHideWorkspaceSwitcher] = useState(false);
  const [loginAccount, setLoginAccount] = useState("");
  const [loginName, setLoginName] = useState("");
  /** 仅在用户切换工作空间时递增，用于强制主内容区 remount，刷新当前页状态与接口数据 */
  const [mainContentKey, setMainContentKey] = useState(0);
  const isBypassLayout =
    pathname === "/login" ||
    pathname.startsWith("/mobile") ||
    pathname.startsWith("/share") ||
    pathname === "/construct/app/extra";

  const onLogout = () => {
    setLoginAccount("");
    setLoginName("");
    setHideWorkspaceSwitcher(false);
    setCachedWatermarkText("");
    clearAuthSession();
    void router.replace("/login");
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
    void Promise.all([
      systemApi.listWorkspaces(),
      getCurrentUser(),
      permissionApi.getEduPrivacy().catch(() => ({ anonymize_display: true }))
    ])
      .then(([workspaces, user, privacy]) => {
        if (!active) return;
        const anonymize = privacy?.anonymize_display !== false;
        const scope = user.edu_scope || {};
        const schoolId = String(scope.school_id || "").trim();
        const schoolName = schoolHeaderLabel(String(scope.school_name || "").trim());
        const showSchoolPlain =
          scope.edu_role === "school_admin" &&
          !anonymize &&
          !!schoolName &&
          !looksLikeSchoolToken(schoolName);
        const opts = workspaces.map((w) => {
          const name = w.name || `工作空间 ${w.id}`;
          const matchSchool =
            showSchoolPlain &&
            schoolId &&
            name.trim().toLowerCase() === schoolId.toLowerCase();
          const onlySchoolWs =
            showSchoolPlain && workspaces.length === 1 && looksLikeSchoolToken(name);
          return {
            value: Number(w.id),
            label: matchSchool || onlySchoolWs ? schoolName : name
          };
        });
        setWorkspaceOptions(opts);
        const account = String(user.account || "").trim();
        const name = String(user.name || "").trim();
        setLoginAccount(account);
        setLoginName(name);
        setHideWorkspaceSwitcher(
          Boolean(user.is_platform_admin) || scope.edu_role === "bureau_admin"
        );
        setCachedWatermarkText(userWatermarkText(account, name));
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
        setLoginName("");
        setHideWorkspaceSwitcher(false);
        setCachedWatermarkText("");
      });
    return () => {
      active = false;
    };
  }, [isBypassLayout]);

  useEffect(() => {
    const onWs = (event: Event) => {
      const oid = Number((event as CustomEvent<{ oid?: number }>).detail?.oid);
      if (!Number.isFinite(oid)) return;
      setWorkspaceOid((prev) => (prev === oid ? prev : oid));
    };
    window.addEventListener("workspace:changed", onWs);
    return () => window.removeEventListener("workspace:changed", onWs);
  }, []);

  const watermarkText = useMemo(
    () => userWatermarkText(loginAccount, loginName),
    [loginAccount, loginName]
  );

  const themeConfig = useMemo(
    () => ({
      token: {
        colorPrimary: "#2563EB",
        borderRadius: 8
      },
      algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm
    }),
    [mode]
  );

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <AntApp>
        {isBypassLayout ? (
          children
        ) : (
          <Watermark
            inherit={false}
            content={watermarkText || undefined}
            gap={[200, 160]}
            rotate={-22}
            font={{
              fontSize: 20,
              color: mode === "dark" ? "rgba(255,255,255,0.18)" : "rgba(0,0,0,0.16)"
            }}
            className="h-screen w-screen"
          >
          <div className="flex h-full w-full overflow-hidden">
            <div className="hidden shrink-0 md:block">
              <SideBar />
            </div>
            <div className="relative flex flex-1 flex-col overflow-hidden">
              <div className="workspace-topbar flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 text-sm text-slate-700 md:h-10 dark:border-slate-700 dark:bg-[#111827]">
                {pathname === "/chat" ? <div id="chat-header-slot" className="min-w-0 flex-1" /> : <span className="text-sm font-medium">学情分析工作台</span>}
                <div className="workspace-account flex shrink-0 items-center gap-3 text-slate-500">
                  {workspaceOptions.length && !hideWorkspaceSwitcher ? (
                    <div className="flex h-9 items-center gap-2 rounded-full border border-[#d9e2f0] bg-[#f7faff] px-3 pr-2 shadow-sm dark:border-[#2f3a52] dark:bg-[#1a2130]">
                      <ApartmentOutlined className="text-[13px] text-[#4f6fa8] dark:text-[#8aa8de]" />
                      <Select
                        size="middle"
                        variant="borderless"
                        value={workspaceOid}
                        style={{ width: 220 }}
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
                  <UserAvatarMenu
                    account={loginAccount}
                    name={loginName}
                    onNameChange={(nextName) => {
                      setLoginName(nextName);
                      setCachedWatermarkText(userWatermarkText(loginAccount, nextName));
                    }}
                    onLogout={onLogout}
                  />
                </div>
              </div>
              <div key={mainContentKey} className="min-h-0 flex-1 overflow-hidden">
                {children}
              </div>
            </div>
          </div>
          </Watermark>
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
