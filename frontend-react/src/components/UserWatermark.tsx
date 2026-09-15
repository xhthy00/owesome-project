import { Watermark } from "antd";
import type { ReactNode } from "react";
import { getCachedWatermarkText } from "@/utils/userWatermark";

/** Modal/Drawer 在 Portal 里，页面级 Watermark 盖不到，弹框内容需单独包一层。 */
export function UserWatermark({ children }: { children: ReactNode }) {
  const text = getCachedWatermarkText();
  if (!text) return <>{children}</>;
  return (
    <Watermark
      inherit={false}
      content={text}
      gap={[200, 160]}
      rotate={-22}
      font={{ fontSize: 20, color: "rgba(0,0,0,0.16)" }}
    >
      {children}
    </Watermark>
  );
}
