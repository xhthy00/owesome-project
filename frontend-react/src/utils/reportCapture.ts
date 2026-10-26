/**
 * 报告截图公共层：PDF / Word 导出共用。
 *
 * 解决两个共性问题：
 * 1. 表格横向滚动条裁切——截图前展开滚动容器并按最宽表格放宽文档宽度，
 *    保证被裁掉的列完整入图；截完恢复，预览不受影响。
 * 2. 图表被分页切断——分页切断点避开图表 / 提示框 / 表格行 / 标题，
 *    落在元素边界（PDF 与 Word 切片共用同一套断点计算）。
 *
 * `withReportCapture` 保证「截图 + 元素测位」都发生在布局展开期间
 * （回调里测的 getBoundingClientRect 与画布一一对应）。
 */
import { captureElementWithWatermark } from "./userWatermark";

interface AtomicRange {
  top: number;
  bottom: number;
}

type Html2CanvasFn = (
  element: HTMLElement,
  options?: object
) => Promise<HTMLCanvasElement>;

/** 展开横向滚动容器 + 放宽文档宽度；返回 restore 与展开后的宽度。 */
function prepareDocumentForCapture(doc: Document): {
  needed: number;
  restore: () => void;
} {
  const body = doc.body;
  const wraps = Array.from(
    doc.querySelectorAll<HTMLElement>(".edu-table-wrap, .table-wrap")
  );
  let needed = body?.scrollWidth || 1100;
  wraps.forEach((w) => {
    const t = w.querySelector("table");
    if (t) needed = Math.max(needed, t.scrollWidth + 48);
  });
  needed = Math.min(Math.max(needed, 1100), 2400);

  const override = doc.createElement("style");
  override.id = "edu-report-capture-override";
  override.textContent = [
    `body{width:${needed}px !important;}`,
    ".edu-container{max-width:none !important;}",
    ".edu-table-wrap,.table-wrap{overflow:visible !important;}"
  ].join("");
  (doc.head || doc.documentElement).appendChild(override);
  return {
    needed,
    restore: () => override.remove()
  };
}

/**
 * 展开布局 → 截整页大图（含水印）→ 在展开布局下执行回调 → 恢复布局。
 * 回调里可安全调用 computeSliceCuts（元素位置与画布一致）。
 */
export async function withReportCapture<T>(
  doc: Document,
  html2canvas: Html2CanvasFn,
  scale: number,
  run: (canvas: HTMLCanvasElement) => Promise<T> | T
): Promise<T> {
  const body = doc.body;
  if (!body) throw new Error("report body missing");
  // 便于确认页面跑的是新版导出逻辑（旧版是整页大图硬切）
  console.info("[report-capture] smart pagination enabled");
  // 等两帧，确保 ECharts 等异步绘制完成
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
  const { needed, restore } = prepareDocumentForCapture(doc);
  try {
    const canvas = await captureElementWithWatermark(body, html2canvas, {
      scale,
      useCORS: true,
      allowTaint: true,
      backgroundColor: "#ffffff",
      width: needed,
      windowWidth: needed
    });
    return await run(canvas);
  } finally {
    restore();
  }
}

/**
 * 计算切片切断点（画布 px）：图表 / 提示框 / 芯片清单 / 表格行 / 标题
 * 不跨页，断点落在元素边界。最后一个断点恒为 canvas.height。
 * 必须在 withReportCapture 的回调里调用（布局需处于展开状态）。
 */
export function computeSliceCuts(
  canvas: HTMLCanvasElement,
  sliceH: number,
  doc: Document
): number[] {
  const body = doc.body;
  if (!body || !(sliceH > 0)) return [canvas.height];
  const bodyTop = body.getBoundingClientRect().top;
  const pxToCanvas = canvas.width / Math.max(body.scrollWidth || 1, 1);

  // 硬边界：图表 / 提示框 / 芯片清单 / 标题（标题带下一个兄弟块，避免孤行）
  const HARD_SEL = ".edu-chart, .edu-tip, .edu-diag-chips, h1, h2, h3";
  const softRanges: AtomicRange[] = [];
  doc.querySelectorAll(".edu-table-wrap tr, .table-wrap tr").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.height <= 0) return;
    softRanges.push({
      top: Math.max(0, (r.top - bodyTop) * pxToCanvas),
      bottom: (r.bottom - bodyTop) * pxToCanvas
    });
  });
  const hardRanges: AtomicRange[] = [];
  doc.querySelectorAll(HARD_SEL).forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.height <= 0) return;
    const range = {
      top: Math.max(0, (r.top - bodyTop) * pxToCanvas),
      bottom: (r.bottom - bodyTop) * pxToCanvas
    };
    // 标题与其后第一个内容块 keep-together：块间空隙并入标题范围
    if (/^H[1-3]$/.test((el as HTMLElement).tagName)) {
      let nextTop = Infinity;
      const parent = el.parentElement;
      if (parent) {
        for (const sib of Array.from(parent.children)) {
          const sr = sib.getBoundingClientRect();
          if (sr.top > r.top + 1 && sr.height > 0 && sr.top - r.bottom < 80) {
            nextTop = Math.min(nextTop, sr.top);
          }
        }
      }
      if (nextTop < Infinity) {
        range.bottom = (nextTop - bodyTop) * pxToCanvas + 1;
      }
    }
    hardRanges.push(range);
  });
  hardRanges.sort((a, b) => a.top - b.top);
  softRanges.sort((a, b) => a.top - b.top);

  // 在 cut 落点处找可兼顾的边界：先硬边界（图表/标题等整体挪页或整留本页），
  // 再软边界（表格行）。返回调整后的 cut；无法兼顾返回 -1。
  const minFill = sliceH * 0.15; // 断点上移时本页最少要填多少，避免产生碎片页
  const resolveCut = (cut: number, prev: number): number => {
    for (const ranges of [hardRanges, softRanges]) {
      for (const r of ranges) {
        if (r.top >= cut || r.bottom <= cut) continue;
        if (r.bottom - r.top > sliceH) continue;
        if (r.top >= prev + minFill && r.top < cut) return r.top;
        if (r.bottom <= prev + sliceH && r.bottom >= prev + minFill) {
          return r.bottom;
        }
      }
    }
    return -1;
  };

  const cuts: number[] = [];
  let prev = 0;
  let ideal = sliceH;
  while (ideal < canvas.height) {
    let cut = Math.min(ideal, canvas.height);
    const resolved = resolveCut(cut, prev);
    if (resolved > 0) cut = resolved;
    if (cut <= prev) cut = ideal;
    cuts.push(cut);
    prev = cut;
    ideal = cut + sliceH;
  }
  if (canvas.height > prev) cuts.push(canvas.height);
  return cuts;
}

/** 按断点把整页大图切成若干片（dataURL），每片独立画布、白底。 */
export function sliceCanvasByCuts(
  canvas: HTMLCanvasElement,
  cuts: number[]
): string[] {
  const urls: string[] = [];
  let start = 0;
  for (let i = 0; i < cuts.length; i++) {
    const end = cuts[i];
    const h = Math.min(end - start, canvas.height - start);
    if (h > 0) {
      const slice = document.createElement("canvas");
      slice.width = canvas.width;
      slice.height = h;
      const ctx = slice.getContext("2d");
      if (ctx) {
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, h);
        ctx.drawImage(canvas, 0, start, canvas.width, h, 0, 0, canvas.width, h);
      }
      urls.push(slice.toDataURL("image/png"));
    }
    start = end;
  }
  return urls.length ? urls : [canvas.toDataURL("image/png")];
}
