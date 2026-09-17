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

  it("宽表被截断时丢掉残表并保留查询结果", () => {
    const text = `全校 19 个班级分学科均分与排名（均为在籍生群体）

| 指标 | 数值 |
| --- | --- |
| 班级数 | 19 |

| 班级 | 人数 | 语文均分 | 数学均分 | 英语均分 | 物理均分 |
| --- | --- | --- | --- | --- | --- |
| 高三(1)班 | 48 | 110 | 98 | 105 | 61 |
| 高三(2)班 | 47 | 109 | 97 | 104 | 60 |
| 高三(12)班

### 查询结果

| 班级 | 人数 | 语文均分 | 数学均分 | 英语均分 | 物理均分 |
| --- | --- | --- | --- | --- | --- |
| 高三(1)班 | 48 | 110 | 98 | 105 | 61 |
| 高三(12)班 | 48 | 110.83 | 98.73 | 105.19 | 60.94 |
| 高三(19)班 | 47 | 108 | 96 | 103 | 59 |
`;
    const out = stripRedundantQueryResultSection(text);
    expect(out).toContain("查询结果");
    expect(out).toContain("高三(19)班");
    expect(out).toContain("班级数");
    expect(out).not.toMatch(/\| 高三\(12\)班\s*$/m);
    expect(out).toMatch(/\| 高三\(12\)班 \| 48 \| 110\.83/);
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

  it("截断宽表时保留完整查询结果", () => {
    const summary = `均分 565。

| 指标 | 数值 |
| --- | --- |
| 班级数 | 19 |

| 班级 | 人数 | 语文均分 | 数学均分 | 英语均分 | 物理均分 |
| --- | --- | --- | --- | --- | --- |
| 高三(1)班 | 48 | 110 | 98 | 105 | 61 |
| 高三(12)班

### 查询结果

| 班级 | 人数 | 语文均分 | 数学均分 | 英语均分 | 物理均分 |
| --- | --- | --- | --- | --- | --- |
| 高三(1)班 | 48 | 110 | 98 | 105 | 61 |
| 高三(19)班 | 47 | 108 | 96 | 103 | 59 |
`;
    const out = pickCustomerAnswer(summary, "");
    expect(out).toContain("查询结果");
    expect(out).toContain("高三(19)班");
    expect(out).toContain("班级数");
  });
});
