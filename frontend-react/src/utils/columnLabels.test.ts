import { describe, expect, it } from "vitest";
import { labelColumn } from "./columnLabels";

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
    expect(labelColumn("totally_unknown_alias")).toBe("字段");
    expect(/[a-z_]/i.test(labelColumn("totally_unknown_alias"))).toBe(false);
  });
});
