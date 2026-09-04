import type { ReactNode } from "react";
import type { Components } from "react-markdown";

function flattenText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (typeof node === "object" && "props" in node) {
    return flattenText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

function looksLikeNumericHeader(text: string): boolean {
  const t = text.replace(/\s/g, "");
  return /均分|分差|名次|得分率|及格率|优秀率|差值|数值|对照|人数|满分|排名|率差/.test(t);
}

function looksLikeNumericValue(text: string): boolean {
  const t = text.trim().replace(/,/g, "");
  if (!t || t === "—" || t === "-" || t === "–") return false;
  return /^-?\d+(\.\d+)?%?$/.test(t) || /^\d+\s*\/\s*\d+$/.test(t);
}

function looksWeakLabel(text: string): boolean {
  const t = text.trim().replace(/\s/g, "");
  return t === "薄弱" || t.startsWith("薄弱（") || t.startsWith("薄弱(");
}

function rowLooksWeak(text: string): boolean {
  const t = text.replace(/\s/g, "");
  if (t.includes("无明显薄弱") || t.includes("不作为薄弱")) return false;
  return t.includes("薄弱");
}

/** 学情报告同款表格：圆角容器、蓝表头、无竖线、数字右对齐。 */
export const eduMarkdownComponents: Components = {
  table: ({ children }) => (
    <div className="edu-md-table-wrap">
      <table>{children}</table>
    </div>
  ),
  tr: ({ children }) => {
    const weak = rowLooksWeak(flattenText(children));
    return <tr className={weak ? "is-weak" : undefined}>{children}</tr>;
  },
  th: ({ children }) => {
    const num = looksLikeNumericHeader(flattenText(children));
    return <th className={num ? "num" : undefined}>{children}</th>;
  },
  td: ({ children }) => {
    const text = flattenText(children);
    const cls = [
      looksLikeNumericValue(text) ? "num" : "",
      looksWeakLabel(text) ? "is-weak-label" : ""
    ]
      .filter(Boolean)
      .join(" ");
    return <td className={cls || undefined}>{children}</td>;
  }
};
