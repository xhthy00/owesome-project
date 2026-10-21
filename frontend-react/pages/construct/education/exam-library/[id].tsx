import { ArrowDownOutlined, ArrowLeftOutlined, ArrowUpOutlined, DeleteOutlined, HolderOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Input,
  InputNumber,
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
  canImportExamPapers,
  examLibraryApi,
  type ExamDetail,
  type MatchItem,
  type MatchSuggestion,
  type MatchTask
} from "@/api/examLibrary";
import { buildKnowledgeTree } from "@/utils/knowledgeTree";
import { adjustWeight, suggestWeights } from "@/utils/knowledgeWeights";

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

function StemField({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const [editing, setEditing] = useState(false);
  const blocks = stemBlocks(value);
  const hasTable = blocks.some((block) => block.kind === "table");
  if (!hasTable || editing) {
    return (
      <Input.TextArea
        value={value}
        autoFocus={editing}
        autoSize={{ minRows: 2 }}
        className="!w-full"
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
      className="w-full cursor-text rounded-md border border-[#d9d9d9] bg-white px-2 py-1.5 text-left"
      onClick={() => setEditing(true)}
      onKeyDown={(event) => {
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
  knowledge_ids: number[];
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
    knowledge_ids: []
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
    knowledge_ids: item.knowledge_links.map((link) => link.knowledge_id)
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
  const examScore = detail?.exam.exam_score ?? 0;
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
          knowledge_links: row.knowledge_ids.map((knowledgeId) => ({ knowledge_id: knowledgeId }))
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
          knowledge_ids: item.knowledge_links.map((link) => link.knowledge_id)
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
          className="w-full"
          value={row.question_no}
          onChange={(event) => updateRow(row.key, { question_no: event.target.value })}
        />
      )
    },
    {
      title: "题型",
      width: 100,
      render: (_, row) => (
        <Select
          className="w-full"
          allowClear
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
          className="w-full"
          min={0}
          step={0.5}
          value={row.question_score ?? undefined}
          onChange={(value) => updateRow(row.key, { question_score: value })}
        />
      )
    },
    {
      title: "题干",
      onCell: () => ({ style: { whiteSpace: "normal", verticalAlign: "top", minWidth: 0 } }),
      render: (_, row) => (
        <StemField value={row.content} onChange={(content) => updateRow(row.key, { content })} />
      )
    },
    {
      title: "知识点",
      width: 240,
      render: (_, row) => (
        <TreeSelect
          className="w-full"
          multiple
          allowClear
          showSearch
          treeExpandAction="click"
          filterTreeNode={(input, node) => {
            const keyword = input.trim().toLowerCase();
            if (!keyword) return true;
            const searchable = String(
              (node as { searchText?: string }).searchText || node.title || ""
            );
            return searchable.toLowerCase().includes(keyword);
          }}
          maxTagCount="responsive"
          placeholder="选择知识点"
          popupMatchSelectWidth={false}
          dropdownStyle={{ minWidth: 420, maxWidth: 560 }}
          listHeight={400}
          treeData={knowledgeTree}
          value={row.knowledge_ids}
          onChange={(value) => {
            const picked = (Array.isArray(value) ? value : [value]).filter(
              (id): id is number => typeof id === "number"
            );
            updateRow(row.key, { knowledge_ids: picked });
          }}
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
    setSelected((current) => {
      const pick = current[itemId] || { add: [], remove: [], weights: {} };
      return {
        ...current,
        [itemId]: { ...pick, remove: toggleId(pick.remove, knowledgeId) }
      };
    });
  };

  const changeWeight = (itemId: number, knowledgeId: number, value: number, suggestions: MatchSuggestion[]) => {
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
    setSelected({});
    message.info("已取消全部勾选");
  };

  const adopt = async () => {
    if (!task) return;
    const operations = reportItems
      .map((item) => ({
        item_id: item.id,
        add_ids: selected[item.id]?.add || [],
        remove_ids: selected[item.id]?.remove || [],
        weights: Object.fromEntries(
          (selected[item.id]?.add || []).map((id) => [String(id), selected[item.id]?.weights[id] ?? 0])
        )
      }))
      .filter((item) => item.add_ids.length || item.remove_ids.length);
    if (!operations.length) {
      message.info("没有可采纳的建议");
      return;
    }
    try {
      const result = await examLibraryApi.apply(task.id, operations);
      if (result.errors.length) message.warning(result.errors.join("；"));
      message.success(`已采纳 ${result.success} 题`);
      await load();
      await openReport(task.id);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "采纳建议失败");
    }
  };

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="w-full min-w-0">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Link href="/construct/education/exam-library" className="text-[#0C75FC]">
              <ArrowLeftOutlined />
            </Link>
            <div>
              <Typography.Title level={4} className="!mb-0">
                {detail ? `${detail.exam.exam_name} · ${detail.exam.subject}` : "题目明细"}
              </Typography.Title>
              {detail?.exam.exam_batch_name ? (
                <Typography.Text type="secondary">考试批次：{detail.exam.exam_batch_name}</Typography.Text>
              ) : null}
            </div>
          </div>
          <Space wrap>
            <Button onClick={() => setRows((current) => [...current, blankDraft()])}>添加题目</Button>
            {canImportExamPapers(currentUser) && detail?.exam.exam_batch_id ? (
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
            <Button onClick={() => void startMatch()}>知识点核对</Button>
            <Button onClick={() => void openReport().catch((error) => message.error(error instanceof Error ? error.message : "读取核对报告失败"))}>
              核对报告
            </Button>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              保存试卷
            </Button>
          </Space>
        </div>
        {detail && (detail.exam.score_count || detail.exam.detail_count) ? (
          <Alert
            className="mb-4"
            type="warning"
            showIcon
            message={`该试卷已有 ${detail.exam.score_count || 0} 条成绩、${detail.exam.detail_count || 0} 条小题分。修改题目不会自动改成绩，已有成绩的试卷不能删除。`}
          />
        ) : null}
        {examScore && scoreSum !== examScore ? (
          <Alert className="mb-4" type="warning" showIcon message={`小题分合计 ${scoreSum}，与卷面满分 ${examScore} 不一致。`} />
        ) : (
          <Typography.Paragraph type="secondary">小题分合计 {scoreSum}</Typography.Paragraph>
        )}
        <div className="rounded-2xl border border-[#e5e7eb] bg-white p-4 shadow-sm dark:border-[#334155] dark:bg-[#141923]">
          <Table
            rowKey="key"
            size="middle"
            loading={loading}
            columns={columns}
            dataSource={rows}
            pagination={false}
            tableLayout="fixed"
            className="[&_.ant-table]:!w-full [&_td]:align-top [&_td]:whitespace-normal"
            onRow={(_row, index) => ({
              onDragOver: (event) => event.preventDefault(),
              onDrop: () => {
                if (index == null || dragFrom.current == null) return;
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
        onClose={() => setDrawerOpen(false)}
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
            <Button onClick={() => setDrawerOpen(false)}>关闭</Button>
            <Button type="primary" disabled={!selectedCount} onClick={() => void adopt()}>
              采纳选中（{selectedCount}）
            </Button>
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
              <Button disabled={!selectedCount} onClick={clearAll}>全部勾除</Button>
              <Button type="primary" disabled={!totalSuggestions} onClick={selectAll}>全部勾选</Button>
            </Space>
          </div>
        ) : null}
        {!task && !reportItems.length ? <Typography.Text type="secondary">暂无核对记录</Typography.Text> : null}
        <div className="space-y-3">
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
                    {item.current_links.map((link) => (
                      <Tag key={link.knowledge_id}>
                        {link.knowledge_name || `ID:${link.knowledge_id}`}
                        {link.weight != null ? `(${formatWeight(link.weight)})` : ""}
                      </Tag>
                    ))}
                  </div>
                ) : (
                  <div className="mt-1 text-[13px] text-[#64748b]">无</div>
                )}
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
                                      disabled={pick.add.length === 1}
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
