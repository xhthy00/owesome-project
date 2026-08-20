import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Switch, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { permissionApi } from "@/api/permission";

export default function ConstructPermissionPrivacyPage() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [anonymizeDisplay, setAnonymizeDisplay] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const res = await permissionApi.getEduPrivacy();
      setAnonymizeDisplay(res?.anonymize_display !== false);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载脱敏设置失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleToggle = async (checked: boolean) => {
    setSaving(true);
    try {
      await permissionApi.setEduPrivacy(checked);
      setAnonymizeDisplay(checked);
      message.success(checked ? "已开启匿名脱敏展示" : "已关闭匿名脱敏，可展示姓名/学号/校名");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            数据脱敏
          </Typography.Title>
          <Typography.Text className="oc-muted">
            控制系统问数与报告是否匿名展示学生、学号与学校信息
          </Typography.Text>
        </div>
        <Typography.Link className="flex items-center gap-1 text-sm" onClick={() => void load()}>
          <ReloadOutlined />
          刷新
        </Typography.Link>
      </div>

      <Alert
        className="mb-4"
        type="warning"
        showIcon
        message="全局开关，仅系统管理员可修改"
        description="开启时只展示脱敏学号与校码，不展示学生姓名、真实学号与学校全称。关闭后可在特定场合展示明文。身份证号与考生号始终隐藏。"
      />

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]">
        <div className="flex items-center justify-between px-4 py-3.5">
          <div>
            <div className="text-sm text-[#2e3a52] dark:text-gray-300">匿名脱敏展示</div>
            <div className="mt-1 text-xs text-gray-400">
              {anonymizeDisplay
                ? "当前：隐藏姓名、真实学号、学校全称"
                : "当前：允许展示姓名、学号、学校全称"}
            </div>
          </div>
          <Switch
            checked={anonymizeDisplay}
            loading={saving || loading}
            onChange={(checked) => void handleToggle(checked)}
          />
        </div>
      </div>
    </div>
  );
}
