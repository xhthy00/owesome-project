import { ArrowDownOutlined, ArrowLeftOutlined, ArrowUpOutlined, DeleteOutlined, FileTextOutlined, HolderOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Input,
  InputNumber,
  Popover,
  Progress,
  Rate,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  TreeSelect,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentUser, type CurrentUser } from "@/api/auth";
import {
  canMaintainExam,
  examLibraryApi,
  type ExamDetail,
  type KnowledgeLink,
  type KnowledgeOption,
  type MatchItem,
  type MatchSuggestion,
  type MatchTask
} from "@/api/examLibrary";
import { buildKnowledgeTree, type KnowledgeTreeNode } from "@/utils/knowledgeTree";
import { adjustWeight, shareWeights, suggestWeights } from "@/utils/knowledgeWeights";

const QUESTION_TYPES = ["单选题", "多选题", "填空题", "解答题", "写作"];

function stemBlocks(content: string): Array<{ kind: "text"; text: string } | { kind: "table"; rows: string[][] }> {
  const blocks: Array<{ kind: "text"; text: string } | { kind: "table"; rows: string[][] }> = [];
  let text: string[] = [];
  let table: string[][] = [];
  const flushText = () => {
    const joined = text.join("\n").trim();
    if (joined) blocks.push({ kind: "text", text: joined });
    text = [];
  };
  const flushTable = () => {
    if (table.length) blocks.push({ kind: "table", rows: table });
    table = [];
  };
  for (const line of (content || "").split(/\r?\n/)) {
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushText();
      if (!/^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$/.test(line)) {
        table.push(
          line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim())
        );
      }
    } else {
      flushTable();
      text.push(line);
    }
  }
  flushText();
  flushTable();
  return blocks;
}

