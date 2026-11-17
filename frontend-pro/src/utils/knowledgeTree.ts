import type { KnowledgeOption } from "@/api/examLibrary";

export interface KnowledgeTreeNode {
  title: string;
  value: string | number;
  searchText: string;
  selectable?: boolean;
  isLeaf?: boolean;
  children?: KnowledgeTreeNode[];
}

type Group = {
  title: string;
  value: string;
  searchText: string;
  children: Map<string, Group>;
  leaves: KnowledgeOption[];
};

function text(value: string | null | undefined): string {
  return (value || "").trim();
}

function uniqueFilled(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.map(text).filter(Boolean))];
}

function leafNode(item: KnowledgeOption): KnowledgeTreeNode {
  const name = text(item.knowledge_name) || `知识点 ${item.id}`;
  const searchText = [item.stage, item.grade, item.module, item.chapter, item.secondary, name]
    .map(text)
    .filter(Boolean)
    .join(" ");
  return { title: name, value: item.id, searchText, isLeaf: true };
}

function toNodes(groups: Map<string, Group>): KnowledgeTreeNode[] {
  return [...groups.values()].map((group) => ({
    title: group.title,
    value: group.value,
    searchText: group.searchText,
    selectable: false,
    children: [...toNodes(group.children), ...group.leaves.map(leafNode)]
  }));
}

/** 把同一学科的知识点按大概念 / 重要概念 / 次位概念组成树。学段、年级只在出现多个取值时展示。 */
export function buildKnowledgeTree(options: KnowledgeOption[]): KnowledgeTreeNode[] {
  const showStage = uniqueFilled(options.map((item) => item.stage)).length > 1;
  const showGrade = uniqueFilled(options.map((item) => item.grade)).length > 1;
  const hasModule = options.some((item) => text(item.module));
  const hasChapter = options.some((item) => text(item.chapter));
  const root = new Map<string, Group>();
  const rootLeaves: KnowledgeOption[] = [];

  for (const item of options) {
    const levels: Array<{ key: string; title: string }> = [];
    if (showStage) {
      const name = text(item.stage) || "未填写";
      levels.push({ key: `学段:${name}`, title: `学段 · ${name}` });
    }
    if (showGrade) {
      const name = text(item.grade) || "未填写";
      levels.push({ key: `年级:${name}`, title: `年级 · ${name}` });
    }
    if (hasModule) {
      const name = text(item.module) || "未填写";
      levels.push({ key: `大概念:${name}`, title: name });
    }
    if (hasChapter) {
      const name = text(item.chapter) || "未填写";
      levels.push({ key: `重要概念:${name}`, title: name });
    }
    const secondary = text(item.secondary);
    if (secondary) levels.push({ key: `次位概念:${secondary}`, title: secondary });

    if (!levels.length) {
      rootLeaves.push(item);
      continue;
    }

    let bucket = root;
    let path = "";
    let parent: Group | null = null;
    for (const level of levels) {
      path = path ? `${path}/${level.key}` : level.key;
      let group = bucket.get(level.key);
      if (!group) {
        group = {
          title: level.title,
          value: `group:${path}`,
          searchText: level.title,
          children: new Map(),
          leaves: []
        };
        bucket.set(level.key, group);
      }
      parent = group;
      bucket = group.children;
    }
    parent?.leaves.push(item);
  }

  return [...toNodes(root), ...rootLeaves.map(leafNode)];
}
