import ScopeOverview from '@/components/overview/ScopeOverview';

/**
 * 学情总览入口：V1 已下线，统一走服务端按授权范围驱动的 ScopeOverview。
 */
export default function Overview() {
  return <ScopeOverview />;
}
