/**
 * 报告 iframe → PDF 导出（html2canvas + jspdf）。
 * 滚动展开 / 图表避切等公共逻辑见 reportCapture.ts。
 */
import {
  computeSliceCuts,
  sliceCanvasByCuts,
  withReportCapture
} from "./reportCapture";

const PAGE_MARGIN_PT = 24;

export async function exportIframeReportPdf(options: {
  /** 报告所在 iframe 的 document */
  doc: Document;
  filename: string;
  /** html2canvas 放大倍数，默认 2 */
  scale?: number;
}): Promise<void> {
  const { doc, filename, scale = 2 } = options;

  const [{ default: html2canvas }, jspdfMod] = await Promise.all([
    import("html2canvas"),
    import("jspdf")
  ]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const JsPDF = (jspdfMod as any).jsPDF;

  await withReportCapture(doc, html2canvas, scale, (canvas) => {
    const pdf = new JsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
    const pageW = pdf.internal.pageSize.getWidth();
    const pageH = pdf.internal.pageSize.getHeight();
    const margin = PAGE_MARGIN_PT;
    const contentW = pageW - margin * 2;
    const contentH = pageH - margin * 2;
    const ratio = contentW / canvas.width; // pt / 画布px
    const sliceH = contentH / ratio; // 整页对应的画布 px

    const cuts = computeSliceCuts(canvas, sliceH, doc);
    const slices = sliceCanvasByCuts(canvas, cuts);
    slices.forEach((dataUrl, i) => {
      if (i > 0) pdf.addPage();
      const h = (cuts[i] - (cuts[i - 1] || 0)) * ratio;
      pdf.addImage(dataUrl, "PNG", margin, margin, contentW, h);
    });
    pdf.save(filename);
  });
}
