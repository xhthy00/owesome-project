import { useId } from 'react';

/**
 * 品牌标识：渐变圆角方块 + 心电/数据脉搏折线。
 * 纯 SVG，不依赖图片资源，尺寸可任意缩放（登录页 44、顶栏 30）。
 */
export default function BrandLogo({ size = 40, className }: { size?: number; className?: string }) {
  const raw = useId();
  const key = raw.replace(/[^a-zA-Z0-9]/g, '');
  const bg = `brand-bg-${key}`;
  const glow = `brand-glow-${key}`;
  return <svg
    className={className}
    width={size}
    height={size}
    viewBox="0 0 40 40"
    aria-hidden="true"
    focusable="false"
  >
    <defs>
      <linearGradient id={bg} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stopColor="#1d4ed8" />
        <stop offset="48%" stopColor="#2563eb" />
        <stop offset="100%" stopColor="#22d3ee" />
      </linearGradient>
      <linearGradient id={glow} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="#ffffff" stopOpacity=".3" />
        <stop offset="60%" stopColor="#ffffff" stopOpacity="0" />
      </linearGradient>
    </defs>
    <rect x="0" y="0" width="40" height="40" rx="11" fill={`url(#${bg})`} />
    <rect x="0" y="0" width="40" height="40" rx="11" fill={`url(#${glow})`} />
    <rect x=".5" y=".5" width="39" height="39" rx="10.5" fill="none" stroke="#ffffff" strokeOpacity=".28" />
    <path
      d="M7 21.5h4.6l2.4-6.1 3.4 11.4 2.9-7.6 1.9 2.3H33"
      fill="none"
      stroke="#ffffff"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <circle cx="10.6" cy="12.4" r="1.5" fill="#ffffff" fillOpacity=".85" />
    <circle cx="30.2" cy="13.6" r="1.1" fill="#ffffff" fillOpacity=".55" />
  </svg>;
}
