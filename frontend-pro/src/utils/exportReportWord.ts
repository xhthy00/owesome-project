/**
 * 将学情报告导出为 Word（.doc）。
 *
 * Word 对 CSS Grid/Flex/渐变支持很差，DOM 直转无法与 HTML 预览一致。
 * 因此按「已渲染预览」截图嵌入文档，保证样式与 HTML/PDF 观感一致。
 */

import {
  computeSliceCuts,
  sliceCanvasByCuts,
  withReportCapture
} from "./reportCapture";

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

/** 将超长截图按断点切片（断点避开图表/表格行），避免单图过大导致 Word 打不开 */
function sliceCanvasToDataUrls(canvas: HTMLCanvasElement, cuts: number[]): string[] {
  return sliceCanvasByCuts(canvas, cuts);
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

  const { default: html2canvas } = await import("html2canvas");
  await withReportCapture(doc, html2canvas, 2, (canvas) => {
    // ~A4 @2x：210mm ≈ 794px CSS，*2 scale ≈ 1600
    const pageHeightPx = Math.max(1200, Math.floor(canvas.width * 1.414));
    const cuts = computeSliceCuts(canvas, pageHeightPx, doc);
    const imageDataUrls = sliceCanvasToDataUrls(canvas, cuts);
    const content = buildWordDocWithImages(title, imageDataUrls);
    downloadDoc(title, content);
  });
}
