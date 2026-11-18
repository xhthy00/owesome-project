import LegacyOverview from '@/components/overview/LegacyOverview';
import ScopeOverview from '@/components/overview/ScopeOverview';
import { useSession } from '@/workspace';

/**
 * 学情总览入口：v2 分级权限账号走服务端驱动的 ScopeOverview；
 * 尚未迁移的旧版权限账号保持原有达线看板视图。
 */
export default function Overview() {
  const { session } = useSession();
  const v2 = Boolean(
    session?.user.edu_scope?.edu_role === 'v2' ||
      session?.user.edu_scope?.levels?.length ||
      session?.user.edu_scope?.datasource_id,
  );
  return v2 ? <ScopeOverview /> : <LegacyOverview />;
}
