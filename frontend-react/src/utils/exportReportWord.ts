/**
 * 将学情报告导出为 Word（.doc）。
 *
 * Word 对 CSS Grid/Flex/渐变支持很差，DOM 直转无法与 HTML 预览一致。
 * 因此按「已渲染预览」截图嵌入文档，保证样式与 HTML/PDF 观感一致。
 */

import { captureElementWithWatermark } from "@/utils/userWatermark";

export function sanitizeFileName(name: string): string {
  return (name || "report").replace(/[\\/:*?"<>|]+/g, "_").trim() || "report";
}

function buildWordDocWithImages(title: string, imageDataUrls: string[]): string {
  const safeTitle = sanitizeFileName(title);
  const imagesHtml = imageDataUrls
    .map(
      (src, i) =>
        `<p style="margin:0 0 ${i === imageDataUrls.length - 1 ? 0 : 8}px;text-align:center;">` +
        `<img src="${src}" width="680" style="width:680px;max-width:100%;height:auto;" />` +
        `</p>`
    )
    .join("\n");

  return `<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:w="urn:schemas-microsoft-com:office:word"
 xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<title>${safeTitle}</title>
<!--[if gte mso 9]>
<xml>
  <w:WordDocument>
    <w:View>Print</w:View>
    <w:Zoom>100</w:Zoom>
    <w:DoNotOptimizeForBrowser/>
  </w:WordDocument>
</xml>
<![endif]-->
<style>
  @page { size: A4; margin: 1.5cm; }
  body { margin: 0; padding: 0; }
  img { border: 0; }
</style>
</head>
<body>${imagesHtml}</body>
</html>`;
}

/** 将超长截图按近似 A4 高度切片，避免单图过大导致 Word 打不开 */
function sliceCanvasToDataUrls(canvas: HTMLCanvasElement, pageHeightPx: number): string[] {
  const urls: string[] = [];
  const totalH = canvas.height;
  const w = canvas.width;
  if (totalH <= pageHeightPx) {
    urls.push(canvas.toDataURL("image/png"));
    return urls;
  }
  let y = 0;
  while (y < totalH) {
    const sliceH = Math.min(pageHeightPx, totalH - y);
    const slice = document.createElement("canvas");
    slice.width = w;
    slice.height = sliceH;
    const ctx = slice.getContext("2d");
    if (!ctx) break;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, sliceH);
    ctx.drawImage(canvas, 0, y, w, sliceH, 0, 0, w, sliceH);
    urls.push(slice.toDataURL("image/png"));
    y += sliceH;
  }
  return urls.length ? urls : [canvas.toDataURL("image/png")];
}

function downloadDoc(title: string, content: string): void {
  const fileTitle = sanitizeFileName(title);
  const blob = new Blob(["\ufeff", content], {
    type: "application/msword;charset=utf-8"
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${fileTitle}.doc`;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * 从预览 iframe 截图并导出 Word，样式与 HTML 预览一致。
 * 必须传入已加载完成的 iframe（含图表渲染）。
 */
export async function exportReportAsWord(options: {
  title: string;
  html: string;
  iframe?: HTMLIFrameElement | null;
}): Promise<void> {
  const { title, html, iframe } = options;
  if (!html.trim()) {
    throw new Error("暂无报告内容");
  }

  const doc = iframe?.contentDocument;
  const body = doc?.body;
  if (!body) {
    throw new Error("无法访问报告预览，请等预览加载完成后再导出 Word");
  }

  // 等一帧，确保 ECharts 等异步绘制完成
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });

  const { default: html2canvas } = await import("html2canvas");
  const canvas = await captureElementWithWatermark(body, html2canvas, {
    scale: 2,
    useCORS: true,
    allowTaint: true,
    backgroundColor: "#ffffff",
    logging: false,
    windowWidth: body.scrollWidth,
    windowHeight: body.scrollHeight
  });

  // ~A4 @2x：210mm ≈ 794px CSS，*2 scale ≈ 1600
  const pageHeightPx = Math.max(1200, Math.floor(canvas.width * 1.414));
  const imageDataUrls = sliceCanvasToDataUrls(canvas, pageHeightPx);
  const content = buildWordDocWithImages(title, imageDataUrls);
  downloadDoc(title, content);
}
