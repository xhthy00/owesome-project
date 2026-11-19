import { Watermark } from "antd";
import type { WatermarkProps } from "antd";
import type { ReactNode } from "react";
import { getCachedWatermarkText } from "@/utils/userWatermark";

/** 页面与弹层使用轻水印；报告导出继续使用独立的清晰水印。 */
export const pageWatermarkProps = {
  gap: [280, 200],
  rotate: -22,
  font: { fontSize: 14, color: "rgba(100,116,139,0.14)" },
} satisfies WatermarkProps;

/** Modal/Drawer 在 Portal 里，页面级 Watermark 盖不到，弹框内容需单独包一层。 */
export function UserWatermark({ children }: { children: ReactNode }) {
  const text = getCachedWatermarkText();
  if (!text) return <>{children}</>;
  return (
    <Watermark
      {...pageWatermarkProps}
      inherit={false}
      content={text}
    >
      {children}
    </Watermark>
  );
}
