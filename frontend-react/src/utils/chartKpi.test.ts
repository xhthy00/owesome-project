import { describe, expect, it } from "vitest";
import { buildKpis } from "./chartKpi";

describe("buildKpis", () => {
  it("单行输出指标卡", () => {
    const kpis = buildKpis(["学校", "均分", "及格率"], [["A", 88.4, 96.2]]);
    expect(kpis).toHaveLength(2);
    expect(kpis[0].label).toBe("均分");
    expect(kpis[1].value).toBe("96.20%");
  });

  it("两期结果给出最新值与差值", () => {
    const kpis = buildKpis(
      ["考试", "均分"],
      [
        ["3月", 78.6],
        ["5月", 76.2]
      ]
    );
    expect(kpis[0].label).toBe("最新均分");
    expect(kpis[0].delta).toBe("-2.40");
    expect(kpis[0].deltaTone).toBe("down");
  });

  it("语义层英文字段转成中文 KPI 标题", () => {
    const kpis = buildKpis(
      ["own_class_mean", "school_grade_mean", "分差"],
      [[123.18, 106.8, 16.38]]
    );
    expect(kpis.map((kpi) => kpi.label)).toEqual(["本班均分", "年级均分", "分差"]);
  });

  it("ID 字段不进入 KPI，成绩字段不再出现英文", () => {
    const columns = [
      "exam_batch_id",
      "recorded_count",
      "valid_count",
      "total_score",
      "average_score",
      "min_score",
      "max_score",
      "pass_count",
      "classified_count",
      "及格率"
    ];
    const rows = [
      [2, 52, 52, 6858, 131.88, 106, 145, 52, 52, 100],
      [2, 48, 48, 6151, 128.15, 107, 146, 48, 48, 98]
    ];
    const kpis = buildKpis(columns, rows);
    const labels = kpis.map((kpi) => kpi.label);
    expect(labels).not.toContain("最高exam_batch_id");
    expect(labels).toContain("平均分");
    expect(labels).toContain("录入人数");
    expect(labels).toContain("有效人数");
    expect(labels.every((label) => !/[a-z]/i.test(label))).toBe(true);
  });

  it("中文批次 / 编号字段也不进入 KPI", () => {
    const kpis = buildKpis(
      ["考试批次", "班级", "平均分"],
      [
        [2, "高三(1)班", 130],
        [2, "高三(2)班", 128]
      ]
    );
    expect(kpis.map((kpi) => kpi.label)).not.toContain("最高考试批次");
    expect(kpis.map((kpi) => kpi.label)).toContain("平均分");
  });

  it("本班标记行优先作为 KPI", () => {
    const kpis = buildKpis(
      ["学校", "标记", "均分"],
      [
        ["A", "", 90],
        ["B", "本班", 80]
      ]
    );
    expect(kpis[0].label).toBe("本班均分");
    expect(kpis[0].value).toBe("80");
  });
});
