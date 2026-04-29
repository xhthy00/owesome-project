import { App as AntApp, ConfigProvider, theme } from "antd";
import type { AppProps } from "next/app";
import { useContext, useEffect, useMemo } from "react";
import { ChatContext, ChatContextProvider } from "@/app/chat-context";
import SideBar from "@/components/layout/side-bar";
import "../styles/globals.css";

const APP_MODE_KEY = "frontend_react_mode";

function LayoutWrapper({
  children,
  pathname
}: {
  children: React.ReactNode;
  pathname: string;
}) {
  const { mode, setMode } = useContext(ChatContext);
  const isBypassLayout =
    pathname.startsWith("/mobile") || pathname.startsWith("/share") || pathname === "/construct/app/extra";

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
            <div className="relative flex flex-1 flex-col overflow-hidden">{children}</div>
          </div>
        )}
      </AntApp>
    </ConfigProvider>
  );
}

export default function MyApp({ Component, pageProps, router }: AppProps) {
  return (
    <ChatContextProvider>
      <LayoutWrapper pathname={router.pathname}>
        <Component {...pageProps} />
      </LayoutWrapper>
    </ChatContextProvider>
  );
}