function StemField({
  value,
  onChange,
  readOnly
}: {
  value: string;
  onChange: (next: string) => void;
  readOnly?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const blocks = stemBlocks(value);
  const hasTable = blocks.some((block) => block.kind === "table");
  if (!readOnly && (!hasTable || editing)) {
    return (
      <Input.TextArea
        value={value}
        autoFocus={editing}
        autoSize={{ minRows: 2 }}
        className="!max-h-none !w-full !overflow-hidden !rounded-lg !border-[#e4eef8] !bg-[#f8fbff] !px-3 !py-2 !text-[13px] !leading-6"
        placeholder="题干"
        onBlur={() => setEditing(false)}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  return (
    <div
      role="button"
      tabIndex={0}
      title="点击编辑题干"
      className="w-full cursor-text rounded-lg border border-[#e4eef8] bg-[#f8fbff] px-3 py-2 text-left"
      onClick={() => {
        if (!readOnly) setEditing(true);
      }}
      onKeyDown={(event) => {
        if (readOnly) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setEditing(true);
        }
      }}
    >
      {blocks.map((block, index) =>
        block.kind === "text" ? (
          <div key={index} className="whitespace-pre-wrap text-sm leading-6">
            {block.text}
          </div>
        ) : (
          <div key={index} className="max-w-full overflow-x-auto">
            <table className="border-collapse text-xs">
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex} className="border border-[#cbd5e1] px-1.5 py-1 whitespace-nowrap">
                        {cell || "\u00a0"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}
type ReportPick = { add: number[]; remove: number[]; weights: Record<number, number> };

function orderedIds(ids: number[], suggestions: MatchSuggestion[]): number[] {
  const rank = new Map(suggestions.map((item, index) => [item.knowledge_id, index]));
  return [...ids].sort((left, right) => (rank.get(left) ?? 99) - (rank.get(right) ?? 99));
}

function weightsFor(ids: number[], suggestions: MatchSuggestion[]): Record<number, number> {
  const ordered = orderedIds(ids, suggestions).slice(0, 3);
  const shares = suggestWeights(
    ordered.map((id) => suggestions.find((item) => item.knowledge_id === id)?.confidence || 0)
  );
  return Object.fromEntries(ordered.map((id, index) => [id, shares[index]]));
}

function topIds(suggestions: MatchSuggestion[]): number[] {
  return [...suggestions]
    .sort((left, right) => (right.confidence || 0) - (left.confidence || 0))
    .slice(0, 3)
    .map((item) => item.knowledge_id);
}

function formatWeight(value: number): string {
  return String(Math.round(value * 10) / 10);
}

type KnowledgeDraftLink = { knowledge_id: number; weight: number };

function draftLinks(links: KnowledgeLink[]): KnowledgeDraftLink[] {
  if (!links.length) return [];
  const weights = links.map((link) => Number(link.weight) || 0);
  const shares = weights.some((value) => value <= 0) ? shareWeights(links.map(() => 1)) : shareWeights(weights);
  return links.map((link, index) => ({ knowledge_id: link.knowledge_id, weight: shares[index] ?? 0 }));
}

function nextLinks(previous: KnowledgeDraftLink[], ids: number[]): KnowledgeDraftLink[] {
  const previousById = new Map(previous.map((item) => [item.knowledge_id, item.weight]));
  const hasNew = ids.some((id) => !previousById.has(id));
  const weights = ids.map((id) => previousById.get(id) ?? 0);
  const shares = hasNew || weights.some((value) => value <= 0) ? shareWeights(ids.map(() => 1)) : shareWeights(weights);
  return ids.map((id, index) => ({ knowledge_id: id, weight: shares[index] ?? 0 }));
}

function knowledgeLevels(option: KnowledgeOption | undefined, fallbackId: number): string[] {
  const name = (option?.knowledge_name || "").trim() || `知识点 ${fallbackId}`;
  const parents = [option?.module, option?.chapter, option?.secondary]
    .map((item) => (item || "").trim())
    .filter(Boolean);
  return [...parents, name];
}

function KnowledgePath({ levels }: { levels: string[] }) {
  const name = levels[levels.length - 1] || "";
  const trail = levels.slice(0, -1);
  return (
    <span className="min-w-0 flex-1 whitespace-normal break-words text-[13px] leading-6">
      {trail.map((part, index) => (
        <span key={`${part}-${index}`} className="text-[#64748b]">
          {part}
          <span className="px-1 text-[#cbd5e1]">-</span>
        </span>
      ))}
      <span className="font-medium text-[#1e293b] dark:text-[#e2e8f0]">{name}</span>
    </span>
  );
}

function KnowledgeLinksCell({
  links,
  tree,
  options,
  onChange,
  readOnly
}: {
  links: KnowledgeDraftLink[];
  tree: KnowledgeTreeNode[];
  options: KnowledgeOption[];
  onChange: (links: KnowledgeDraftLink[]) => void;
  readOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const popupRoot = useRef<HTMLDivElement>(null);
  const byId = useMemo(() => new Map(options.map((item) => [item.id, item])), [options]);
  const weightMax = links.length <= 1 ? 1 : Math.round((1 - (links.length - 1) * 0.1) * 10) / 10;
  const levelsOf = (knowledgeId: number) => knowledgeLevels(byId.get(knowledgeId), knowledgeId);

  const changeIds = (value: number | number[]) => {
    const picked = (Array.isArray(value) ? value : [value]).filter((id): id is number => typeof id === "number");
    if (picked.length > 3 && picked.length > links.length) {
      message.warning("一道题最多关联 3 个知识点");
      return;
    }
    onChange(nextLinks(links, picked));
  };

  const changeWeight = (knowledgeId: number, value: number) => {
    const ids = links.map((item) => item.knowledge_id);
    const next = adjustWeight(
      links.map((item) => item.weight),
      ids.indexOf(knowledgeId),
      value
    );
    onChange(ids.map((id, index) => ({ knowledge_id: id, weight: next[index] ?? 0 })));
  };

  const editor = (
    <div ref={popupRoot} className="w-[420px]">
      <TreeSelect
        className="w-full"
        multiple
        showSearch
        treeExpandAction="click"
        filterTreeNode={(input, node) => {
          const keyword = input.trim().toLowerCase();
          if (!keyword) return true;
          const searchable = String((node as { searchText?: string }).searchText || node.title || "");
          return searchable.toLowerCase().includes(keyword);
        }}
        placeholder="搜索知识点"
        popupMatchSelectWidth={false}
        popupClassName="knowledge-tree-dropdown"
        getPopupContainer={() => popupRoot.current || document.body}
        dropdownStyle={{ minWidth: 360, maxWidth: 480 }}
        listHeight={320}
        treeData={tree}
        value={links.map((item) => item.knowledge_id)}
        onChange={changeIds}
      />
      {links.length > 1 ? (
        <div className="mt-3 space-y-2 border-t border-[#eef2f6] pt-3">
          {links.map((link) => (
            <div key={link.knowledge_id} className="flex items-start gap-2">
              <KnowledgePath levels={levelsOf(link.knowledge_id)} />
              <InputNumber
                size="small"
                min={0.1}
                max={weightMax}
                step={0.1}
                precision={1}
                value={link.weight}
                aria-label="权重"
                className="mt-0.5 w-16 shrink-0"
                onChange={(value) => {
                  if (value == null) return;
                  changeWeight(link.knowledge_id, Number(value));
                }}
              />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );

  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      open={open}
      destroyOnHidden
      onOpenChange={(next) => {
        if (readOnly) return;
        if (!next && document.querySelector(".knowledge-tree-dropdown:not(.ant-select-dropdown-hidden)")) return;
        setOpen(next);
      }}
      content={editor}
    >
      <button
        type="button"
        disabled={readOnly}
        title={readOnly ? undefined : links.length ? "调整知识点" : "关联知识点"}
        className="w-full text-left disabled:cursor-default"
      >
        {links.length ? (
          <span className="flex flex-col gap-1.5">
            {links.map((link) => {
              const levels = levelsOf(link.knowledge_id);
              const name = levels[levels.length - 1] || "";
              const trail = levels.slice(0, -1);
              return (
                <span key={link.knowledge_id} className="block rounded-lg bg-[#f3f8ff] px-2.5 py-1.5 dark:bg-[#1e3a5f]">
                  {trail.length ? (
                    <span className="block whitespace-normal break-words text-[11px] leading-4 text-[#7b8da6]">
                      {trail.join(" - ")}
                    </span>
                  ) : null}
                  <span className="mt-0.5 flex items-start gap-2">
                    <span className="min-w-0 flex-1 whitespace-normal break-words text-[13px] font-medium leading-5 text-[#16324f] dark:text-[#e2e8f0]">
                      {name}
                    </span>
                    {links.length > 1 ? (
                      <span className="shrink-0 rounded-full bg-white px-1.5 text-[11px] leading-5 tabular-nums text-[#0C75FC] ring-1 ring-[#d6e8ff] dark:bg-[#141923]">
                        {formatWeight(link.weight)}
                      </span>
                    ) : null}
                  </span>
                </span>
              );
            })}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-lg border border-dashed border-[#c5ddfb] bg-[#f4f8ff] px-2.5 py-1.5 text-[13px] text-[#0C75FC]">
            <PlusOutlined className="text-[11px]" />
            关联知识点
          </span>
        )}
      </button>
    </Popover>
  );
}

function contentPreview(content: string): string {
  if (!content) return "（无题干）";
  return content.length > 80 ? `${content.slice(0, 80)}…` : content;
}

function toggleId(current: number[] | undefined, id: number): number[] {
  const next = new Set(current || []);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return [...next];
}

type Draft = {
  key: string;
  id: number | null;
  question_no: string;
  question_type: string | null;
  question_score: number | null;
  content: string;
  knowledge_links: KnowledgeDraftLink[];
};

function reorderQuestions<T>(rows: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return rows;
  const next = rows.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

let draftSeq = 0;

function questionNoTextWidth(value: string): number {
  let width = 0;
  for (const char of value) {
    width += char.charCodeAt(0) > 255 ? 14 : 8;
  }
  return width;
}

function blankDraft(): Draft {
  draftSeq += 1;
  return {
    key: `new-${draftSeq}`,
    id: null,
    question_no: "",
    question_type: "解答题",
    question_score: null,
    content: "",
    knowledge_links: []
  };
}

function insertQuestionAfter(rows: Draft[], index: number): Draft[] {
  const next = rows.slice();
  next.splice(index + 1, 0, blankDraft());
  return next;
}

function toDrafts(detail: ExamDetail): Draft[] {
  return detail.questions.map((item) => ({
    key: String(item.id),
    id: item.id,
    question_no: item.question_no,
    question_type: item.question_type,
    question_score: item.question_score,
    content: item.content || "",
    knowledge_links: draftLinks(item.knowledge_links)
  }));
}

export default function ExamQuestionPage() {
  const router = useRouter();
  const examId = Number(router.query.id);
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [rows, setRows] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [task, setTask] = useState<MatchTask | null>(null);
  const [reportItems, setReportItems] = useState<MatchItem[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const [selected, setSelected] = useState<Record<number, ReportPick>>({});
  const started = useRef("");
  const stopWatch = useRef<(() => void) | null>(null);
  const dragFrom = useRef<number | null>(null);
  const weightWarnAt = useRef(0);

  const load = useCallback(async () => {
    if (!Number.isFinite(examId) || examId <= 0) return;
    setLoading(true);
    try {
      const data = await examLibraryApi.detail(examId);
      setDetail(data);
      setRows(toDrafts(data));
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载题目失败");
    } finally {
      setLoading(false);
    }
  }, [examId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, []);

  useEffect(() => () => stopWatch.current?.(), []);

  const openReport = useCallback(async (taskId?: number) => {
    const report = await examLibraryApi.report(examId, taskId);
    setTask(report.task);
    setReportItems(report.items);
    setSelected({});
    setDrawerOpen(true);
  }, [examId]);

  const watchTask = useCallback(
    (taskId: number) => {
      let stopped = false;
      const tick = async () => {
        if (stopped) return;
        try {
          const current = await examLibraryApi.task(taskId);
          if (stopped) return;
          setTask(current);
          if (current.status === "running") {
            window.setTimeout(tick, 1500);
            return;
          }
          await openReport(taskId);
        } catch (error) {
          if (!stopped) message.error(error instanceof Error ? error.message : "读取核对进度失败");
        }
      };
      void tick();
      return () => {
        stopped = true;
      };
    },
    [openReport]
  );

  const startMatch = useCallback(
    async (questionId?: number) => {
      try {
        const created = questionId
          ? await examLibraryApi.startQuestionMatch(examId, questionId)
          : await examLibraryApi.startExamMatch(examId);
        setTask(created);
        setDrawerOpen(true);
        setReportItems([]);
        stopWatch.current?.();
        stopWatch.current = watchTask(created.id);
      } catch (error) {
        message.error(error instanceof Error ? error.message : "开始核对失败");
      }
    },
    [examId, watchTask]
  );

  useEffect(() => {
    if (!detail || !router.isReady) return;
    const mode = String(router.query.check || router.query.report || "");
    const token = `${examId}:${mode}`;
    if (!mode || started.current === token) return;
    started.current = token;
    if (router.query.check) {
      void startMatch();
      return;
    }
    void openReport().catch((error) => {
      message.error(error instanceof Error ? error.message : "读取核对报告失败");
    });
  }, [detail, examId, openReport, router.isReady, router.query.check, router.query.report, startMatch]);

  const scoreSum = useMemo(
    () => Math.round(rows.reduce((sum, row) => sum + (Number(row.question_score) || 0), 0) * 100) / 100,
    [rows]
  );
  const linkedCount = rows.filter((row) => row.knowledge_links.length > 0).length;
  const examScore = detail?.exam.exam_score ?? 0;
  const scoreMismatch = Boolean(examScore) && scoreSum !== examScore;
  const knowledgeOptions = detail?.knowledge_options ?? [];
  const knowledgeTree = useMemo(() => buildKnowledgeTree(knowledgeOptions), [knowledgeOptions]);

  const updateRow = (key: string, patch: Partial<Draft>) => {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const save = async () => {
    setSaving(true);
    try {
      const saved = await examLibraryApi.saveQuestions(
        examId,
        rows.map((row) => ({
          id: row.id,
          question_no: row.question_no.trim(),
          question_type: row.question_type,
          question_score: Number(row.question_score) || 0,
          content: row.content,
          knowledge_links: row.knowledge_links.map((link) => ({
            knowledge_id: link.knowledge_id,
            weight: link.weight
          }))
        }))
      );
      setRows(
        saved.questions.map((item) => ({
          key: String(item.id),
          id: item.id,
          question_no: item.question_no,
          question_type: item.question_type,
          question_score: item.question_score,
          content: item.content || "",
          knowledge_links: draftLinks(item.knowledge_links)
        }))
      );
      message.success("试卷已保存");
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "保存试卷失败");
    } finally {
      setSaving(false);
    }
  };

  const writable = canMaintainExam(currentUser, detail?.exam);
  const questionNoWidth = Math.min(
    360,
    Math.max(
      128,
      rows.reduce((max, row) => Math.max(max, questionNoTextWidth(row.question_no)), 0) + 36
    )
  );

  const columns: ColumnsType<Draft> = [
    {
      title: "题号",
      width: questionNoWidth,
      render: (_, row) => (
        <Input
          className="w-full !rounded-lg !border-[#e4eef8] !bg-[#eef5ff] !text-center !font-medium !text-[#0C75FC]"
          value={row.question_no}
          disabled={!writable}
          onChange={(event) => updateRow(row.key, { question_no: event.target.value })}
        />
      )
    },
    {
      title: "题型",
      width: 100,
      render: (_, row) => (
        <Select
          className="w-full [&_.ant-select-selector]:!rounded-lg [&_.ant-select-selector]:!border-[#e4eef8]"
          allowClear
          disabled={!writable}
          value={row.question_type || undefined}
          options={QUESTION_TYPES.map((item) => ({ value: item, label: item }))}
          onChange={(value) => updateRow(row.key, { question_type: value || null })}
        />
      )
    },
    {
      title: "分值",
      width: 72,
      render: (_, row) => (
        <InputNumber
          className="w-full !rounded-lg !border-[#e4eef8]"
          min={0}
          step={0.5}
          disabled={!writable}
          value={row.question_score ?? undefined}
          onChange={(value) => updateRow(row.key, { question_score: value })}
        />
      )
    },
    {
      title: "题干",
      width: "28%",
      onCell: () => ({ style: { whiteSpace: "normal", verticalAlign: "middle" } }),
      render: (_, row) => (
        <StemField readOnly={!writable} value={row.content} onChange={(content) => updateRow(row.key, { content })} />
      )
    },
    {
      title: "知识点",
      width: "40%",
      onCell: () => ({ style: { whiteSpace: "normal", verticalAlign: "middle" } }),
      render: (_, row) => (
        <KnowledgeLinksCell
          links={row.knowledge_links}
          tree={knowledgeTree}
          options={knowledgeOptions}
          readOnly={!writable}
          onChange={(knowledge_links) => updateRow(row.key, { knowledge_links })}
        />
      )
    },
    {
      title: "操作",
      width: 208,
      render: (_, row, index) => (
        <Space size={0} className="whitespace-nowrap">
          <span
            draggable
            aria-label="拖动调整顺序"
            className="inline-flex cursor-grab items-center px-1 text-[#94a3b8] active:cursor-grabbing"
            onDragStart={(event) => {
              dragFrom.current = index;
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", String(index));
            }}
          >
            <HolderOutlined />
          </span>
          <Button
            type="text"
            size="small"
            aria-label="上移"
            disabled={index === 0}
            icon={<ArrowUpOutlined />}
            onClick={() => setRows((current) => reorderQuestions(current, index, index - 1))}
          />
          <Button
            type="text"
            size="small"
            aria-label="下移"
            disabled={index >= rows.length - 1}
            icon={<ArrowDownOutlined />}
            onClick={() => setRows((current) => reorderQuestions(current, index, index + 1))}
          />
          <Tooltip title="添加下一题">
            <Button
              type="text"
              size="small"
              aria-label="添加下一题"
              icon={<PlusOutlined />}
              onClick={() => setRows((current) => insertQuestionAfter(current, index))}
            />
          </Tooltip>
          <Button
            type="link"
            size="small"
            disabled={!row.id}
            onClick={() => row.id && void startMatch(row.id)}
          >
            核对
          </Button>
          <Button type="link" size="small" danger onClick={() => setRows((current) => current.filter((item) => item.key !== row.key))}>
            删除
          </Button>
        </Space>
      )
    }
  ];

  const totalSuggestions = reportItems.reduce(
    (sum, item) => sum + item.add_suggestions.length + item.remove_suggestions.length,
    0
  );
  const selectedCount = Object.values(selected).reduce(
    (sum, pick) => sum + pick.add.length + pick.remove.length,
    0
  );

  const toggleAdd = (itemId: number, knowledgeId: number, suggestions: MatchSuggestion[]) => {
    if (adopting) return;
    setSelected((current) => {
      const pick = current[itemId] || { add: [], remove: [], weights: {} };
      const exists = pick.add.includes(knowledgeId);
      if (!exists && pick.add.length >= 3) {
        const now = Date.now();
        if (now - weightWarnAt.current > 400) {
          weightWarnAt.current = now;
          message.warning("一道题最多关联 3 个知识点");
        }
        return current;
      }
      const add = exists
        ? pick.add.filter((id) => id !== knowledgeId)
        : orderedIds([...pick.add, knowledgeId], suggestions);
      return {
        ...current,
        [itemId]: { add, remove: pick.remove, weights: weightsFor(add, suggestions) }
      };
    });
  };

  const toggleRemove = (itemId: number, knowledgeId: number) => {
    if (adopting) return;
    setSelected((current) => {
      const pick = current[itemId] || { add: [], remove: [], weights: {} };
      return {
        ...current,
        [itemId]: { ...pick, remove: toggleId(pick.remove, knowledgeId) }
      };
    });
  };

  const changeWeight = (itemId: number, knowledgeId: number, value: number, suggestions: MatchSuggestion[]) => {
    if (adopting) return;
    setSelected((current) => {
      const pick = current[itemId] || { add: [], remove: [], weights: {} };
      const ids = orderedIds(pick.add, suggestions);
      const values = ids.map((id) => pick.weights[id] ?? 0);
      const next = adjustWeight(values, ids.indexOf(knowledgeId), value);
      return {
        ...current,
        [itemId]: {
          ...pick,
          weights: Object.fromEntries(ids.map((id, index) => [id, next[index]]))
        }
      };
    });
  };

  const selectAll = () => {
    if (adopting) return;
    const next: Record<number, ReportPick> = {};
    reportItems.forEach((item) => {
      const add = topIds(item.add_suggestions);
      next[item.id] = {
        add,
        remove: item.remove_suggestions.map((suggestion) => suggestion.knowledge_id),
        weights: weightsFor(add, item.add_suggestions)
      };
    });
    setSelected(next);
  };

  const clearAll = () => {
    if (adopting) return;
    setSelected({});
    message.info("已取消全部勾选");
  };

  const adopt = async () => {
    if (!task || adopting) return;
    const operations = reportItems
      .map((item) => {
        const add = selected[item.id]?.add || [];
        const remove = new Set(selected[item.id]?.remove || []);
        if (add.length >= 3) {
          item.current_links.forEach((link) => {
            if (!add.includes(link.knowledge_id)) remove.add(link.knowledge_id);
          });
        }
        return {
          item_id: item.id,
          add_ids: add,
          remove_ids: [...remove],
          weights: Object.fromEntries(
            add.map((id) => [String(id), selected[item.id]?.weights[id] ?? 0])
          )
        };
      })
      .filter((item) => item.add_ids.length || item.remove_ids.length);
    if (!operations.length) {
      message.info("没有可采纳的建议");
      return;
    }
    setAdopting(true);
    try {
      const result = await examLibraryApi.apply(task.id, operations);
      if (result.errors.length) {
        message.warning(result.errors.join("；"));
        await openReport(task.id);
        return;
      }
      message.success(`已采纳 ${result.success} 题`);
      setSelected({});
      setDrawerOpen(false);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "采纳建议失败");
      return;
    } finally {
      setAdopting(false);
    }
    void load();
  };

  const hasScores = Boolean(detail && (detail.exam.score_count || detail.exam.detail_count));

  return (
    <div className="dbgpt-ui-font flex h-full min-h-0 flex-col overflow-hidden bg-[#f5f7fb] p-6 dark:bg-transparent">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]">
        <div className="shrink-0 border-b border-[#eef2f6] bg-[linear-gradient(180deg,#f3f8ff_0%,#ffffff_78%)] px-5 py-4 dark:border-[#334155] dark:bg-none">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-3">
              <Link href="/construct/education/exam-library" aria-label="返回试卷库" className="mt-2.5 text-[#0C75FC]">
                <ArrowLeftOutlined />
              </Link>
              <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#e8f3ff] text-[18px] text-[#0C75FC]">
                <FileTextOutlined />
              </span>
              <div className="min-w-0">
                <Typography.Title level={4} className="!mb-0">
                  {detail ? `${detail.exam.exam_name} · ${detail.exam.subject}` : "题目明细"}
                </Typography.Title>
                <Typography.Text type="secondary" className="text-[13px]">
                  {detail?.exam.exam_batch_name || "题目、分值与知识点"}
                </Typography.Text>
                <div className="mt-2 flex flex-wrap gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white/80 px-2.5 py-1 text-xs ring-1 ring-[#e5e7eb] dark:bg-[#141923] dark:ring-[#334155]">
                    <span className="text-[#64748b]">题目</span>
                    <span className="font-medium tabular-nums text-[#1e293b] dark:text-[#e2e8f0]">{rows.length}</span>
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white/80 px-2.5 py-1 text-xs ring-1 ring-[#e5e7eb] dark:bg-[#141923] dark:ring-[#334155]">
                    <span className="text-[#64748b]">已关联</span>
                    <span className={`font-medium tabular-nums ${rows.length && linkedCount === rows.length ? "text-[#15803d]" : "text-[#0C75FC]"}`}>
                      {linkedCount}/{rows.length}
                    </span>
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white/80 px-2.5 py-1 text-xs ring-1 ring-[#e5e7eb] dark:bg-[#141923] dark:ring-[#334155]">
                    <span className="text-[#64748b]">小题分</span>
                    <span className={`font-medium tabular-nums ${scoreMismatch ? "text-[#c2410c]" : "text-[#1e293b] dark:text-[#e2e8f0]"}`}>
                      {examScore ? `${scoreSum}/${examScore}` : scoreSum}
                    </span>
                  </span>
                </div>
              </div>
            </div>
            <Space wrap>
              {writable ? <Button onClick={() => setRows((current) => [...current, blankDraft()])}>添加题目</Button> : null}
              {writable && detail?.exam.exam_batch_id ? (
                <Button
                  onClick={() =>
                    router.push(
                      `/construct/education/exam-library/import?batchId=${detail.exam.exam_batch_id}&subject=${encodeURIComponent(detail.exam.subject)}`
                    )
                  }
                >
                  重新导入
                </Button>
              ) : null}
              {writable ? <Button onClick={() => void startMatch()}>知识点核对</Button> : null}
              <Button onClick={() => void openReport().catch((error) => message.error(error instanceof Error ? error.message : "读取核对报告失败"))}>
                核对报告
              </Button>
              {writable ? (
                <Button type="primary" loading={saving} onClick={() => void save()}>
                  保存试卷
                </Button>
              ) : null}
            </Space>
          </div>
          {hasScores || scoreMismatch ? (
            <div className="mt-3 rounded-lg bg-[#fff7ed] px-3 py-2 text-xs leading-5 text-[#9a3412]">
              {hasScores ? (
                <div>
                  该试卷已有 {detail?.exam.score_count || 0} 条成绩、{detail?.exam.detail_count || 0} 条小题分。修改题目不会自动改成绩，已有成绩的试卷不能删除。
                </div>
              ) : null}
              {scoreMismatch ? <div>小题分合计 {scoreSum}，与卷面满分 {examScore} 不一致。</div> : null}
            </div>
          ) : null}
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-5 pb-4">
          <Table
            rowKey="key"
            size="middle"
            loading={loading}
            columns={writable ? columns : columns.filter((column) => column.title !== "操作")}
            dataSource={rows}
            pagination={false}
            tableLayout="fixed"
            className="[&_.ant-table]:!w-full [&_.ant-table-container]:!overflow-visible [&_.ant-table-content]:!overflow-visible [&_.ant-table-tbody>tr:hover>td]:!bg-[#f9fbfe] [&_.ant-table-tbody>tr>td]:!border-[#eef3f8] [&_.ant-table-tbody>tr>td]:!py-3.5 [&_.ant-table-thead>tr>th]:!sticky [&_.ant-table-thead>tr>th]:!top-0 [&_.ant-table-thead>tr>th]:!z-10 [&_.ant-table-thead>tr>th]:!border-b-[#d6e6f8] [&_.ant-table-thead>tr>th]:!bg-[#f7f9fc] [&_.ant-table-thead>tr>th]:!font-medium [&_.ant-table-thead>tr>th]:!py-3 [&_.ant-table-thead>tr>th]:!text-[#475569] [&_td]:!align-middle [&_td]:whitespace-normal dark:[&_.ant-table-thead>tr>th]:!bg-[#1c2430]"
            onRow={(_row, index) => ({
              onDragOver: (event) => {
                if (writable) event.preventDefault();
              },
              onDrop: () => {
                if (!writable || index == null || dragFrom.current == null) return;
                const from = dragFrom.current;
                dragFrom.current = null;
                setRows((current) => reorderQuestions(current, from, index));
              }
            })}
          />
        </div>
      </div>
      <Drawer
        title="知识点核对报告"
        width={520}
        open={drawerOpen}
        closable={!adopting}
        maskClosable={!adopting}
        keyboard={!adopting}
        onClose={() => {
          if (!adopting) setDrawerOpen(false);
        }}
        extra={
          task?.status === "running" ? (
            <Button onClick={() => void examLibraryApi.cancel(task.id)}>取消</Button>
          ) : null
        }
        footer={
          <div className="flex items-center justify-end gap-2">
            {totalSuggestions ? (
              <span className="mr-auto text-xs text-[#64748b]">共 {totalSuggestions} 条建议</span>
            ) : null}
            <Button disabled={adopting} onClick={() => setDrawerOpen(false)}>关闭</Button>
            {writable ? (
              <Button type="primary" loading={adopting} disabled={!selectedCount} onClick={() => void adopt()}>
                {adopting ? "正在写入" : `采纳选中（${selectedCount}）`}
              </Button>
            ) : null}
          </div>
        }
      >
        {task?.status === "running" ? (
          <Progress percent={task.total ? Math.round((task.done / task.total) * 100) : 0} />
        ) : null}
        {task ? (
          <div className="mb-4 rounded-lg bg-[#eff6ff] px-3 py-2.5 dark:bg-[#1e3a5f]">
            <Tag color={task.status === "done" ? "green" : task.status === "failed" ? "red" : "orange"}>
              {task.status === "done" ? "已完成" : task.status === "failed" ? "失败" : task.status === "running" ? "进行中" : task.status}
            </Tag>
            {task.summary ? <p className="mb-0 mt-2 text-[13px]">{task.summary}</p> : null}
          </div>
        ) : null}
        {task?.error ? <Alert className="mb-3" type="error" showIcon message={task.error} /> : null}
        {reportItems.length ? (
          <div className="sticky top-0 z-10 mb-4 flex items-center justify-between gap-3 rounded-lg border border-[#e5e7eb] bg-white px-3 py-2.5 dark:border-[#334155] dark:bg-[#141923]">
            <div className="flex min-w-0 items-center gap-2">
              <span className="text-[13px] font-semibold">批量操作</span>
              {selectedCount ? (
                <span className="rounded-full bg-[#eff6ff] px-2 text-xs leading-[18px] text-[#0C75FC]">已选 {selectedCount}</span>
              ) : (
                <span className="text-xs text-[#64748b]">{totalSuggestions} 条建议</span>
              )}
            </div>
            <Space size={8}>
              <Button disabled={adopting || !selectedCount} onClick={clearAll}>全部勾除</Button>
              <Button type="primary" disabled={adopting || !totalSuggestions} onClick={selectAll}>全部勾选</Button>
            </Space>
          </div>
        ) : null}
        {!task && !reportItems.length ? <Typography.Text type="secondary">暂无核对记录</Typography.Text> : null}
        <div className={`space-y-3${adopting ? " pointer-events-none" : ""}`}>
          {reportItems.map((item) => {
            const pick = selected[item.id] || { add: [], remove: [], weights: {} };
            return (
              <div key={item.id} className="rounded-lg border border-[#e5e7eb] p-3.5 dark:border-[#334155]">
                <div className="mb-2 flex items-center gap-2">
                  <span className="text-sm font-semibold">{item.question_no || "-"} 题</span>
                  {item.verdict === "failed" ? <Tag color="red">失败</Tag> : null}
                  {item.adopted ? <span className="ml-auto text-xs text-[#16a34a]">已采纳</span> : null}
                </div>
                <p className="mb-3 text-[13px] leading-6 text-[#64748b]">{contentPreview(item.content)}</p>
                <div className="text-xs font-semibold text-[#64748b]">当前关联</div>
                {item.current_links.length ? (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {item.current_links.map((link) => {
                      const replaced = pick.add.length >= 3 && !pick.add.includes(link.knowledge_id);
                      const removing = replaced || pick.remove.includes(link.knowledge_id);
                      return (
                        <Tag
                          key={link.knowledge_id}
                          className={removing ? "!cursor-pointer !text-[#94a3b8] line-through" : "!cursor-pointer"}
                          onClick={() => {
                            if (!replaced) toggleRemove(item.id, link.knowledge_id);
                          }}
                        >
                          {link.knowledge_name || `ID:${link.knowledge_id}`}
                          {link.weight != null ? `(${formatWeight(link.weight)})` : ""}
                        </Tag>
                      );
                    })}
                  </div>
                ) : (
                  <div className="mt-1 text-[13px] text-[#64748b]">无</div>
                )}
                {item.add_suggestions.length > 0 && item.current_links.length + item.add_suggestions.length > 3 ? (
                  <div className="mt-1 text-xs leading-5 text-[#64748b]">
                    当前已有 {item.current_links.length} 个关联。勾选满 3 条建议再采纳，会用这些建议替换当前关联；只采纳其中一部分时，请先点掉不需要保留的当前关联。
                  </div>
                ) : null}
                {item.remove_suggestions.length ? (
                  <div className="mt-3">
                    <div className="mb-1.5 text-xs font-semibold text-[#dc2626]">建议移除</div>
                    <div className="space-y-1.5">
                      {item.remove_suggestions.map((suggestion) => {
                        const checked = pick.remove.includes(suggestion.knowledge_id);
                        return (
                          <div
                            key={suggestion.knowledge_id}
                            className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 ${checked ? "border-[#0C75FC] bg-[#eff6ff] dark:bg-[#1e3a5f]" : "border-[#e5e7eb] dark:border-[#334155]"}`}
                            onClick={() => toggleRemove(item.id, suggestion.knowledge_id)}
                          >
                            <DeleteOutlined className="mt-0.5 text-[#64748b]" />
                            <div className="min-w-0 flex-1">
                              <div className="text-[13px] font-medium">{suggestion.knowledge_name}</div>
                              {suggestion.reason ? <div className="mt-0.5 text-xs leading-5 text-[#64748b]">{suggestion.reason}</div> : null}
                            </div>
                            <Checkbox
                              checked={checked}
                              disabled={adopting}
                              onClick={(event) => event.stopPropagation()}
                              onChange={() => toggleRemove(item.id, suggestion.knowledge_id)}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                {item.add_suggestions.length ? (
                  <div className="mt-3">
                    <div className="mb-1.5 text-xs font-semibold text-[#16a34a]">建议关联</div>
                    <div className="space-y-1.5">
                      {item.add_suggestions.map((suggestion) => {
                        const checked = pick.add.includes(suggestion.knowledge_id);
                        return (
                          <div
                            key={suggestion.knowledge_id}
                            className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 ${checked ? "border-[#0C75FC] bg-[#eff6ff] dark:bg-[#1e3a5f]" : "border-[#e5e7eb] dark:border-[#334155]"}`}
                            onClick={() => toggleAdd(item.id, suggestion.knowledge_id, item.add_suggestions)}
                          >
                            <PlusOutlined className="mt-0.5 text-[#64748b]" />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-[13px] font-medium">{suggestion.knowledge_name}</span>
                                {checked ? (
                                  <span
                                    className="inline-flex items-center gap-1 text-xs text-[#64748b]"
                                    onClick={(event) => event.stopPropagation()}
                                  >
                                    权重
                                    <InputNumber
                                      size="small"
                                      min={0.1}
                                      max={pick.add.length <= 1 ? 1 : Math.round((1 - (pick.add.length - 1) * 0.1) * 10) / 10}
                                      step={0.1}
                                      precision={1}
                                      disabled={adopting || pick.add.length === 1}
                                      value={pick.weights[suggestion.knowledge_id]}
                                      className="w-16"
                                      onChange={(value) => {
                                        if (value == null) return;
                                        changeWeight(item.id, suggestion.knowledge_id, value, item.add_suggestions);
                                      }}
                                    />
                                  </span>
                                ) : null}
                              </div>
                              <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs leading-5 text-[#64748b]">
                                <Rate disabled count={5} value={Math.round((suggestion.confidence || 0) * 5)} className="text-[12px]" />
                                <span>{suggestion.reason}</span>
                              </div>
                            </div>
                            <Checkbox
                              checked={checked}
                              disabled={adopting}
                              onClick={(event) => event.stopPropagation()}
                              onChange={() => toggleAdd(item.id, suggestion.knowledge_id, item.add_suggestions)}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </Drawer>
    </div>
  );
}
