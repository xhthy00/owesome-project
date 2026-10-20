import { FileTextOutlined, UploadOutlined } from "@ant-design/icons";
import { Button, Input, Modal, Select, Spin, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { educationApi, type ExamBatchOption } from "@/api/education";
import {
  scoreLibraryApi,
  type ScoreDetailItem,
  type ScoreFilterOptions,
  type ScoreLibraryItem
} from "@/api/scoreLibrary";

const PANEL =
  "rounded-2xl border border-[#e5e7eb] bg-white p-4 shadow-sm dark:border-[#334155] dark:bg-[#141923]";

const EMPTY_OPTIONS: ScoreFilterOptions = { schools: [], grades: [], classes: [], tracks: [] };

function FilterField({
  label,
  width = "w-[148px]",
  children
}: {
  label: string;
  width?: string;
  children: ReactNode;
}) {
  return (
    <label className={`flex flex-col gap-1 text-xs text-slate-500 ${width}`}>
      <span className="leading-4">{label}</span>
      {children}
    </label>
  );
}

const SUBJECTS: Array<{ title: string; field: keyof ScoreLibraryItem; subject: string }> = [
  { title: "语文", field: "yw", subject: "语文" },
  { title: "数学", field: "sx", subject: "数学" },
  { title: "英语", field: "yy", subject: "英语" },
  { title: "物理", field: "wl", subject: "物理" },
  { title: "化学", field: "hxzh", subject: "化学" },
  { title: "生物", field: "swzh", subject: "生物" },
  { title: "历史", field: "ls", subject: "历史" },
  { title: "政治", field: "zzzh", subject: "政治" },
  { title: "地理", field: "dlzh", subject: "地理" }
];

function showText(value: string) {
  return value || "-";
}

function showScore(value: number | null) {
  return value == null ? "-" : value;
}

function formatScore(value: number | null) {
  if (value == null || Number.isNaN(value)) return "-";
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 10) / 10);
}

function trackTag(value: string) {
  if (!value) return <span className="text-slate-300">-</span>;
  const history = value.includes("史") || value.includes("历");
  const tone = history
    ? "bg-[#fff6e8] text-[#b86e00]"
    : "bg-[#e8f1ff] text-[#1d4ed8]";
  return <span className={`inline-flex rounded-md px-1.5 py-0.5 text-xs font-medium ${tone}`}>{value}</span>;
}

function scoreTone(score: number | null, full: number | null) {
  if (score == null || full == null) return "text-slate-500";
  if (score <= 0) return "text-rose-600";
  if (score >= full) return "text-emerald-600";
  return "text-amber-600";
}

function pipeCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isPipeRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && pipeCells(trimmed).length >= 2;
}

function isPipeSeparator(line: string): boolean {
  return isPipeRow(line) && pipeCells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
}

type QuestionBlock =
  | { kind: "text"; text: string }
  | { kind: "table"; headers: string[]; rows: string[][] };

function questionBlocks(content: string): QuestionBlock[] {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: QuestionBlock[] = [];
  const text: string[] = [];
  const flushText = () => {
    if (!text.length) return;
    blocks.push({ kind: "text", text: text.join("\n") });
    text.length = 0;
  };
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const next = lines[index + 1];
    if (isPipeRow(line) && !isPipeSeparator(line) && next != null && isPipeSeparator(next)) {
      flushText();
      const headers = pipeCells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && isPipeRow(lines[index]) && !isPipeSeparator(lines[index])) {
        rows.push(pipeCells(lines[index]));
        index += 1;
      }
      blocks.push({ kind: "table", headers, rows });
      continue;
    }
    text.push(line);
    index += 1;
  }
  flushText();
  return blocks;
}

