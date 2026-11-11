import { describe, expect, it } from "vitest";
import {
  adaptLegacyChartConfig,
  parseChartSpec,
  profileColumns,
  specTitle,
  validateChartSpec
} from "./chartSpec";

const COLUMNS = ["区县", "3月均分", "5月均分"];
const ROWS = [
  ["市直", 88.4, 86.1],
  ["广陵", 82.1, 79.6]
];

describe("chartSpec", () => {
  it("画像区分维度、指标与时间", () => {
    const profiles = profileColumns(["考试", "区县", "均分"], [["3月", "市直", 88.4]]);
    expect(profiles[0].isTime).toBe(true);
    expect(profiles[1].role).toBe("dimension");
    expect(profiles[2].role).toBe("measure");
  });

  it("校验拦截缺失列与非数值 Y 列", () => {
    const spec = {
      version: 1 as const,
      type: "groupedColumn" as const,
      dataset: { columns: [] },
      encoding: { x: "不存在", y: ["区县"] }
    };
    const errors = validateChartSpec(spec, COLUMNS, ROWS);
    expect(errors.some((item) => item.includes("X 轴字段不存在"))).toBe(true);
    expect(errors.some((item) => item.includes("不是数值"))).toBe(true);
  });

  it("标题里混入原始字段时重建中文标题", () => {
    expect(
      specTitle({
        version: 1,
        type: "column",
        title: "recorded_count（按班级）",
        dataset: { columns: [] },
        encoding: { x: "class_name", y: ["recorded_count"] }
      })
    ).toBe("录入人数（按班级）");
  });

  it("解析 spec 与旧 ECharts option", () => {
    const spec = parseChartSpec({
      kind: "spec",
      version: 1,
      type: "column",
      dataset: { columns: [{ name: "区县", role: "dimension", type: "string" }] },
      encoding: { x: "区县", y: ["3月均分"] }
    });
    expect(spec?.type).toBe("column");

    const legacy = adaptLegacyChartConfig("bar", {
      xAxis: { data: ["A", "B"] },
      series: [{ name: "均分", type: "bar", data: [90, 80] }]
    });
    expect(legacy?.type).toBe("column");
    expect(legacy?.dataset.rows).toEqual([
      ["A", 90],
      ["B", 80]
    ]);
  });
});
