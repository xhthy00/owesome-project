import { describe, expect, it } from "vitest";
import {
  buildSecondarySpec,
  classifyIntent,
  recommendChartSpec
} from "./chartRecommend";

const REGIONS = ["市直", "广陵", "邗江", "江都", "宝应", "高邮", "仪征"];
const COMPARE_COLUMNS = ["区县", "3月均分", "5月均分"];
const COMPARE_ROWS = REGIONS.map((name, index) => [name, 90 - index * 3, 88 - index * 3]);

describe("classifyIntent", () => {
  it("区分趋势、对比、排名、明细", () => {
    expect(classifyIntent("近两次考试数学均分走势")).toBe("trend");
    expect(classifyIntent("各区县数学均分对比")).toBe("compare");
    expect(classifyIntent("数学均分前 10 的学校")).toBe("ranking");
    expect(classifyIntent("列出临界生名单")).toBe("detail");
  });
});

describe("recommendChartSpec", () => {
  it("两期对比生成分组柱，且 spec 不重复携带 rows", () => {
    const spec = recommendChartSpec("对比各区县 3 月与 5 月数学均分", COMPARE_COLUMNS, COMPARE_ROWS);
    expect(spec?.type).toBe("groupedColumn");
    expect(spec?.encoding).toEqual({ x: "区县", y: ["3月均分", "5月均分"] });
    expect(spec?.dataset.rows).toBeUndefined();
  });

  it("时间趋势生成折线", () => {
    const spec = recommendChartSpec(
      "数学均分走势",
      ["月份", "均分"],
      [
        ["2026-03", 78.6],
        ["2026-04", 77.2],
        ["2026-05", 76.2]
      ]
    );
    expect(spec?.type).toBe("line");
    expect(spec?.encoding.x).toBe("月份");
  });

  it("排名使用指标而不是名次列作为柱长", () => {
    const spec = recommendChartSpec(
      "各校数学均分前 3",
      ["学校", "排名", "均分"],
      [
        ["A", 1, 106.8],
        ["B", 2, 100.9],
        ["C", 3, 96.4]
      ]
    );
    expect(spec?.type).toBe("bar");
    expect(spec?.encoding.y).toEqual(["均分"]);
    expect(spec?.transform?.sort).toEqual({ by: "均分", order: "desc" });
  });

  it("小占比生成环形图，类别过多退化为条形", () => {
    const donut = recommendChartSpec(
      "分数段人数占比",
      ["分数段", "人数"],
      [
        ["140+", 8],
        ["120-140", 22],
        ["100-120", 31],
        ["80-100", 24]
      ]
    );
    expect(donut?.type).toBe("donut");

    const bar = recommendChartSpec(
      "各校人数占比",
      ["学校", "人数"],
      Array.from({ length: 8 }, (_, index) => [`S${index}`, index + 1])
    );
    expect(bar?.type).toBe("bar");
  });

  it("单行结果是 KPI，明细意图保留表格", () => {
    expect(
      recommendChartSpec("全市高三 3 月数学平均分", ["考试", "均分"], [["3月", 78.6]])?.type
    ).toBe("kpi");
    expect(
      recommendChartSpec(
        "列出临界生名单",
        ["学校", "学生", "分数"],
        [
          ["A", "S1", 58],
          ["B", "S2", 57]
        ]
      )?.type
    ).toBe("table");
  });

  it("类别多时提供分布辅图，趋势图不切碎", () => {
    const spec = recommendChartSpec("各校数学均分对比", COMPARE_COLUMNS, COMPARE_ROWS);
    const secondary = buildSecondarySpec(spec, COMPARE_COLUMNS, COMPARE_ROWS);
    expect(secondary?.type).toBe("column");
    expect(secondary?.title).toContain("分布");
    expect(secondary?.dataset.rows?.length).toBeGreaterThan(0);

    const trend = recommendChartSpec(
      "数学均分走势",
      ["月份", "均分"],
      [
        ["2026-01", 78],
        ["2026-02", 77],
        ["2026-03", 76],
        ["2026-04", 75],
        ["2026-05", 74],
        ["2026-06", 73],
        ["2026-07", 72],
        ["2026-08", 71],
        ["2026-09", 70]
      ]
    );
    expect(buildSecondarySpec(trend, ["月份", "均分"], [])).toBeNull();
  });
});
