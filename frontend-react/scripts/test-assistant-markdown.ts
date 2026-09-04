import assert from "node:assert/strict";
import { normalizeAssistantMarkdown } from "../src/utils/toolLabels";

const emptyCellTable = [
  "| 科目 | 本班均分 | 对照班均 | 分差 | 名次 | 及格率差 | 判定 |",
  "| --- | --- | --- | --- | --- | --- | --- |",
  "| 化学 | 72.1 | 78.4 | -6.3 | 9/12 |  | 薄弱 |",
  "| 语文 | 98.5 | 96.2 | 2.3 | 4/12 | 1.2 | — |",
].join("\n");

const outEmpty = normalizeAssistantMarkdown(emptyCellTable);
assert.equal(
  outEmpty.split("\n").filter((l) => l.includes("化学")).length,
  1,
  `empty cell must stay in one row, got:\n${outEmpty}`
);
assert.match(outEmpty, /\| 化学 \| 72\.1 \| 78\.4 \| -6\.3 \| 9\/12 \|  \| 薄弱 \|/);

const glued = "| 指标 | 数值 | | 均分 | 80.5 |";
const outGlued = normalizeAssistantMarkdown(glued);
assert.equal(outGlued.split("\n").filter((l) => l.trim().startsWith("|")).length, 2);
assert.match(outGlued, /\| 指标 \| 数值 \|/);
assert.match(outGlued, /\| 均分 \| 80\.5 \|/);

const twoTables = [
  "**关键指标**",
  "| 指标 | 数值 |",
  "| --- | --- |",
  "| 参考人数 | 60 |",
  "",
  "**各科位置**",
  "| 科目 | 本班均分 | 对照班均 | 分差 | 名次 | 及格率差 | 判定 |",
  "| --- | --- | --- | --- | --- | --- | --- |",
  "| 英语 | 88 | 93 | -5 | 8/12 |  | 薄弱 |",
].join("\n");
const outTwo = normalizeAssistantMarkdown(twoTables);
assert.equal(outTwo.split("\n").filter((l) => l.includes("英语")).length, 1);
assert.match(outTwo, /\| 英语 \| 88 \| 93 \| -5 \| 8\/12 \|  \| 薄弱 \|/);

console.log("assistant markdown table tests passed");
