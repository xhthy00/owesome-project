import { ArrowDownOutlined, ArrowLeftOutlined, ArrowUpOutlined, CloseOutlined, HolderOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  Tooltip,
  Typography,
  Upload,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  educationApi,
  type ExamBatchOption,
  type PaperQuestionDraft
} from "@/api/education";

const SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"];
const SUBJECT_FULL_SCORE: Record<string, number> = {
  语文: 150,
  数学: 150,
  英语: 150,
  物理: 100,
  化学: 100,
  生物: 100,
  政治: 100,
  历史: 100,
  地理: 100
};
const QUESTION_TYPES = ["单选题", "多选题", "填空题", "解答题", "写作"];

function blankQuestion(): PaperQuestionDraft {
  return { question_no: "", question_type: "解答题", question_score: 0, content: "" };
}

function insertQuestionAfter(rows: PaperQuestionDraft[], index: number): PaperQuestionDraft[] {
  const next = rows.slice();
  next.splice(index + 1, 0, blankQuestion());
  return next;
}

function questionNoTextWidth(value: string): number {
  let width = 0;
  for (const char of value) {
    width += char.charCodeAt(0) > 255 ? 14 : 8;
  }
  return width;
}

function reorderQuestions<T>(rows: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return rows;
  const next = rows.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

const PANEL =
  "rounded-2xl border border-[#e5e7eb] bg-white p-4 shadow-sm dark:border-[#334155] dark:bg-[#141923]";

const IMAGE_FILE = /\.(png|jpe?g|webp|bmp)$/i;

function readerForFile(file: File): "code" | "model" {
  return IMAGE_FILE.test(file.name) ? "model" : "code";
}

function containerQuestionNos(rows: PaperQuestionDraft[]): Set<string> {
  const nos = rows.map((row) => row.question_no.trim()).filter(Boolean);
  const containers = new Set<string>();
  for (const no of nos) {
    const hasChild = nos.some(
      (other) =>
        other !== no &&
        (other.startsWith(`${no}_`) || other.startsWith(`${no}-`) || other.startsWith(`${no}（`))
    );
    if (hasChild) containers.add(no);
  }
  return containers;
}

type StemBlock = { kind: "text"; text: string } | { kind: "table"; rows: string[][] };

function stemBlocks(content: string): StemBlock[] {
  const blocks: StemBlock[] = [];
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
          line
            .trim()
            .replace(/^\|/, "")
            .replace(/\|$/, "")
            .split("|")
            .map((cell) => cell.trim())
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

function plainFormula(text: string): string {
  let next = text.replace(/\$/g, "");
  let previous = "";
  while (previous !== next) {
    previous = next;
    next = next.replace(/\\sqrt\{([^{}]*)\}/g, (_match, body: string) =>
      /^[\d.]+$/.test(body) || /^[A-Za-z]$/.test(body) ? `√${body}` : `√(${body})`
    );
    next = next.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, (_match, numerator: string, denominator: string) => {
      const simple = /^[\d.√A-Za-z+-]+$/;
      return simple.test(numerator) && simple.test(denominator)
        ? `${numerator}/${denominator}`
        : `(${numerator})/(${denominator})`;
    });
  }
  return next.replace(/[{}]/g, "");
}

function StemField({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const [editing, setEditing] = useState(false);
  const plain = plainFormula(value);
  useEffect(() => {
    if (plain !== value) onChange(plain);
  }, [plain, value, onChange]);
  const blocks = stemBlocks(plain);
  const hasTable = blocks.some((block) => block.kind === "table");
  if (!hasTable || editing) {
    return (
      <Input.TextArea
        value={plain}
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
      onClick={() => setEditing(true)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setEditing(true);
        }
      }}
    >
      <div className="space-y-2">
        {blocks.map((block, index) =>
          block.kind === "text" ? (
            <div
              key={index}
              className="whitespace-pre-wrap text-sm leading-6 text-[#1f2937] dark:text-[#e5e7eb]"
            >
              {block.text}
            </div>
          ) : (
            <div key={index} className="max-w-full overflow-x-auto">
              <table className="border-collapse text-xs">
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => {
                        const Head = rowIndex === 0;
                        const Tag = Head ? "th" : "td";
                        return (
                          <Tag
                            key={cellIndex}
                            className={
                              "border border-[#cbd5e1] px-1.5 py-1 dark:border-[#475569] " +
                              (Head ? "bg-[#f8fafc] font-medium dark:bg-[#1e293b] " : "") +
                              (cellIndex === 0 ? "whitespace-nowrap text-left" : "whitespace-nowrap text-center")
                            }
                          >
                            {cell || "\u00a0"}
                          </Tag>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>
    </div>
  );
}

function scoredQuestionSum(rows: PaperQuestionDraft[]): number {
  const containers = containerQuestionNos(rows);
  const total = rows.reduce((sum, row) => {
    if (containers.has(row.question_no.trim())) return sum;
    return sum + (Number(row.question_score) || 0);
  }, 0);
  return Math.round(total * 100) / 100;
}

function confirmPaperWrite(): Promise<boolean> {
  return new Promise((resolve) => {
    Modal.confirm({
      title: "确认写入试卷",
      width: 560,
      okText: "确认写入",
      cancelText: "取消",
      okButtonProps: { danger: true },
      content: (
        <Alert
          type="warning"
          showIcon
          message="写入会改动已有试卷和成绩"
          description={
            <div className="space-y-2">
              <p>若该批次已有此科试卷，本次会替换全部题目，并删除该科的单科成绩和小题分。</p>
              <p>
                成绩宽表不会自动清理。宽表同一行包含多科成绩和总分，写入后这些分数可能和已删除的成绩对不上。
              </p>
              <p>写入后请重新导入成绩宽表，并重新导入该科小题分。</p>
            </div>
          }
        />
      ),
      onOk: () => resolve(true),
      onCancel: () => resolve(false)
    });
  });
}

export function PaperImportPage() {
  const router = useRouter();
  const [batches, setBatches] = useState<ExamBatchOption[]>([]);
  const [batchId, setBatchId] = useState<number | null>(null);
  const [subject, setSubject] = useState<string>("数学");
  const [file, setFile] = useState<File | null>(null);
  const [examScore, setExamScore] = useState<number>(SUBJECT_FULL_SCORE["数学"]);
  const [questions, setQuestions] = useState<PaperQuestionDraft[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [textSource, setTextSource] = useState<string>("");
  const [recognizing, setRecognizing] = useState(false);
  const [reader, setReader] = useState<"code" | "model">("code");
  const [progress, setProgress] = useState({ current: 0, total: 1, message: "正在读取试卷" });
  const [saving, setSaving] = useState(false);
  const readerTicket = useRef(0);
  const presetApplied = useRef(false);
  const dragFrom = useRef<number | null>(null);

  function chooseFile(next: File) {
    setFile(next);
    const ticket = ++readerTicket.current;
    if (!next.name.toLowerCase().endsWith(".pdf")) {
      setReader(readerForFile(next));
      return;
    }
    setReader("code");
    void educationApi.paperHasTextLayer(next).then(
      (hasText) => {
        if (ticket === readerTicket.current) setReader(hasText ? "code" : "model");
      },
      () => {
        if (ticket === readerTicket.current) setReader("code");
      }
    );
  }

  const loadBatches = useCallback(async () => {
    try {
      setBatches(await educationApi.listRawImportBatches());
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载批次失败");
    }
  }, []);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  useEffect(() => {
    if (!router.isReady || presetApplied.current) return;
    const rawBatch = router.query.batchId;
    const batchText = Array.isArray(rawBatch) ? rawBatch[0] : rawBatch;
    if (batchText && batches.length === 0) return;
    const batch = Number(batchText);
    if (Number.isFinite(batch) && batches.some((item) => item.id === batch)) {
      setBatchId(batch);
    }
    const rawSubject = router.query.subject;
    const subjectName = Array.isArray(rawSubject) ? rawSubject[0] : rawSubject;
    if (subjectName && SUBJECTS.includes(subjectName)) setSubject(subjectName);
    presetApplied.current = true;
  }, [router.isReady, router.query.batchId, router.query.subject, batches]);

  useEffect(() => {
    setExamScore(SUBJECT_FULL_SCORE[subject] ?? 100);
  }, [subject]);

  const containers = useMemo(() => containerQuestionNos(questions), [questions]);
  const scoreSum = useMemo(() => scoredQuestionSum(questions), [questions]);
  const missingScore = questions.some(
    (row) => !containers.has(row.question_no.trim()) && (Number(row.question_score) || 0) <= 0
  );
  const scoreMismatch = questions.length > 0 && Math.abs(scoreSum - examScore) > 0.1;
  const scoreNotes = [
    missingScore ? "有题目未识别到分值，请在表中补全" : "",
    scoreMismatch ? `小题分合计 ${scoreSum}，与卷面满分 ${examScore} 不一致` : "",
    containers.size > 0 && (missingScore || scoreMismatch) ? "含小问的大题分值不重复计入" : ""
  ].filter(Boolean);
  const scoreAlert = scoreNotes.length ? `${scoreNotes.join("。")}。` : "";

  function patchQuestion(index: number, patch: Partial<PaperQuestionDraft>) {
    setQuestions((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function recognize() {
    if (!file) {
      message.warning("请先选择试卷文件");
      return;
    }
    setProgress({ current: 0, total: 1, message: "正在读取试卷" });
    setRecognizing(true);
    try {
      const outline = await educationApi.previewPaperImport(subject, file, setProgress, reader);
      setQuestions(outline.questions || []);
      setWarnings(outline.warnings || []);
      setExamScore(outline.exam_score);
      setTextSource(outline.text_source);
      if (!outline.questions?.length) {
        message.warning("没有识别到题目");
      } else {
        message.success(`识别到 ${outline.questions.length} 道题`);
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "识别试卷失败");
    } finally {
      setRecognizing(false);
    }
  }

  async function save() {
    if (batchId == null) {
      message.warning("请选择考试批次");
      return;
    }
    if (!questions.length) {
      message.warning("请先识别题目");
      return;
    }
    const confirmed = await confirmPaperWrite();
    if (!confirmed) return;
    setSaving(true);
    try {
      const saved = await educationApi.executePaperImport({
        exam_batch_id: batchId,
        subject,
        exam_score: examScore,
        questions
      });
      const clearedScore = saved.cleared_score_count || 0;
      const clearedDetail = saved.cleared_detail_count || 0;
      if (saved.updated) {
        message.success(
          `已覆盖 ${saved.exam_name}，共 ${saved.question_count} 题，已删除该科成绩 ${clearedScore} 条、小题分 ${clearedDetail} 条。请重新导入成绩宽表和该科小题分。`
        );
      } else {
        message.success(`已写入 ${saved.exam_name}，共 ${saved.question_count} 题`);
      }
      void router.push(`/construct/education/exam-library/${saved.exam_id}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "写入试卷失败");
    } finally {
      setSaving(false);
    }
  }

  const questionNoWidth = Math.min(
    360,
    Math.max(
      128,
      questions.reduce((max, row) => Math.max(max, questionNoTextWidth(row.question_no)), 0) + 36
    )
  );

  const columns: ColumnsType<PaperQuestionDraft> = [
    {
      title: "题号",
      dataIndex: "question_no",
      width: questionNoWidth,
      render: (_value, _row, index) => (
        <Input
          className="w-full !rounded-lg !border-[#e4eef8] !bg-[#eef5ff] !text-center !font-medium !text-[#0C75FC]"
          value={questions[index]?.question_no}
          onChange={(event) => patchQuestion(index, { question_no: event.target.value })}
        />
      )
    },
    {
      title: "题型",
      dataIndex: "question_type",
      width: 112,
      render: (_value, _row, index) => (
        <Select
          className="w-full [&_.ant-select-selector]:!rounded-lg [&_.ant-select-selector]:!border-[#e4eef8]"
          value={questions[index]?.question_type || undefined}
          options={QUESTION_TYPES.map((item) => ({ value: item, label: item }))}
          onChange={(value) => patchQuestion(index, { question_type: value })}
        />
      )
    },
    {
      title: "分值",
      dataIndex: "question_score",
      width: 80,
      render: (_value, _row, index) => (
        <InputNumber
          className="w-full !rounded-lg !border-[#e4eef8]"
          min={0}
          step={0.5}
          value={questions[index]?.question_score}
          onChange={(value) => patchQuestion(index, { question_score: Number(value) || 0 })}
        />
      )
    },
    {
      title: "题干",
      dataIndex: "content",
      onCell: () => ({ style: { whiteSpace: "normal", verticalAlign: "middle", minWidth: 0 } }),
      render: (_value, _row, index) => (
        <StemField
          value={questions[index]?.content || ""}
          onChange={(content) => patchQuestion(index, { content })}
        />
      )
    },
    {
      title: "操作",
      width: 168,
      render: (_value, _row, index) => (
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
            onClick={() => setQuestions((rows) => reorderQuestions(rows, index, index - 1))}
          />
          <Button
            type="text"
            size="small"
            aria-label="下移"
            disabled={index >= questions.length - 1}
            icon={<ArrowDownOutlined />}
            onClick={() => setQuestions((rows) => reorderQuestions(rows, index, index + 1))}
          />
          <Tooltip title="添加下一题">
            <Button
              type="text"
              size="small"
              aria-label="添加下一题"
              icon={<PlusOutlined />}
              onClick={() => setQuestions((rows) => insertQuestionAfter(rows, index))}
            />
          </Tooltip>
          <Button type="link" size="small" danger onClick={() => setQuestions((rows) => rows.filter((_, i) => i !== index))}>
            删除
          </Button>
        </Space>
      )
    }
  ];

  return (
    <div className="dbgpt-ui-font flex h-full min-h-0 flex-col overflow-hidden bg-[#f5f7fb] p-6 dark:bg-transparent">
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="mb-4 flex shrink-0 items-center gap-2">
        <Link href="/construct/education/exam-library" className="text-[#0C75FC]">
          <ArrowLeftOutlined />
        </Link>
        <Typography.Title level={4} className="!mb-0">
          导入试卷
        </Typography.Title>
      </div>

      <div className={`${PANEL} mb-4 shrink-0`}>
        <Typography.Paragraph type="secondary" className="!mb-3">
          上传 Word、PDF 或图片。题号按卷面生成：选择题写成单选/多选加印刷题号，解答题按小问拆开。物理实验、化学和生物的细目请在下表里改到与阅卷表一致。同一批次、同一科目再次导入会覆盖题目，并删除该科单科成绩和小题分。成绩宽表不会自动清理。
        </Typography.Paragraph>
        <div className="flex flex-wrap items-center gap-3">
          <Select
            className="min-w-[280px]"
            showSearch
            optionFilterProp="label"
            placeholder="选择考试批次"
            value={batchId ?? undefined}
            options={batches.map((item) => ({ value: item.id, label: item.batch_name }))}
            onChange={(value) => setBatchId(value)}
          />
          <Select
            className="min-w-[120px]"
            value={subject}
            options={SUBJECTS.map((item) => ({ value: item, label: item }))}
            onChange={(value) => setSubject(value)}
          />
          <Upload
            accept=".pdf,.docx,.png,.jpg,.jpeg,.webp,.bmp"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(next) => {
              chooseFile(next);
              return false;
            }}
          >
            <Button>选择试卷</Button>
          </Upload>
          {file ? (
            <span className="inline-flex max-w-[220px] items-center text-sm text-[#64748b]">
              <span className="truncate">{file.name}</span>
              <button
                type="button"
                aria-label="移除试卷"
                className="inline-flex shrink-0 items-center px-1 text-xs text-[#94a3b8] hover:text-[#475569]"
                onClick={() => setFile(null)}
              >
                <CloseOutlined />
              </button>
            </span>
          ) : null}
          <div className="ml-auto inline-flex items-center gap-2">
            <Button type="primary" loading={recognizing} onClick={() => void recognize()}>
              识别题目
            </Button>
            <span className="inline-flex items-center gap-1.5">
              <Switch
                size="small"
                checked={reader === "model"}
                disabled={recognizing}
                aria-label="AI识别"
                onChange={(checked) => {
                  readerTicket.current += 1;
                  setReader(checked ? "model" : "code");
                }}
              />
              <span className="text-sm text-[#64748b]">AI识别</span>
            </span>
          </div>
        </div>
        {recognizing ? (
          <div className="mt-4">
            <Progress
              percent={progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0}
              status="active"
            />
            <div className="mt-1 text-sm text-[#64748b]">{progress.message}</div>
          </div>
        ) : null}
      </div>
      {warnings.length > 0 ? (
        <Alert className="mb-4 shrink-0" type="warning" showIcon message={warnings.join("；")} />
      ) : null}
      {scoreAlert ? <Alert className="mb-4 shrink-0" type="warning" showIcon message={scoreAlert} /> : null}
      {textSource === "ocr" ? (
        <Alert className="mb-4 shrink-0" type="info" showIcon message="题干来自 AI 识别，请核对题号。" />
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[#eef2f6] bg-[linear-gradient(180deg,#f3f8ff_0%,#ffffff_78%)] px-5 py-3 dark:border-[#334155] dark:bg-none">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/80 px-2.5 py-1 text-xs ring-1 ring-[#e5e7eb] dark:bg-[#141923] dark:ring-[#334155]">
              <span className="text-[#64748b]">题目</span>
              <span className="font-medium tabular-nums text-[#1e293b] dark:text-[#e2e8f0]">{questions.length}</span>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/80 px-2.5 py-1 text-xs ring-1 ring-[#e5e7eb] dark:bg-[#141923] dark:ring-[#334155]">
              <span className="text-[#64748b]">卷面满分</span>
              <InputNumber
                size="small"
                min={1}
                className="!w-16"
                value={examScore}
                onChange={(value) => setExamScore(Number(value) || 0)}
              />
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/80 px-2.5 py-1 text-xs ring-1 ring-[#e5e7eb] dark:bg-[#141923] dark:ring-[#334155]">
              <span className="text-[#64748b]">小题分</span>
              <span className={`font-medium tabular-nums ${scoreMismatch ? "text-[#c2410c]" : "text-[#1e293b] dark:text-[#e2e8f0]"}`}>
                {examScore ? `${scoreSum}/${examScore}` : scoreSum}
              </span>
              {containers.size > 0 ? <span className="text-[#94a3b8]">含小问的大题不重复计入</span> : null}
            </span>
          </div>
          <Space>
            <Button onClick={() => setQuestions((rows) => [...rows, blankQuestion()])}>添加题目</Button>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              提交试卷
            </Button>
          </Space>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-5 pb-4">
        <Table
          size="middle"
          rowKey={(row) => String(questions.indexOf(row))}
          columns={columns}
          dataSource={questions}
          pagination={false}
          tableLayout="fixed"
          className="[&_.ant-table]:!w-full [&_.ant-table-container]:!overflow-visible [&_.ant-table-content]:!overflow-visible [&_.ant-table-tbody>tr:hover>td]:!bg-[#f9fbfe] [&_.ant-table-tbody>tr>td]:!border-[#eef3f8] [&_.ant-table-tbody>tr>td]:!py-3.5 [&_.ant-table-thead>tr>th]:!sticky [&_.ant-table-thead>tr>th]:!top-0 [&_.ant-table-thead>tr>th]:!z-10 [&_.ant-table-thead>tr>th]:!border-b-[#d6e6f8] [&_.ant-table-thead>tr>th]:!bg-[#f7f9fc] [&_.ant-table-thead>tr>th]:!font-medium [&_.ant-table-thead>tr>th]:!py-3 [&_.ant-table-thead>tr>th]:!text-[#475569] [&_td]:!align-middle [&_td]:whitespace-normal dark:[&_.ant-table-thead>tr>th]:!bg-[#1c2430]"
          locale={{ emptyText: "识别后在这里改题号、分值、题干和顺序" }}
          onRow={(_row, index) => ({
            onDragOver: (event) => event.preventDefault(),
            onDrop: () => {
              if (index == null || dragFrom.current == null) return;
              const from = dragFrom.current;
              dragFrom.current = null;
              setQuestions((rows) => reorderQuestions(rows, from, index));
            }
          })}
        />
        </div>
      </div>
    </div>
    </div>
  );
}

export default function PaperImportRedirect() {
  const router = useRouter();
  useEffect(() => {
    if (!router.isReady) return;
    const params = new URLSearchParams();
    const batchId = router.query.batchId;
    const subject = router.query.subject;
    if (typeof batchId === "string") params.set("batchId", batchId);
    if (typeof subject === "string") params.set("subject", subject);
    const query = params.toString();
    void router.replace(`/construct/education/exam-library/import${query ? `?${query}` : ""}`);
  }, [router]);
  return null;
}
