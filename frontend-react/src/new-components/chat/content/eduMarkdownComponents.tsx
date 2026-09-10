import { FullscreenOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import {
  Children,
  createContext,
  isValidElement,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode
} from "react";
import type { Components } from "react-markdown";
import { labelColumn } from "@/utils/columnLabels";
import { formatNumericDisplay } from "@/utils/formatNumericDisplay";

const NumColContext = createContext<{
  isNumCol: (index: number) => boolean;
  markNumCol: (index: number) => void;
}>({ isNumCol: () => false, markNumCol: () => undefined });

const ColIndexContext = createContext(0);

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
  return /均分|分差|名次|得分率|及格率|优秀率|差值|数值|对照|人数|满分|排名|率差|学校数|班级数|参赛校数|参赛班数|校数|班数|段位|占比|百分/.test(
    t
  );
}

function looksLikeNumericValue(text: string): boolean {
  const t = text.trim().replace(/,/g, "").replace(/\s/g, "");
  if (!t || t === "—" || t === "-" || t === "–") return false;
  if (/^第\d+名?$/.test(t)) return true;
  if (/^[前后]\d+(\.\d+)?%$/.test(t)) return true;
  return /^-?\d+(\.\d+)?%?$/.test(t) || /^\d+\/\d+$/.test(t);
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

function EduMarkdownTable({ children }: { children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const numColsRef = useRef<Set<number>>(new Set());
  const ctx = useMemo(
    () => ({
      isNumCol: (index: number) => numColsRef.current.has(index),
      markNumCol: (index: number) => {
        numColsRef.current.add(index);
      }
    }),
    []
  );
  return (
    <NumColContext.Provider value={ctx}>
      <div className="edu-md-table-wrap">
        <div className="edu-md-table-toolbar">
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="edu-md-table-fullscreen"
            title="全屏查看"
          >
            <FullscreenOutlined />
            <span>全屏</span>
          </button>
        </div>
        <div className="edu-md-table-scroll">
          <table>{children}</table>
        </div>
      </div>
      <Modal
        title="查询结果"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width="96%"
        style={{ top: 16 }}
        styles={{ body: { padding: 12, maxHeight: "80vh", overflow: "auto" } }}
        destroyOnClose
      >
        <div className="edu-md-table-wrap edu-md-table-wrap--full">
          <table>{children}</table>
        </div>
      </Modal>
    </NumColContext.Provider>
  );
}

function TableRow({ children }: { children?: ReactNode }) {
  const weak = rowLooksWeak(flattenText(children));
  return (
    <tr className={weak ? "is-weak" : undefined}>
      {Children.toArray(children)
        .filter((cell) => isValidElement(cell))
        .map((cell, i) => (
          <ColIndexContext.Provider key={i} value={i}>
            {cell}
          </ColIndexContext.Provider>
        ))}
    </tr>
  );
}

function TableHeaderCell({ children }: { children?: ReactNode }) {
  const index = useContext(ColIndexContext);
  const { markNumCol } = useContext(NumColContext);
  const labeled = labelColumn(flattenText(children));
  const num = looksLikeNumericHeader(labeled);
  if (num) markNumCol(index);
  return <th className={num ? "num" : undefined}>{labeled}</th>;
}

function TableDataCell({ children }: { children?: ReactNode }) {
  const index = useContext(ColIndexContext);
  const { isNumCol } = useContext(NumColContext);
  const text = flattenText(children);
  const formatted = formatNumericDisplay(text);
  const num = isNumCol(index) || looksLikeNumericValue(formatted);
  const cls = [num ? "num" : "", looksWeakLabel(formatted) ? "is-weak-label" : ""]
    .filter(Boolean)
    .join(" ");
  return <td className={cls || undefined}>{formatted}</td>;
}

/** 学情报告同款表格：圆角容器、蓝表头、无竖线、数字列头尾同对齐。 */
export const eduMarkdownComponents: Components = {
  table: ({ children }) => <EduMarkdownTable>{children}</EduMarkdownTable>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children }) => <TableHeaderCell>{children}</TableHeaderCell>,
  td: ({ children }) => <TableDataCell>{children}</TableDataCell>
};
