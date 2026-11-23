import EduPermissionV2 from "@/components/EduPermissionV2";

/** 教育权限页：V1 已下线，只保留按工作空间/数据源绑定的 V2 授权编辑。 */
export default function EduPermissionPage() {
  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <EduPermissionV2 />
    </div>
  );
}
