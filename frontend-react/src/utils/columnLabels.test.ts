import { describe, expect, it } from "vitest";
import { labelColumn, labelColumns } from "./columnLabels";

describe("labelColumn", () => {
  it("把单科排名表头里的字段名译成中文", () => {
    expect(labelColumn("学校")).toBe("学校");
    expect(labelColumn("班级")).toBe("班级");
    expect(labelColumn("均分yy")).toBe("英语均分");
    expect(labelColumn("人数yy")).toBe("英语人数");
    expect(labelColumn("rk")).toBe("排名");
    expect(labelColumn("total_classes")).toBe("全市班级数");
  });

  it("英文列名与带科目后缀的均分仍可译", () => {
    expect(labelColumn("xx")).toBe("学校");
    expect(labelColumn("avg_yy")).toBe("英语均分");
    expect(labelColumn("n_sx")).toBe("数学人数");
    expect(labelColumn("city_rank")).toBe("全市排名");
  });

  it("语义层 KPI 字段不再直接暴露 snake_case", () => {
    expect(labelColumn("own_class_mean")).toBe("本班均分");
    expect(labelColumn("school_grade_mean")).toBe("年级均分");
    expect(labelColumn("own_class_pass_rate")).toBe("本班及格率");
    expect(labelColumn("school_grade_excellent_rate")).toBe("年级优秀率");
    expect(labelColumn("school_grade_rank")).toBe("年级排名");
  });

  it("成绩明细 / 录入统计字段可读", () => {
    expect(labelColumn("exam_batch_id")).toBe("考试批次");
    expect(labelColumn("recorded_count")).toBe("录入人数");
    expect(labelColumn("valid_count")).toBe("有效人数");
    expect(labelColumn("total_score")).toBe("总分");
    expect(labelColumn("average_score")).toBe("平均分");
    expect(labelColumn("min_score")).toBe("最低分");
    expect(labelColumn("max_score")).toBe("最高分");
    expect(labelColumn("pass_count")).toBe("及格人数");
    expect(labelColumn("classified_count")).toBe("已分类人数");
  });

  it("不依赖固定字段也能把任意 snake_case 组合译成中文", () => {
    expect(labelColumn("foo_bar_count")).toBe("人数");
    expect(labelColumn("custom_metric")).toBe("自定义指标");
    expect(labelColumn("ownClassMean")).toBe("本班均分");
  });

  it("译不出来时保留原始列名,不塌缩成同一个占位词", () => {
    // 回归:模型写了 c1/c2 这类占位别名时,旧逻辑把 5 列全渲染成「字段」
    expect(labelColumn("c1")).toBe("c1");
    expect(labelColumn("totally_unknown_alias")).toBe("totally_unknown_alias");
    const labels = labelColumns(["c1", "c2", "c3", "c4", "c5", "row_type", "subject", "item"]);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels.slice(0, 5)).toEqual(["c1", "c2", "c3", "c4", "c5"]);
  });
});

describe("labelColumns", () => {
  it("翻译结果重名时补上原始列名以便区分", () => {
    expect(labelColumns(["avg_score", "mean_score"])).toEqual([
      "均分(avg_score)",
      "均分(mean_score)"
    ]);
  });

  it("无重名时保持原样", () => {
    expect(labelColumns(["class_name", "valid_count", "pass_rate"])).toEqual([
      "班级",
      "有效人数",
      "及格率"
    ]);
  });
});
