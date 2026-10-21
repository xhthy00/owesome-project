import { describe, expect, it } from "vitest";
import type { KnowledgeOption } from "@/api/examLibrary";
import { buildKnowledgeTree } from "./knowledgeTree";

function option(partial: Partial<KnowledgeOption> & Pick<KnowledgeOption, "id" | "knowledge_name">): KnowledgeOption {
  return {
    stage: "高中",
    grade: "高三",
    module: "",
    chapter: "",
    secondary: "",
    ...partial
  };
}

describe("buildKnowledgeTree", () => {
  it("按大概念、重要概念、次位概念分层，并保留接口顺序", () => {
    const tree = buildKnowledgeTree([
      option({
        id: 1,
        knowledge_name: "信息理解判断",
        module: "阅读",
        chapter: "信息提取",
        secondary: "信息理解"
      }),
      option({
        id: 2,
        knowledge_name: "论点论据分析",
        module: "阅读",
        chapter: "论述类文本",
        secondary: "论证分析"
      })
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0]?.title).toBe("阅读");
    expect(tree[0]?.selectable).toBe(false);
    const chapters = tree[0]?.children ?? [];
    expect(chapters.map((item) => item.title)).toEqual(["信息提取", "论述类文本"]);
    expect(chapters[0]?.children?.[0]?.title).toBe("信息理解");
    expect(chapters[0]?.children?.[0]?.children?.[0]).toMatchObject({
      title: "信息理解判断",
      value: 1,
      isLeaf: true
    });
  });

  it("没有次位概念时，知识点直接挂在重要概念下", () => {
    const tree = buildKnowledgeTree([
      option({ id: 3, knowledge_name: "文言断句", module: "阅读", chapter: "文言文", secondary: "" })
    ]);
    const chapter = tree[0]?.children?.[0];
    expect(chapter?.title).toBe("文言文");
    expect(chapter?.children?.map((item) => item.title)).toEqual(["文言断句"]);
  });

  it("同一学科有多个年级时，先按年级分开", () => {
    const tree = buildKnowledgeTree([
      option({ id: 1, knowledge_name: "函数单调性", grade: "高一", module: "函数", chapter: "性质" }),
      option({ id: 2, knowledge_name: "导数应用", grade: "高三", module: "导数", chapter: "应用" })
    ]);
    expect(tree.map((item) => item.title)).toEqual(["年级 · 高一", "年级 · 高三"]);
    expect(String(tree[0]?.value)).toContain("高一");
    expect(String(tree[1]?.value)).toContain("高三");
  });

  it("层级字段都为空时，退回平铺的知识点", () => {
    const tree = buildKnowledgeTree([
      option({ id: 8, knowledge_name: "未归类点", stage: "", grade: "", module: "", chapter: "", secondary: "" })
    ]);
    expect(tree).toEqual([
      expect.objectContaining({ title: "未归类点", value: 8, isLeaf: true })
    ]);
  });
});
