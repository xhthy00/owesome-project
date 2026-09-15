import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { normalizeAssistantMarkdown } from "@/utils/toolLabels";
import { eduMarkdownComponents } from "./eduMarkdownComponents";

function renderMd(md: string) {
  return render(
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={eduMarkdownComponents}>
      {md}
    </ReactMarkdown>
  );
}

describe("eduMarkdownComponents table align", () => {
  it("同一列的表头和单元格同为数字对齐，含第N名与破折号", () => {
    const md = `
| 口径 | 选考方向 | 六门均分 | 全市排名 | 参赛班级数 |
| --- | --- | --- | --- | --- |
| 按选考方向 | 物理类 | 565.22 | 第1名 | — |
| 不区分选考 | 统一 | 565.22 | 第1名 | — |
`;
    const { container } = renderMd(md);
    const table = container.querySelector("table");
    expect(table).toBeTruthy();
    const headers = Array.from(table!.querySelectorAll("thead th"));
    expect(headers.map((el) => el.textContent)).toEqual([
      "口径",
      "选考方向",
      "六门均分",
      "全市排名",
      "参赛班级数"
    ]);
    expect(headers.map((el) => el.classList.contains("num"))).toEqual([
      false,
      false,
      true,
      true,
      true
    ]);
    const firstRow = table!.querySelectorAll("tbody tr")[0].querySelectorAll("td");
    expect(Array.from(firstRow).map((el) => el.textContent)).toEqual([
      "按选考方向",
      "物理类",
      "565.22",
      "第1名",
      "—"
    ]);
    expect(Array.from(firstRow).map((el) => el.classList.contains("num"))).toEqual([
      false,
      false,
      true,
      true,
      true
    ]);
  });

  it("八列表头与每行列数一致，数字列头尾都带 num", () => {
    const md = `
| 口径 | 学校 | 班级 | 选考方向 | 六门均分 | 参考人数 | 全市排名 | 参赛班级数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 按选考方向 | 扬州中学 | 高三(10)班 | 物理类 | 565.22 | 46 | 1 | 1 |
`;
    const { container } = renderMd(md);
    const table = container.querySelector("table");
    const headers = table!.querySelectorAll("thead th");
    const cells = table!.querySelectorAll("tbody tr")[0].querySelectorAll("td");
    expect(headers.length).toBe(8);
    expect(cells.length).toBe(8);
    for (let i = 0; i < 8; i += 1) {
      expect(cells[i].classList.contains("num")).toBe(headers[i].classList.contains("num"));
    }
  });

  it("参赛校数与排名段位按数字列对齐，含空格的第N和前N%", () => {
    const md = `
| 学科 | 均分 | 全市排名 | 参赛校数 | 排名段位 |
| --- | --- | --- | --- | --- |
| 语文 | 113.24 | 第 3 | 38 | 前 7.9% |
| 化学 | 77.95 | 第3 | 36 | 前8.3% |
`;
    const { container } = renderMd(md);
    const table = container.querySelector("table");
    const headers = Array.from(table!.querySelectorAll("thead th"));
    expect(headers.map((el) => el.classList.contains("num"))).toEqual([
      false,
      true,
      true,
      true,
      true
    ]);
    const firstRow = table!.querySelectorAll("tbody tr")[0].querySelectorAll("td");
    expect(Array.from(firstRow).map((el) => el.classList.contains("num"))).toEqual([
      false,
      true,
      true,
      true,
      true
    ]);
    const secondRow = table!.querySelectorAll("tbody tr")[1].querySelectorAll("td");
    expect(Array.from(secondRow).map((el) => el.classList.contains("num"))).toEqual([
      false,
      true,
      true,
      true,
      true
    ]);
  });

  it("六门总分与总分名次表头和数字同为右对齐", () => {
    const md = `
| 姓名 | 班级 | 六门总分 | 总分名次 |
| --- | --- | --- | --- |
| 杨秋宇 | 高三(1)班 | 684.00 | 1 |
| 朱晨旭 | 高三(1)班 | 682.00 | 2 |
`;
    const { container } = renderMd(md);
    const table = container.querySelector("table");
    const headers = Array.from(table!.querySelectorAll("thead th"));
    expect(headers.map((el) => el.classList.contains("num"))).toEqual([
      false,
      false,
      true,
      true
    ]);
    const firstRow = table!.querySelectorAll("tbody tr")[0].querySelectorAll("td");
    expect(Array.from(firstRow).map((el) => el.classList.contains("num"))).toEqual([
      false,
      false,
      true,
      true
    ]);
  });

  it("单科排名表头把 yy/rk/total_classes 译成中文", () => {
    const md = `
| 学校 | 班级 | 均分yy | 人数yy | rk | total_classes |
| --- | --- | --- | --- | --- | --- |
| A01扬州中学 | 高三(10)班 | 124.28 | 46 | 46 | 388 |
`;
    const { container } = renderMd(md);
    const headers = Array.from(container.querySelectorAll("thead th"));
    expect(headers.map((el) => el.textContent)).toEqual([
      "学校",
      "班级",
      "英语均分",
      "英语人数",
      "排名",
      "全市班级数"
    ]);
  });

  it("表头少一列时补齐后能渲染成表格", () => {
    const md = normalizeAssistantMarkdown(`
| 选科方向 | 1 月期末线上 15 分人数 | 3 月月未达特控线人数 |
| --- | --- | --- | --- |
| 物理类 | 316 | 315 | 41 |
| 合计 | 380 | 379 | 43 |
`);
    const { container } = renderMd(md);
    const table = container.querySelector("table");
    expect(table).toBeTruthy();
    expect(table!.querySelectorAll("thead th").length).toBe(4);
    expect(table!.querySelectorAll("tbody tr")[0].querySelectorAll("td").length).toBe(4);
    expect(table!.querySelectorAll("tbody tr")[0].textContent).toContain("316");
    expect(table!.querySelectorAll("tbody tr")[0].textContent).toContain("41");
  });
});
