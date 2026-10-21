import { ArrowDownOutlined, ArrowLeftOutlined, ArrowUpOutlined, HolderOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
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
  type KnowledgeOption,
  type MatchItem,
  type MatchTask
} from "@/api/examLibrary";

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
        autoSize={{ minRows: 1, maxRows: 6 }}
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
const VERDICT: Record<string, { color: string; label: string }> = {
  ok: { color: "green", label: "贴切" },
  needs_review: { color: "orange", label: "待复核" },
  failed: { color: "red", label: "失败" }
};

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
  const [selected, setSelected] = useState<Record<number, { add: number[]; remove: number[] }>>({});
  const started = useRef("");
  const stopWatch = useRef<(() => void) | null>(null);
  const dragFrom = useRef<number | null>(null);

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
    const next: Record<number, { add: number[]; remove: number[] }> = {};
    report.items.forEach((item) => {
      next[item.id] = {
        add: item.add_suggestions.map((suggestion) => suggestion.knowledge_id),
        remove: item.remove_suggestions.map((suggestion) => suggestion.knowledge_id)
      };
    });
    setSelected(next);
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

  const columns: ColumnsType<Draft> = [
    {
      title: "题号",
      width: 90,
      render: (_, row) => (
        <Input value={row.question_no} onChange={(event) => updateRow(row.key, { question_no: event.target.value })} />
      )
    },
    {
      title: "题型",
      width: 120,
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
      width: 100,
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
      render: (_, row) => (
        <StemField value={row.content} onChange={(content) => updateRow(row.key, { content })} />
      )
    },
    {
      title: "知识点",
      width: 240,
      render: (_, row) => (
        <Select
          mode="multiple"
          className="w-full"
          allowClear
          showSearch
          optionFilterProp="label"
          maxTagCount={2}
          value={row.knowledge_ids}
          options={knowledgeOptions.map((item: KnowledgeOption) => ({
            value: item.id,
            label: item.knowledge_name
          }))}
          onChange={(value) => updateRow(row.key, { knowledge_ids: value })}
        />
      )
    },
    {
      title: "操作",
      width: 248,
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

  const adopt = async () => {
    if (!task) return;
    const operations = reportItems
      .map((item) => ({
        item_id: item.id,
        add_ids: selected[item.id]?.add || [],
        remove_ids: selected[item.id]?.remove || []
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
      <div className="mx-auto max-w-6xl">
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
            scroll={{ x: 1280 }}
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
        title="核对报告"
        width={720}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={
          <Space>
            {task?.status === "running" ? (
              <Button onClick={() => task && void examLibraryApi.cancel(task.id)}>取消</Button>
            ) : null}
            <Button type="primary" disabled={!task || task.status === "running"} onClick={() => void adopt()}>
              采纳所选
            </Button>
          </Space>
        }
      >
        {task?.status === "running" ? (
          <Progress percent={task.total ? Math.round((task.done / task.total) * 100) : 0} />
        ) : null}
        {task?.summary ? <Typography.Paragraph>{task.summary}</Typography.Paragraph> : null}
        {task?.error ? <Alert className="mb-3" type="error" showIcon message={task.error} /> : null}
        {!task && !reportItems.length ? <Typography.Text type="secondary">还没有核对报告。</Typography.Text> : null}
        <div className="space-y-3">
          {reportItems.map((item) => {
            const verdict = VERDICT[item.verdict] || { color: "default", label: item.verdict || "未核对" };
            const pick = selected[item.id] || { add: [], remove: [] };
            return (
              <div key={item.id} className="rounded-xl border border-[#e5e7eb] p-3 dark:border-[#334155]">
                <div className="mb-2 flex items-center gap-2">
                  <span className="font-medium">第 {item.question_no || "-"} 题</span>
                  <Tag color={verdict.color}>{verdict.label}</Tag>
                  {item.adopted ? <Tag>已采纳</Tag> : null}
                </div>
                <Typography.Paragraph className="!mb-2 whitespace-pre-wrap" ellipsis={{ rows: 3 }}>
                  {item.content || "（无题干）"}
                </Typography.Paragraph>
                <div className="text-sm text-[#64748b]">
                  当前：{item.current_links.map((link) => link.knowledge_name || link.knowledge_id).join("、") || "无"}
                </div>
                {item.add_suggestions.map((suggestion) => (
                  <Checkbox
                    key={`add-${suggestion.knowledge_id}`}
                    className="mt-1 flex"
                    checked={pick.add.includes(suggestion.knowledge_id)}
                    onChange={(event) => {
                      const add = event.target.checked
                        ? [...pick.add, suggestion.knowledge_id]
                        : pick.add.filter((id) => id !== suggestion.knowledge_id);
                      setSelected((current) => ({ ...current, [item.id]: { ...pick, add } }));
                    }}
                  >
                    新增 {suggestion.knowledge_name}
                    {suggestion.reason ? `：${suggestion.reason}` : ""}
                  </Checkbox>
                ))}
                {item.remove_suggestions.map((suggestion) => (
                  <Checkbox
                    key={`remove-${suggestion.knowledge_id}`}
                    className="mt-1 flex"
                    checked={pick.remove.includes(suggestion.knowledge_id)}
                    onChange={(event) => {
                      const remove = event.target.checked
                        ? [...pick.remove, suggestion.knowledge_id]
                        : pick.remove.filter((id) => id !== suggestion.knowledge_id);
                      setSelected((current) => ({ ...current, [item.id]: { ...pick, remove } }));
                    }}
                  >
                    移除 {suggestion.knowledge_name}
                    {suggestion.reason ? `：${suggestion.reason}` : ""}
                  </Checkbox>
                ))}
              </div>
            );
          })}
        </div>
      </Drawer>
    </div>
  );
}
