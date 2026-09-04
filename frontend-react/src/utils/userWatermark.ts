/** 登录用户水印：主布局、报告 iframe 预览、PDF/Word 截图共用文案。 */

const OVERLAY_ID = "awesome-user-watermark";

let cachedText = "";

export function userWatermarkText(account: string, name: string): string {
  const a = account.trim();
  const n = name.trim();
  if (a && n && n !== a) return `${a} ${n}`;
  return a || n;
}

export function setCachedWatermarkText(text: string): void {
  cachedText = text.trim();
}

export function getCachedWatermarkText(): string {
  return cachedText;
}

function svgTileUrl(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="220">` +
    `<text x="40" y="128" fill="rgba(0,0,0,0.16)" font-size="22" font-family="sans-serif" ` +
    `transform="rotate(-22,180,110)">${escaped}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function applyDocumentWatermark(doc: Document, text?: string): void {
  const t = (text ?? cachedText).trim();
  const existing = doc.getElementById(OVERLAY_ID);
  if (!t) {
    existing?.remove();
    return;
  }
  const body = doc.body;
  if (!body) return;
  const overlay = existing ?? doc.createElement("div");
  overlay.id = OVERLAY_ID;
  overlay.setAttribute("aria-hidden", "true");
  const height = Math.max(
    body.scrollHeight,
    doc.documentElement?.scrollHeight || 0,
    body.clientHeight,
    1
  );
  overlay.style.cssText = [
    "position:absolute",
    "left:0",
    "top:0",
    "width:100%",
    `height:${height}px`,
    "pointer-events:none",
    "z-index:2147483646",
    `background-image:url("${svgTileUrl(t)}")`,
    "background-repeat:repeat"
  ].join(";");
  const pos = doc.defaultView?.getComputedStyle(body).position;
  if (!pos || pos === "static") {
    body.style.position = "relative";
  }
  if (!existing) body.appendChild(overlay);
}

/** 数字列：表头与单元格同为右对齐，并套上学情报告表格外观。 */
export function alignEduTableNumericHeaders(doc: Document): void {
  doc.querySelectorAll("table").forEach((table) => {
    table.classList.add("edu-table");
    if (!table.closest(".edu-table-wrap")) {
      const wrap = doc.createElement("div");
      wrap.className = "edu-table-wrap";
      table.parentNode?.insertBefore(wrap, table);
      wrap.appendChild(table);
    }
    const ths = table.querySelectorAll("thead th");
    const row = table.querySelector("tbody tr");
    if (!ths.length || !row) return;
    row.querySelectorAll("td").forEach((td, i) => {
      if (td.classList.contains("num") && ths[i]) {
        ths[i].classList.add("num");
      }
    });
    table.querySelectorAll("tbody tr").forEach((tr) => {
      const weak = Array.from(tr.querySelectorAll("td")).some((td) => {
        const t = (td.textContent || "").replace(/\s/g, "");
        return t === "薄弱" || t.startsWith("薄弱（") || t.startsWith("薄弱(");
      });
      if (weak) tr.classList.add("is-weak");
    });
  });
  const styleId = "edu-table-num-align";
  const css = [
    ".edu-table-wrap{overflow-x:auto;margin:8px 0 12px;border:1px solid #e8edf3;border-radius:12px;background:#fff}",
    "table,.edu-table{width:100%;border-collapse:collapse;font-size:13px}",
    "table th,table td,.edu-table th,.edu-table td{border:none;border-bottom:1px solid #e8edf3;padding:11px 14px;text-align:left;vertical-align:middle}",
    "table thead th,.edu-table thead th{background:linear-gradient(180deg,#f3f8ff 0%,#e6f4ff 100%);color:#3b6fb8;font-weight:650;white-space:nowrap;font-size:12.5px}",
    "table tbody tr:nth-child(even) td,.edu-table tbody tr:nth-child(even) td{background:#fafcfe}",
    "table tbody tr:hover td,.edu-table tbody tr:hover td{background:#f0f7ff}",
    "table tbody tr.is-weak td,.edu-table tbody tr.is-weak td{background:#fff7e6}",
    "table tbody tr.is-weak:hover td,.edu-table tbody tr.is-weak:hover td{background:#fff1d6}",
    "table tbody tr:last-child td,.edu-table tbody tr:last-child td{border-bottom:none}",
    "table th.num,table td.num,.edu-table th.num,.edu-table td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}",
    ".edu-badge-weak{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;background:#fff1f0;color:#cf1322;font-weight:650;vertical-align:middle}"
  ].join("");
  let style = doc.getElementById(styleId) as HTMLStyleElement | null;
  if (!style) {
    style = doc.createElement("style");
    style.id = styleId;
    (doc.head || doc.documentElement).appendChild(style);
  }
  style.textContent = css;
}

export function bindIframeWatermark(iframe: HTMLIFrameElement | null): void {
  const doc = iframe?.contentDocument;
  if (!doc?.body) return;
  alignEduTableNumericHeaders(doc);
  applyDocumentWatermark(doc);
  window.setTimeout(() => applyDocumentWatermark(doc), 600);
}

/** 把水印写进独立 HTML 文件（下载/复制），打开本地文件时仍可见。 */
export function stampHtmlWatermark(html: string, text?: string): string {
  const t = (text ?? cachedText).trim();
  if (!t || !html.trim()) return html;
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");
  doc.getElementById(OVERLAY_ID)?.remove();
  doc.getElementById(`${OVERLAY_ID}-style`)?.remove();
  const style = doc.createElement("style");
  style.id = `${OVERLAY_ID}-style`;
  style.textContent = [
    "html,body{min-height:100%;}",
    "body{position:relative;}",
    `#${OVERLAY_ID}{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:2147483646;background-image:url("${svgTileUrl(t)}");background-repeat:repeat;}`,
    `@media print{#${OVERLAY_ID}{position:absolute;min-height:100%;height:100%;}}`
  ].join("");
  (doc.head ?? doc.documentElement).appendChild(style);
  const overlay = doc.createElement("div");
  overlay.id = OVERLAY_ID;
  overlay.setAttribute("aria-hidden", "true");
  (doc.body ?? doc.documentElement).appendChild(overlay);
  return `<!DOCTYPE html>\n${doc.documentElement.outerHTML}`;
}

export function stampCanvasWatermark(canvas: HTMLCanvasElement, text?: string): void {
  const t = (text ?? cachedText).trim();
  if (!t) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const fontSize = Math.max(36, Math.round(canvas.width / 55));
  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.16)";
  ctx.font = `${fontSize}px sans-serif`;
  ctx.textBaseline = "middle";
  const gapX = fontSize * 14;
  const gapY = fontSize * 10;
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((-22 * Math.PI) / 180);
  const cover = Math.sqrt(canvas.width ** 2 + canvas.height ** 2);
  for (let y = -cover; y < cover; y += gapY) {
    for (let x = -cover; x < cover; x += gapX) {
      ctx.fillText(t, x, y);
    }
  }
  ctx.restore();
}

type Html2CanvasFn = (
  element: HTMLElement,
  // html2canvas 选项类型与动态 import 不完全对齐，这里放宽
  options?: object
) => Promise<HTMLCanvasElement>;

/** 截图时先藏预览层，再在 canvas 上盖水印，避免漏打或叠两层。 */
export async function captureElementWithWatermark(
  element: HTMLElement,
  html2canvas: Html2CanvasFn,
  options?: object
): Promise<HTMLCanvasElement> {
  const overlay = element.ownerDocument.getElementById(OVERLAY_ID) as HTMLElement | null;
  const prevDisplay = overlay?.style.display;
  if (overlay) overlay.style.display = "none";
  try {
    const canvas = await html2canvas(element, options);
    stampCanvasWatermark(canvas);
    return canvas;
  } finally {
    if (overlay) overlay.style.display = prevDisplay ?? "";
  }
}
