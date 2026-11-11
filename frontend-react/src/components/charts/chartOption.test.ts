import { describe, expect, it } from "vitest";
import { recommendChartSpec } from "./chartRecommend";
import { buildChartOption } from "./chartOption";

const COLUMNS = ["区县", "3月均分", "5月均分"];
const ROWS = [
  ["市直", 88.4, 86.1],
  ["广陵", 82.1, 79.6],
  ["邗江", 79.8, 77.2]
];

describe("buildChartOption", () => {
  it("分组柱生成两个系列和类目轴", () => {
    const spec = recommendChartSpec("对比各区县 3 月与 5 月数学均分", COLUMNS, ROWS);
    expect(spec).not.toBeNull();
    const option = buildChartOption(spec!, ROWS, COLUMNS);
    expect(option).not.toBeNull();
    expect((option!.xAxis as { data?: string[] }).data).toEqual(["市直", "广陵", "邗江"]);
    expect(Array.isArray(option!.series)).toBe(true);
    expect((option!.series as unknown[]).length).toBe(2);
  });

  it("横向条形交换坐标轴并反转类目", () => {
    const spec = recommendChartSpec(
      "各校数学均分前 3",
      ["学校", "均分"],
      [
        ["A", 90],
        ["B", 80],
        ["C", 70]
      ]
    );
    expect(spec?.type).toBe("bar");
    const option = buildChartOption(spec!, [["A", 90], ["B", 80], ["C", 70]], ["学校", "均分"]);
    expect((option!.xAxis as { type?: string }).type).toBe("value");
    expect((option!.yAxis as { type?: string; inverse?: boolean }).type).toBe("category");
    expect((option!.yAxis as { inverse?: boolean }).inverse).toBe(true);
  });

  it("KPI 与表格不生成图表", () => {
    const spec = recommendChartSpec("全市数学平均分", ["考试", "均分"], [["3月", 78.6]]);
    expect(spec?.type).toBe("kpi");
    expect(buildChartOption(spec!, [["3月", 78.6]], ["考试", "均分"])).toBeNull();
  });
});
