import { BulbOutlined, MessageOutlined, ToolOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { Layout, Menu, Switch, Typography } from "antd";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/router";
import { ReactNode } from "react";

const { Sider, Header, Content } = Layout;

type Props = {
  children: ReactNode;
  darkMode: boolean;
  onToggleMode: (checked: boolean) => void;
};

const menus = [
  { key: "/chat", label: <Link href="/chat">Chat</Link>, icon: <MessageOutlined /> },
  { key: "/conversations", label: <Link href="/conversations">Conversations</Link>, icon: <UnorderedListOutlined /> },
  { key: "/construct", label: <Link href="/construct">Construct</Link>, icon: <ToolOutlined /> }
];

export default function MainShell({ children, darkMode, onToggleMode }: Props) {
  const router = useRouter();
  const selectedMenu = menus.find((item) => router.pathname.startsWith(item.key))?.key ?? "/chat";

  return (
    <Layout className="min-h-screen oc-page-bg">
      <Sider
        width={232}
        theme={darkMode ? "dark" : "light"}
        className="!bg-transparent !border-r !border-[var(--oc-border)] backdrop-blur"
      >
        <div className="px-4 py-5">
          <div className="mb-1 flex items-center gap-2">
            <Image src="/logo-mark.svg" alt="logo" width={24} height={24} />
            <Typography.Title level={4} className="!mb-0 !text-[var(--oc-text)]">
              启明
            </Typography.Title>
          </div>
          <Typography.Text className="oc-muted text-xs">Replica Workspace</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedMenu]}
          items={menus}
          className="!bg-transparent !border-none px-2"
        />
      </Sider>
      <Layout className="!bg-transparent">
        <Header className="flex items-center justify-end gap-3 !bg-transparent !px-6">
          <BulbOutlined className="oc-muted" />
          <span className="oc-muted text-sm">{darkMode ? "Dark" : "Light"}</span>
          <Switch checked={darkMode} onChange={onToggleMode} />
        </Header>
        <Content className="px-4 pb-4">{children}</Content>
      </Layout>
    </Layout>
  );
}
