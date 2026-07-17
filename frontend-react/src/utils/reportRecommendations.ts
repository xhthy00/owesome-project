/** 报告建议区提取 / 回写（与后端 report_edit.py 对齐）。 */

const SECTION_ATTR = 'data-edu-section="recommendations"';

const HEADING_RE =
  /<h2[^>]*>\s*(?:改进建议|教学建议|干预建议|学习建议|家庭配合建议)\s*<\/h2>\s*|<h3[^>]*>\s*（二）\s*知识点提升与分科备考策略\s*<\/h3>\s*/i;

function htmlToPlain(fragment: string): string {
  let text = fragment || "";
  text = text.replace(/<br\s*\/?>/gi, "\n");
  text = text.replace(/<\/p\s*>/gi, "\n");
  text = text.replace(/<\/li\s*>/gi, "\n");
  text = text.replace(/<\/h[1-6]\s*>/gi, "\n");
  text = text.replace(/<[^>]+>/g, "");
  const textarea = typeof document !== "undefined" ? document.createElement("textarea") : null;
  if (textarea) {
    textarea.innerHTML = text;
    text = textarea.value;
  } else {
    text = text
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'");
  }
  const lines = text.replace(/\r\n/g, "\n").split("\n").map((ln) => ln.trimEnd());
  const out: string[] = [];
  let blank = 0;
  for (const ln of lines) {
    if (!ln.trim()) {
      blank += 1;
      if (blank <= 1 && out.length) out.push("");
      continue;
    }
    blank = 0;
    out.push(ln.trim());
  }
  return out.join("\n").trim();
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function plainToHtml(plain: string): string {
  const text = (plain || "").replace(/\r\n/g, "\n").trim();
  if (!text) return "<p></p>";
  return text
    .split(/\n\s*\n/)
    .map((part) => `<p>${part.split("\n").map(escapeHtml).join("<br/>")}</p>`)
    .join("\n");
}

function extractMarkedInner(raw: string): string | null {
  const m = raw.match(new RegExp(`<div[^>]*${SECTION_ATTR}[^>]*>([\\s\\S]*?)</div>`, "i"));
  return m ? m[1] : null;
}

function extractHeadingFollowing(raw: string): string | null {
  const m = HEADING_RE.exec(raw);
  if (!m) return null;
  const rest = raw.slice(m.index + m[0].length);
  const block = rest.match(/^\s*(<(?:div|p|ul|ol)[^>]*>[\s\S]*?<\/(?:div|p|ul|ol)>)/i);
  if (block) return block[1];
  const nextH2 = rest.search(/<h2\b/i);
  const nextSec = rest.search(/<\/section>/i);
  let end = rest.length;
  if (nextH2 >= 0) end = Math.min(end, nextH2);
  if (nextSec >= 0) end = Math.min(end, nextSec);
  const chunk = rest.slice(0, end).trim();
  return chunk || null;
}

export function extractRecommendationsText(reportHtml: string): string | null {
  const raw = reportHtml || "";
  const inner = extractMarkedInner(raw) ?? extractHeadingFollowing(raw);
  if (inner == null) return null;
  return htmlToPlain(inner);
}

export function hasRecommendationsSection(reportHtml: string): boolean {
  return extractRecommendationsText(reportHtml) != null;
}

export function replaceRecommendationsHtml(reportHtml: string, plainText: string): string {
  const raw = reportHtml || "";
  const newInner = plainToHtml(plainText);
  const markedRe = new RegExp(`(<div[^>]*${SECTION_ATTR}[^>]*>)([\\s\\S]*?)(</div>)`, "i");
  if (markedRe.test(raw)) {
    return raw.replace(markedRe, `$1${newInner}$3`);
  }
  const m = HEADING_RE.exec(raw);
  if (!m) return raw;
  const startAfter = m.index + m[0].length;
  const rest = raw.slice(startAfter);
  const block = rest.match(/^(\s*)(<(?:div|p|ul|ol)[^>]*>[\s\S]*?<\/(?:div|p|ul|ol)>)/i);
  const wrapped = `<div ${SECTION_ATTR}>${newInner}</div>`;
  if (block) {
    const absStart = startAfter + (block[1]?.length || 0);
    const absEnd = startAfter + block[0].length;
    return raw.slice(0, absStart) + wrapped + raw.slice(absEnd);
  }
  const nextH2 = rest.search(/<h2\b/i);
  const nextSec = rest.search(/<\/section>/i);
  let endRel = rest.length;
  if (nextH2 >= 0) endRel = Math.min(endRel, nextH2);
  if (nextSec >= 0) endRel = Math.min(endRel, nextSec);
  return `${raw.slice(0, startAfter)}\n      ${wrapped}\n    ${raw.slice(startAfter + endRel)}`;
}