function QuestionContent({ text }: { text: string }) {
  return (
    <div className="min-w-0 flex-1 text-sm leading-6 text-slate-700 dark:text-slate-200">
      {questionBlocks(text).map((block, index) =>
        block.kind === "text" ? (
          <p key={index} className="whitespace-pre-wrap">
            {block.text}
          </p>
        ) : (
          <div key={index} className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="bg-[#f4f7fb] dark:bg-[#1c2433]">
                  {block.headers.map((header, cell) => (
                    <th
                      key={cell}
                      className="border border-[#e5eaf1] px-2.5 py-1.5 font-medium text-slate-600 dark:border-[#2a3344] dark:text-slate-300"
                    >
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {block.headers.map((_, cell) => (
                      <td
                        key={cell}
                        className="border border-[#e5eaf1] px-2.5 py-1.5 align-top dark:border-[#2a3344]"
                      >
                        {row[cell] ?? ""}
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

export default function ScoreLibraryPage() {
  const router = useRouter();
  const [studentInput, setStudentInput] = useState("");
  const [student, setStudent] = useState("");
  const [examBatchId, setExamBatchId] = useState<number | undefined>();
  const [school, setSchool] = useState("");
  const [grade, setGrade] = useState("");
  const [className, setClassName] = useState("");
  const [track, setTrack] = useState("");
  const [subject, setSubject] = useState("");
  const [options, setOptions] = useState<ScoreFilterOptions>(EMPTY_OPTIONS);
  const [batches, setBatches] = useState<ExamBatchOption[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<ScoreLibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailWho, setDetailWho] = useState("");
  const [detailSubject, setDetailSubject] = useState("");
  const [detailPaper, setDetailPaper] = useState("");
  const [detailItems, setDetailItems] = useState<ScoreDetailItem[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await scoreLibraryApi.list({
        examBatchId,
        school,
        grade,
        className,
        track,
        subject,
        student,
        page,
        pageSize
      });
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载成绩库失败");
    } finally {
      setLoading(false);
    }
  }, [examBatchId, school, grade, className, track, subject, student, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void educationApi
      .listRawImportBatches()
      .then(setBatches)
      .catch(() => setBatches([]));
  }, []);

  useEffect(() => {
    let active = true;
    void scoreLibraryApi
      .options(examBatchId)
      .then((data) => {
        if (active) setOptions(data);
      })
      .catch(() => {
        if (active) setOptions(EMPTY_OPTIONS);
      });
    return () => {
      active = false;
    };
  }, [examBatchId]);

  const resetFilters = () => {
    setPage(1);
    setExamBatchId(undefined);
    setSchool("");
    setGrade("");
    setClassName("");
    setTrack("");
    setSubject("");
    setStudentInput("");
    setStudent("");
  };

  const classOptions = useMemo(
    () =>
      options.classes.filter(
        (item) => (!school || item.xx === school) && (!grade || item.grade === grade)
      ),
    [options.classes, school, grade]
  );

  const openDetail = async (row: ScoreLibraryItem, subject: string) => {
    if (!row.exam_batch_id || !row.anon_stu_id) {
      message.warning("这条成绩缺少考试或学生标识，无法查看小题分");
      return;
    }
    const who = row.xm || row.sfzh || "学生";
    setDetailWho(who);
    setDetailSubject(subject);
    setDetailPaper("");
    setDetailItems([]);
    setDetailOpen(true);
    setDetailLoading(true);
    try {
      const data = await scoreLibraryApi.detail({
        examBatchId: row.exam_batch_id,
        anonStuId: row.anon_stu_id,
        subject
      });
      setDetailPaper(data.paper_name || data.exam_name || "");
      setDetailItems(data.items);
      if (!data.exam_id) message.info("该批次没有这科试卷");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "加载小题分失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const detailGained = detailItems.reduce((sum, item) => sum + (item.score ?? 0), 0);
  const detailFull = detailItems.reduce((sum, item) => sum + (item.question_score ?? 0), 0);
  const detailMissed = detailItems.filter(
    (item) => item.score != null && item.question_score != null && item.score < item.question_score
  ).length;
  const detailRate = detailFull > 0 ? `${Math.round((detailGained / detailFull) * 1000) / 10}%` : "-";

  const columns: ColumnsType<ScoreLibraryItem> = [
    {
      title: "学号",
      dataIndex: "sfzh",
      width: 168,
      fixed: "left",
      ellipsis: true,
      render: (value) => showText(value)
    },
    { title: "姓名", dataIndex: "xm", width: 88, fixed: "left", render: (value) => <span className="font-medium">{showText(value)}</span> },
    { title: "班级", dataIndex: "bj", width: 112, render: (value) => showText(value) },
    { title: "学校", dataIndex: "xx", width: 140, ellipsis: true, render: (value) => showText(value) },
    { title: "选科", dataIndex: "xkkm", width: 96, render: (value: string) => trackTag(value) },
    { title: "考试", dataIndex: "exam_name", width: 168, ellipsis: true, render: (value) => showText(value) },
    {
      title: "总分",
      dataIndex: "zf6m",
      width: 80,
      align: "center",
      render: (value: number | null) =>
        value == null ? (
          <span className="text-slate-300">-</span>
        ) : (
          <span className="inline-flex min-w-[44px] justify-center rounded-md bg-[#e7f8ee] px-1.5 py-0.5 text-[13px] font-semibold text-[#15803d]">
            {value}
          </span>
        )
    },
    ...SUBJECTS.map((col) => ({
      title: col.title,
      dataIndex: col.field,
      width: 72,
      align: "center" as const,
      onHeaderCell: () => ({
        style: { background: subject === col.subject ? "#dbe7ff" : "#f3f6fb" }
      }),
      onCell: () => ({
        style: { background: subject === col.subject ? "#f4f8ff" : undefined }
      }),
      render: (value: number | null, row: ScoreLibraryItem) =>
        value == null ? (
          <span className="text-slate-300">-</span>
        ) : (
          <button
            type="button"
            className="inline-flex min-w-[36px] justify-center rounded-md bg-[#eef3fb] px-1.5 py-0.5 text-[13px] font-medium text-[#1d4f91] hover:bg-[#dce8f8]"
            onClick={() => void openDetail(row, col.subject)}
          >
            {value}
          </button>
        )
    }))
  ];

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="mx-auto w-full">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-[#e7eefc] text-[#2457c5]">
              <FileTextOutlined className="text-lg" />
            </span>
            <div>
              <Typography.Title level={4} className="!mb-0">
                成绩库
              </Typography.Title>
              <div className="text-xs text-slate-500">共 {total} 名学生</div>
            </div>
          </div>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            onClick={() => router.push("/construct/education/score-library/import")}
          >
            导入成绩
          </Button>
        </div>
        <div className={PANEL}>
          <div className="score-library-filters mb-4 flex flex-wrap items-end gap-x-3 gap-y-2 rounded-xl border border-[#e6eef8] bg-[#f7faff] px-3 py-2.5 dark:border-[#2a3344] dark:bg-[#1a2230]">
            <FilterField label="考试" width="w-[220px]">
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                className="w-full"
                placeholder="全部考试"
                value={examBatchId}
                options={batches.map((item) => ({ value: item.id, label: item.batch_name }))}
                onChange={(value) => {
                  setPage(1);
                  setExamBatchId(value ?? undefined);
                  setSchool("");
                  setGrade("");
                  setClassName("");
                  setTrack("");
                }}
              />
            </FilterField>
            <FilterField label="学校" width="w-[200px]">
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                className="w-full"
                placeholder="全部学校"
                value={school || undefined}
                options={options.schools.map((item) => ({ value: item, label: item }))}
                onChange={(value) => {
                  setPage(1);
                  setSchool(value || "");
                  setClassName("");
                }}
              />
            </FilterField>
            <FilterField label="年级">
              <Select
                allowClear
                className="w-full"
                placeholder="全部年级"
                value={grade || undefined}
                options={options.grades.map((item) => ({ value: item, label: item }))}
                onChange={(value) => {
                  setPage(1);
                  setGrade(value || "");
                  setClassName("");
                }}
              />
            </FilterField>
            <FilterField label="班级">
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                className="w-full"
                placeholder="全部班级"
                value={className ? `${school}||${className}` : undefined}
                options={classOptions.map((item) => ({
                  value: `${item.xx}||${item.bj}`,
                  label: school ? item.bj : `${item.xx} ${item.bj}`
                }))}
                onChange={(value) => {
                  setPage(1);
                  if (!value) {
                    setClassName("");
                    return;
                  }
                  const [xx, bj] = value.split("||");
                  setSchool(xx);
                  setClassName(bj);
                }}
              />
            </FilterField>
            <FilterField label="选科">
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                className="w-full"
                placeholder="全部组合"
                value={track || undefined}
                options={options.tracks.map((item) => ({ value: item, label: item }))}
                onChange={(value) => {
                  setPage(1);
                  setTrack(value || "");
                }}
              />
            </FilterField>
            <FilterField label="学科">
              <Select
                allowClear
                className="w-full"
                placeholder="全部科目"
                value={subject || undefined}
                options={SUBJECTS.map((item) => ({ value: item.subject, label: item.title }))}
                onChange={(value) => {
                  setPage(1);
                  setSubject(value || "");
                }}
              />
            </FilterField>
            <FilterField label="学生" width="w-[188px]">
              <Input.Search
                allowClear
                className="w-full"
                placeholder="姓名或学号"
                value={studentInput}
                onChange={(event) => {
                  const value = event.target.value;
                  setStudentInput(value);
                  if (!value) {
                    setPage(1);
                    setStudent("");
                  }
                }}
                onSearch={(value) => {
                  setPage(1);
                  const next = value.trim();
                  setStudentInput(next);
                  setStudent(next);
                }}
              />
            </FilterField>
            <Button onClick={resetFilters}>重置</Button>
          </div>
          <Table
            className="score-library-table"
            rowKey={(row, index) =>
              `${row.exam_batch_id ?? "b"}-${row.anon_stu_id || row.sfzh || "s"}-${index}`
            }
            size="middle"
            loading={loading}
            columns={columns}
            dataSource={rows}
            scroll={{ x: 1560 }}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: [20, 50, 100],
              showTotal: (count) => `共 ${count} 名学生`,
              onChange: (next, size) => {
                setPage(next);
                setPageSize(size);
              }
            }}
          />
        </div>
        <style jsx>{`
          :global(.score-library-table .ant-table-thead > tr > th) {
            background: #f4f7fb;
            color: #334155;
            font-weight: 600;
          }
          :global(.score-library-table .ant-table-tbody > tr > td) {
            border-bottom-color: #eef2f6;
          }
          :global(.score-library-table .ant-table-tbody > tr:hover > td) {
            background: #f8fbff !important;
          }
          :global(.score-library-filters .ant-select),
          :global(.score-library-filters .ant-input-search) {
            width: 100%;
          }
        `}</style>
      </div>
      <Modal
        open={detailOpen}
        footer={null}
        width={920}
        onCancel={() => setDetailOpen(false)}
        destroyOnClose
        styles={{ body: { padding: "0 0 8px" } }}
        title={
          <div>
            <div className="text-lg font-semibold text-slate-900 dark:text-slate-100">{detailWho}</div>
            <div className="mt-1 text-sm font-normal text-slate-500">
              {detailSubject}小题分{detailPaper ? ` · ${detailPaper}` : ""}
            </div>
          </div>
        }
      >
        <div className="border-b border-[#e8edf3] px-6 pb-4 dark:border-[#2a3344]">
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: "得分", value: formatScore(detailGained) },
              { label: "满分", value: formatScore(detailFull) },
              { label: "得分率", value: detailRate },
              { label: "失分题", value: String(detailMissed) }
            ].map((stat) => (
              <div key={stat.label} className="rounded-xl bg-[#f4f7fb] px-3 py-2.5 dark:bg-[#1c2433]">
                <div className="text-xs text-slate-500">{stat.label}</div>
                <div className="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                  {detailLoading ? "-" : stat.value}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="max-h-[62vh] overflow-y-auto px-3 py-2">
          {detailLoading ? (
            <div className="flex justify-center py-16">
              <Spin />
            </div>
          ) : detailItems.length === 0 ? (
            <div className="py-16 text-center text-sm text-slate-400">暂无小题得分</div>
          ) : (
            detailItems.map((item, index) => (
              <div
                key={`${item.question_no}-${index}`}
                className="flex items-start gap-4 border-b border-[#f1f4f8] px-2 py-3.5 last:border-b-0 dark:border-[#243044]"
              >
                <div className="flex min-w-[4.75rem] shrink-0 items-center justify-start self-stretch border-r border-[#e6eef8] pr-4 dark:border-[#2a3344]">
                  <span className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-lg border border-[#d5e4f7] bg-[#eef4fc] px-2.5 py-1 text-sm font-semibold leading-none text-[#3b6cb5] dark:border-[#2c3d56] dark:bg-[#1c2838] dark:text-[#9eb6d6]">
                    {item.question_no || "-"}
                  </span>
                </div>
                <QuestionContent text={item.content || "暂无题目"} />
                <div
                  className={`shrink-0 pt-0.5 text-right text-sm font-semibold tabular-nums ${scoreTone(item.score, item.question_score)}`}
                >
                  {formatScore(item.score)}
                  <span className="ml-1 font-normal text-slate-400">/ {formatScore(item.question_score)}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </Modal>
    </div>
  );
}
