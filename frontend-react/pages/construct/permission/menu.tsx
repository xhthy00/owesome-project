import { ReloadOutlined, RightOutlined } from "@ant-design/icons";
import { Switch, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { permissionApi, type MenuVisibilityMap } from "@/api/permission";

const routes = [
  { key: "explore", label: "探索广场" },
  { key: "skills", label: "技能" },
  { key: "analysis", label: "分析工具" },
  { key: "line-reach", label: "达线看板" },
  { key: "report-history", label: "报告历史" },
  { key: "datasource", label: "数据源" },
  { key: "score-import", label: "成绩导入" },
  { key: "raw-score-import", label: "原始成绩导入" },
  { key: "fraction-bar", label: "预测分数线" },
  { key: "permission", label: "权限管理" },
  { key: "system", label: "日志管理" }
];

const permissionSubRoutes = [
  { key: "permission-config", label: "权限配置" },
  { key: "permission-edu", label: "教育权限" },
  { key: "permission-users", label: "用户管理" },
  { key: "permission-workspaces", label: "工作空间" },
  { key: "permission-members", label: "成员管理" },
  { key: "permission-privacy", label: "数据脱敏" },
  { key: "permission-menu", label: "菜单权限管理" }
];

const systemSubRoutes = [
  { key: "system-log-access", label: "访问日志" },
  { key: "system-log-operation", label: "操作日志" },
  { key: "system-log-login", label: "登录日志" }
];

const subRouteMap: Record<string, Array<{ key: string; label: string }>> = {
  permission: permissionSubRoutes,
  system: systemSubRoutes
};

type MenuRow = {
  key: string;
  label: string;
  hasChildren: boolean;
};

export default function ConstructPermissionMenuPage() {
  const [loading, setLoading] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<MenuVisibilityMap>({});
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());

  const menuTree: MenuRow[] = useMemo(
    () =>
      routes.map((route) => ({
        key: route.key,
        label: route.label,
        hasChildren: (subRouteMap[route.key]?.length ?? 0) > 0
      })),
    []
  );

  const load = async () => {
    setLoading(true);
    try {
      const res = await permissionApi.getMenuVisibility();
      setVisibility(res ?? {});
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载菜单可见性失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleToggle = async (menuKey: string, visible: boolean) => {
    setSavingKey(menuKey);
    try {
      await permissionApi.setMenuVisibility(menuKey, visible);
      setVisibility((prev) => ({ ...prev, [menuKey]: visible }));
      message.success("保存成功");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSavingKey(null);
    }
  };

  const toggleExpand = (key: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            菜单权限管理
          </Typography.Title>
          <Typography.Text className="oc-muted">
            控制系统管理员以外的用户是否可在侧边栏看到对应菜单
          </Typography.Text>
        </div>
        <Typography.Link
          className="flex items-center gap-1 text-sm"
          onClick={() => void load()}
        >
          <ReloadOutlined />
          刷新
        </Typography.Link>
      </div>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]">
        {menuTree.map((row) => {
          const children = subRouteMap[row.key];
          const expanded = expandedKeys.has(row.key);
          return (
            <div key={row.key}>
              <div className="flex items-center justify-between border-b border-[#e5e7eb] px-4 py-3.5 dark:border-[#334155]">
                <div className="flex items-center gap-2">
                  {children ? (
                    <RightOutlined
                      onClick={() => toggleExpand(row.key)}
                      className={`cursor-pointer text-xs text-gray-400 transition-transform ${expanded ? "rotate-90" : ""}`}
                    />
                  ) : (
                    <span className="w-3" />
                  )}
                  <span className="text-sm text-[#2e3a52] dark:text-gray-300">{row.label}</span>
                </div>
                <Switch
                  checked={visibility[row.key] !== false}
                  loading={savingKey === row.key}
                  onChange={(checked) => void handleToggle(row.key, checked)}
                />
              </div>
              {children && expanded ? (
                <div className="ml-11 flex flex-col border-b border-[#e5e7eb] dark:border-[#334155]">
                  {children.map((child) => (
                    <div
                      key={child.key}
                      className="flex items-center justify-between px-4 py-2.5 text-sm text-[#3d4a64] dark:text-gray-400"
                    >
                      <span>{child.label}</span>
                      <Switch
                        checked={visibility[child.key] !== false}
                        loading={savingKey === child.key}
                        onChange={(checked) => void handleToggle(child.key, checked)}
                      />
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
        {loading ? (
          <div className="flex items-center justify-center py-8 text-sm text-gray-400">加载中...</div>
        ) : null}
      </div>
    </div>
  );
}
