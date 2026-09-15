import { describe, expect, it } from "vitest";
import {
  normalizeAssistantMarkdown,
  pickCustomerAnswer,
  stripRedundantQueryResultSection
} from "./toolLabels";

function mdRowWidth(line: string): number {
  const t = line.trim();
  return t.slice(1, t.endsWith("|") ? -1 : undefined).split("|").length;
}

describe("stripRedundantQueryResultSection", () => {
  it("结论里已有表时去掉文末查询结果附表", () => {
    const text = `均分 565.22。

| 排名口径 | 全市排名 |
| --- | --- |
| 按选考方向 | 第 43 名 |

### 查询结果

| 口径 | 全市排名 |
| --- | --- |
| 按选考方向 | 43 |
`;
    const out = stripRedundantQueryResultSection(text);
    expect(out).toContain("第 43 名");
    expect(out).not.toContain("查询结果");
  });

  it("只有查询结果表时保留", () => {
    const text = `### 查询结果

| 口径 | 全市排名 |
| --- | --- |
| 按选考方向 | 43 |
`;
    expect(stripRedundantQueryResultSection(text)).toContain("查询结果");
  });
});

describe("normalizeAssistantMarkdown", () => {
  it("表头列数少于数据列时补齐，避免 GFM 整表不渲染", () => {
    const md = `
新华中学 1 月线上 15 分后 3 月未达特控线人数如下。

| 选科方向 | 1 月期末线上 15 分人数 | 3 月月未达特控线人数 |
| --- | --- | --- | --- |
| 物理类 | 316 | 315 | 41 |
| 历史类 | 64 | 64 | 2 |
| 合计 | 380 | 379 | 43 |
`;
    const out = normalizeAssistantMarkdown(md);
    const rows = out.split("\n").filter((l) => l.trim().startsWith("|"));
    expect(rows).toHaveLength(5);
    expect(new Set(rows.map(mdRowWidth)).size).toBe(1);
    expect(mdRowWidth(rows[0])).toBe(4);
    expect(rows[0]).toMatch(/1 月期末线上 15 分人数/);
    expect(rows[0]).toMatch(/3 月月未达特控线人数/);
    expect(rows[2]).toContain("316");
    expect(rows[2]).toContain("41");
  });
});

describe("pickCustomerAnswer", () => {
  it("从 summary 去掉重复查询结果表", () => {
    const summary = `<think>ok</think>

扬州中学高三(10)班均分 565.22。

| 排名口径 | 全市排名 |
| --- | --- |
| 按选考方向 | 第 43 名 |

### 查询结果

| 口径 | 全市排名 |
| --- | --- |
| 按选考方向 | 43 |
`;
    const out = pickCustomerAnswer(summary, "");
    expect(out).toContain("第 43 名");
    expect(out).not.toContain("查询结果");
  });
});
