import {
  CheckCircleOutlined,
  DownOutlined,
  DownloadOutlined,
  ReloadOutlined,
  UploadOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Collapse,
  DatePicker,
  Dropdown,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Typography,
  Upload,
  message,
  Tag
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  educationApi,
  type ExamBatchOption,
  type RawImportResult,
  type RawImportWarning,
  type RawOverviewStatus,
  type RawPaperOption,
  type ScoreImportErrorRow
} from "@/api/education";

type WizardStep = 0 | 1 | 2;
type DetailStatus =
  | "pending"
  | "validating"
  | "passed"
  | "errors"
  | "importing"
  | "imported"
  | "failed";

type DetailItem = {
  uid: string;
  file: File;
  subjectGuess: string;
  examId: number | null;
  status: DetailStatus;
  preview: RawImportResult | null;
  message: string;
};

const PANEL_CARD =
  "overflow-hidden rounded-2xl border border-[#e2e8f0] bg-white p-5 shadow-[0_2px_14px_rgba(15,23,42,0.06)] dark:border-[#334155] dark:bg-[#141923]";

const KPI_SURFACE =
  "h-full rounded-2xl border border-[#e2e8f0] bg-gradient-to-br from-white via-[#fafcff] to-[#f1f6ff] p-4 shadow-[0_2px_14px_rgba(15,23,42,0.06)] dark:border-[#334155] dark:from-[#141923] dark:via-[#11161f] dark:to-[#0f141c]";

const STEP_META = [
  { title: "选择考试批次", description: "确认已有试卷" },
  { title: "导入成绩宽表", description: "全市/本校成绩宽表" },
  { title: "导入各科小题分", description: "按已有试卷逐科上传" }
];

function guessSubjectFromFilename(name: string): string {
  const matched = name.match(/小题分[（(]([^)）]+)[)）]/);
  return matched ? matched[1].trim() : "";
}

function parseJcYearFromName(name?: string | null): number | null {
  const matched = String(name || "").match(/(\d{4})届/);
  if (!matched) return null;
  const year = Number(matched[1]);
  return year >= 1990 && year <= 2099 ? year : null;
}

function warningCount(result: RawImportResult | null): number {
  return result?.warnings?.length ?? 0;
}

function asWarnings(result: RawImportResult | null): RawImportWarning[] {
  return (result?.warnings ?? []).map((w) =>
    typeof w === "object" && w !== null
      ? { row: Number(w.row) || 0, message: String(w.message || "") }
      : { row: 0, message: String(w) }
  );
}

const DETAIL_SUBJECT_ORDER = ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "政治", "地理"];

function papersBySubject(list: RawPaperOption[]): RawPaperOption[] {
  const rank = (subject: string) => {
    const index = DETAIL_SUBJECT_ORDER.indexOf(subject);
    return index === -1 ? DETAIL_SUBJECT_ORDER.length : index;
  };
  return [...list].sort((a, b) => rank(a.subject) - rank(b.subject));
}

function sortDetailPapers(list: RawPaperOption[]): RawPaperOption[] {
  const rank = (subject: string) => {
    const index = DETAIL_SUBJECT_ORDER.indexOf(subject);
    return index === -1 ? DETAIL_SUBJECT_ORDER.length : index;
  };
  return [...list].sort((a, b) => {
    const importedDelta = Number(Boolean(a.detail?.imported)) - Number(Boolean(b.detail?.imported));
    if (importedDelta !== 0) return importedDelta;
    return rank(a.subject) - rank(b.subject);
  });
}

function orderedSubjects(list: RawPaperOption[]): string[] {
  const rank = (subject: string) => {
    const index = DETAIL_SUBJECT_ORDER.indexOf(subject);
    return index === -1 ? DETAIL_SUBJECT_ORDER.length : index;
  };
  return [...list]
    .sort((a, b) => rank(a.subject) - rank(b.subject))
    .map((item) => item.subject)
    .filter(Boolean);
}

function paperLabel(paper: RawPaperOption): string {
  const score = paper.exam_score != null ? ` · ${paper.exam_score}分` : "";
  const imported = paper.detail?.imported ? " · 已导入" : "";
  return `${paper.subject || "未命名"}${score}${imported}`;
}

function detailImportedHint(paper: RawPaperOption): string {
  const d = paper.detail;
  if (!d?.imported) return "";
  return `${paper.subject}小题分已导入 ${d.student_count} 人 / ${d.row_count} 条。再次导入会覆盖原数据。`;
}

function detailWrittenMessage(students: number, detailRows: number): string {
  if (students > 0 && detailRows > 0 && detailRows % students === 0) {
    const perStudent = detailRows / students;
    return `导入成功：${students} 名学生，写入 ${detailRows} 条小题分（每人 ${perStudent} 题）`;
  }
  if (students > 0 && detailRows > 0) {
    return `导入成功：${students} 名学生，写入 ${detailRows} 条小题分`;
  }
  return `导入成功：已写入 ${detailRows} 条小题分`;
}

function overviewImportedHint(status: RawOverviewStatus): string {
  let text = `该批次宽表已导入 ${status.row_count} 条`;
  if (status.school_count > 0) {
    text += `（${status.school_count} 所学校）`;
  }
  if (status.last_write_time) {
    text += `，最近写入 ${status.last_write_time}`;
  }
  return `${text}。可直接去导入小题分，也可重新上传覆盖。`;
}

const DETAIL_STATUS_LABEL: Record<DetailStatus, string> = {
  pending: "待预览",
  validating: "校验中",
  passed: "校验通过（尚未导入）",
  errors: "有错误",
  importing: "导入中",
  imported: "导入成功",
  failed: "失败"
};

const DETAIL_STATUS_COLOR: Record<DetailStatus, string> = {
  pending: "default",
  validating: "processing",
  passed: "blue",
  errors: "error",
  importing: "processing",
  imported: "success",
  failed: "error"
};

function resolveExamId(
  detected: string,
  currentExamId: number | null,
  paperList: RawPaperOption[]
): number | null {
  if (currentExamId != null) return currentExamId;
  if (!detected) return currentExamId;
  const matched = paperList.find((p) => p.subject === detected);
  return matched?.exam_id ?? currentExamId;
}

export default function RawScoreImportPage() {
  const [step, setStep] = useState<WizardStep>(0);
  const [loading, setLoading] = useState(false);
  const [batches, setBatches] = useState<ExamBatchOption[]>([]);
  const [batchId, setBatchId] = useState<number | null>(null);
  const [papers, setPapers] = useState<RawPaperOption[]>([]);
  const [missingSubjects, setMissingSubjects] = useState<string[]>([]);
  const [duplicateSubjects, setDuplicateSubjects] = useState<string[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [newBatchName, setNewBatchName] = useState("");
  const [newBatchJc, setNewBatchJc] = useState<number | null>(null);
  const [newExamTime, setNewExamTime] = useState<Dayjs | null>(null);

  const [overviewFile, setOverviewFile] = useState<File | null>(null);
  const [overviewPreview, setOverviewPreview] = useState<RawImportResult | null>(null);
  const [overviewDone, setOverviewDone] = useState(false);
  const [overviewMessage, setOverviewMessage] = useState("");
  const [dbOverview, setDbOverview] = useState<RawOverviewStatus | null>(null);
  const [jcYear, setJcYear] = useState<number | null>(null);
  const [jcManual, setJcManual] = useState(false);
  const [detailItems, setDetailItems] = useState<DetailItem[]>([]);
  const [papersStatus, setPapersStatus] = useState<"idle" | "loading" | "ok" | "failed">("idle");
  const papersReqId = useRef(0);

  const papersReady =
    Boolean(batchId) && duplicateSubjects.length === 0 && papers.length > 0;
  const readySubjectText = orderedSubjects(papers).join("、");
  const templatePapers = papersBySubject(papers);
  const overviewReady = overviewDone || Boolean(dbOverview?.imported);

  const errorColumns: ColumnsType<ScoreImportErrorRow> = useMemo(
    () => [
      { title: "行号", dataIndex: "row", width: 80 },
      { title: "字段", dataIndex: "field", width: 120 },
      { title: "说明", dataIndex: "message" }
    ],
    []
  );

  const loadBatches = useCallback(async () => {
    setLoading(true);
    try {
      const items = await educationApi.listRawImportBatches(true);
      setBatches(items);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载批次失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPapers = useCallback(async (examBatchId: number) => {
    const reqId = ++papersReqId.current;
    setPapersStatus("loading");
    setLoading(true);
    try {
      const res = await educationApi.listRawImportPapers(examBatchId);
      if (reqId !== papersReqId.current) return;
      setPapers(res.papers);
      setMissingSubjects(res.missing_subjects);
      setDuplicateSubjects(res.duplicate_subjects);
      setDbOverview(res.overview ?? { imported: false, row_count: 0, school_count: 0, last_write_time: null });
      setPapersStatus("ok");
    } catch (err) {
      if (reqId !== papersReqId.current) return;
      setPapers([]);
      setMissingSubjects([]);
      setDuplicateSubjects([]);
      setDbOverview(null);
      setPapersStatus("failed");
      message.error(err instanceof Error ? err.message : "加载试卷失败");
    } finally {
      if (reqId === papersReqId.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  useEffect(() => {
    if (jcManual) return;
    const batch = batches.find((item) => item.id === batchId);
    const batchJc = Number(batch?.jc);
    setJcYear(
      parseJcYearFromName(overviewFile?.name) ??
        (Number.isInteger(batchJc) && batchJc >= 1990 ? batchJc : null)
    );
  }, [overviewFile, batchId, batches, jcManual]);

  const resetImportState = () => {
    setOverviewFile(null);
    setOverviewPreview(null);
    setOverviewDone(false);
    setOverviewMessage("");
    setDbOverview(null);
    setJcYear(null);
    setJcManual(false);
    setDetailItems([]);
  };

  const onSelectBatch = (value: number) => {
    setBatchId(value);
    setPapers([]);
    setMissingSubjects([]);
    setDuplicateSubjects([]);
    resetImportState();
    void loadPapers(value);
  };

  const createBatch = async () => {
    const name = newBatchName.trim();
    const when = newExamTime ? newExamTime.format("YYYY-MM-DD") : "";
    if (!name || !when || newBatchJc == null) {
      message.warning("请填写批次名称、届次和考试时间");
      return;
    }
    setLoading(true);
    try {
      const res = await educationApi.createRawImportBatch(name, when, String(newBatchJc));
      const created = res.data;
      if (!created?.id) {
        throw new Error(res.message || "创建批次失败");
      }
      await loadBatches();
      setBatchId(created.id);
      setPapers([]);
      setMissingSubjects([]);
      setDuplicateSubjects([]);
      resetImportState();
      await loadPapers(created.id);
      setCreateOpen(false);
      setNewBatchName("");
      setNewBatchJc(null);
      setNewExamTime(null);
      if (!res.ok) {
        message.info("已存在，已为您选中");
      } else {
        message.success("批次已创建");
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "创建批次失败");
    } finally {
      setLoading(false);
    }
  };

  const downloadTemplate = async (kind: "overview" | "detail", examId?: number | null) => {
    try {
      const subject =
        kind === "detail" ? papers.find((p) => p.exam_id === examId)?.subject : undefined;
      await educationApi.downloadRawImportTemplate(kind, examId ?? undefined, subject);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "下载模板失败");
    }
  };

  const canVisitStep = (target: number) => {
    if (target <= step) return true;
    if (target === 1) return papersReady;
    if (target === 2) return papersReady && overviewReady;
    return false;
  };

  const runOverview = async (endpoint: "overview-preview" | "overview-execute") => {
    if (!batchId || !overviewFile) {
      message.warning("请先选择批次并上传宽表");
      return;
    }
    if (endpoint === "overview-execute" && jcYear == null) {
      message.warning("请填写届次");
      return;
    }
    const fd = new FormData();
    fd.append("exam_batch_id", String(batchId));
    fd.append("file", overviewFile);
    if (jcYear != null) fd.append("jc", String(jcYear));
    setLoading(true);
    try {
      const res = await educationApi.postRawOverviewImport(endpoint, fd);
      setOverviewPreview(res.data);
      if (!res.ok || (res.data?.error_rows?.length ?? 0) > 0) {
        setOverviewDone(false);
        setOverviewMessage("");
        const failHint =
          endpoint === "overview-execute"
            ? `${res.message || "导入失败"}，可重新上传后再次导入`
            : res.message || "校验未通过";
        message.error(failHint);
        return;
      }
      if (endpoint === "overview-execute") {
        setOverviewDone(true);
        const summary = res.data?.summary || {};
        const upserted = Number(summary.overview_upserted || 0);
        setDbOverview((prev) => ({
          imported: true,
          row_count: upserted || prev?.row_count || 0,
          school_count: prev?.school_count ?? 0,
          last_write_time: prev?.last_write_time ?? null
        }));
        setOverviewMessage(
          `宽表导入成功：overview ${upserted}，学生 ${Number(
            summary.students_upserted || 0
          )}，成绩 ${Number(summary.score_upserted || 0)}`
        );
        message.success("宽表导入成功，异常扫描已在后台进行");
      } else {
        message.success("预览校验通过");
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "宽表导入失败");
    } finally {
      setLoading(false);
    }
  };

  const patchDetail = (uid: string, patch: Partial<DetailItem>) => {
    setDetailItems((prev) => prev.map((item) => (item.uid === uid ? { ...item, ...patch } : item)));
  };

  const runDetail = async (
    item: DetailItem,
    endpoint: "detail-preview" | "detail-execute"
  ): Promise<boolean> => {
    if (!batchId || item.examId == null) {
      message.warning("请为该文件选择试卷");
      return false;
    }
    const fd = new FormData();
    fd.append("exam_batch_id", String(batchId));
    fd.append("exam_id", String(item.examId));
    fd.append("file", item.file);
    patchDetail(item.uid, {
      status: endpoint === "detail-preview" ? "validating" : "importing",
      message: ""
    });
    try {
      const res = await educationApi.postRawDetailImport(endpoint, fd);
      const detected = String(res.data?.summary?.detected_subject ?? "").trim();
      const nextExamId = resolveExamId(detected, item.examId, papers);
      if (nextExamId !== item.examId) {
        patchDetail(item.uid, {
          examId: nextExamId,
          status: "pending",
          preview: null,
          message: "已按识别科目切换试卷，请重新预览"
        });
        return false;
      }
      const errors = res.data?.error_rows ?? [];
      if (!res.ok || errors.length > 0) {
        patchDetail(item.uid, {
          status: "errors",
          preview: res.data,
          message: res.message || "校验未通过"
        });
        return false;
      }
      if (endpoint === "detail-execute") {
        const upserted = Number(res.data?.summary?.detail_upserted || 0);
        const students = Number(res.data?.valid_rows || res.data?.summary?.students_matched || 0);
        const written = detailWrittenMessage(students, upserted);
        patchDetail(item.uid, {
          status: "imported",
          preview: res.data,
          message: written
        });
        setPapers((prev) =>
          prev.map((p) =>
            p.exam_id === item.examId
              ? {
                  ...p,
                  detail: {
                    imported: true,
                    row_count: upserted || p.detail?.row_count || 0,
                    student_count: students || p.detail?.student_count || 0
                  }
                }
              : p
          )
        );
        message.success(
          `${detected || "该科"}${written.replace(/^导入成功/, "小题分导入成功")}`
        );
        return true;
      }
      patchDetail(item.uid, {
        status: "passed",
        preview: res.data,
        message: `校验通过 ${res.data?.valid_rows ?? 0} 行，尚未写入数据库`
      });
      return true;
    } catch (err) {
      patchDetail(item.uid, {
        status: "failed",
        message: err instanceof Error ? err.message : "小题分导入失败"
      });
      return false;
    }
  };

  const previewAllDetails = async () => {
    if (!overviewReady) {
      message.warning("请先完成宽表导入");
      return;
    }
    setLoading(true);
    try {
      for (const item of detailItems) {
        if (item.status === "imported") continue;
        await runDetail(item, "detail-preview");
      }
    } finally {
      setLoading(false);
    }
  };

  const executePassedDetails = async () => {
    if (!overviewReady) {
      message.warning("请先完成宽表导入");
      return;
    }
    const passed = detailItems.filter((item) => item.status === "passed");
    if (!passed.length) {
      message.warning("没有校验通过的文件可导入");
      return;
    }
    const already = passed.filter((item) =>
      papers.some((p) => p.exam_id === item.examId && p.detail?.imported)
    );
    const run = async () => {
      setLoading(true);
      try {
        for (const item of passed) {
          await runDetail(item, "detail-execute");
        }
      } finally {
        setLoading(false);
      }
    };
    if (already.length) {
      const names = already
        .map((item) => papers.find((p) => p.exam_id === item.examId)?.subject || item.file.name)
        .join("、");
      Modal.confirm({
        title: "部分科目已导入过小题分",
        content: `${names} 再次导入会覆盖原数据。确定继续？`,
        okText: "覆盖导入",
        cancelText: "取消",
        onOk: () => run()
      });
      return;
    }
    await run();
  };

  const selectedBatch = batches.find((b) => b.id === batchId) || null;
  const overviewCanExecute = Boolean(
    overviewPreview &&
      overviewPreview.error_rows.length === 0 &&
      overviewPreview.valid_rows > 0 &&
      jcYear != null &&
      !overviewDone
  );

  const renderKpis = (result: RawImportResult | null) => {
    if (!result) return null;
    const items = [
      { label: "总行数", value: result.total_rows },
      { label: "校验通过", value: result.valid_rows },
      { label: "错误行", value: result.error_rows.length },
      { label: "警告行", value: warningCount(result) }
    ];
    return (
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {items.map((item) => (
          <div key={item.label} className={KPI_SURFACE}>
            <div className="text-xs text-slate-500">{item.label}</div>
            <div className="mt-1 text-2xl font-semibold text-slate-800 dark:text-slate-100">
              {item.value}
            </div>
          </div>
        ))}
      </div>
    );
  };

  const renderPreviewTables = (
    result: RawImportResult | null,
    opts?: { imported?: boolean; importedHint?: string; countUnit?: string }
  ) => {
    if (!result) return null;
    const warns = asWarnings(result);
    const keys = result.preview_sample?.[0] ? Object.keys(result.preview_sample[0]) : [];
    return (
      <>
        {result.error_rows.length > 0 ? (
          <Table<ScoreImportErrorRow>
            className="mb-3"
            size="small"
            rowKey={(r) => `${r.row}-${r.field}-${r.message}`}
            columns={errorColumns}
            dataSource={result.error_rows}
            pagination={{ pageSize: 8 }}
          />
        ) : (
          <Alert
            className="mb-3"
            type="success"
            showIcon
            icon={<CheckCircleOutlined />}
            message={
              opts?.imported
                ? opts.importedHint || "导入成功，数据已写入"
                : "校验通过（尚未写入），请点击确认导入"
            }
          />
        )}
        {warns.length > 0 ? (
          <Collapse
            className="mb-3"
            items={[
              {
                key: "warnings",
                label: `警告 ${warns.length} 条`,
                children: (
                  <ul className="m-0 list-disc pl-5 text-sm">
                    {warns.map((w, i) => (
                      <li key={`${w.row}-${i}`}>
                        {w.row ? `第 ${w.row} 行：` : ""}
                        {w.message}
                      </li>
                    ))}
                  </ul>
                )
              }
            ]}
          />
        ) : null}
        {result.preview_sample.length > 0 ? (
          <Table
            size="small"
            rowKey={(row, i) => String(row.anon_stu_id || i)}
            columns={(result.preview_columns?.length
              ? result.preview_columns.map((c) => ({
                  title: c.title,
                  dataIndex: c.key,
                  key: c.key,
                  ellipsis: true
                }))
              : keys.map((k) => ({ title: k, dataIndex: k, key: k }))
            )}
            dataSource={result.preview_sample}
            pagination={{
              pageSize: 10,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50, 100],
              showTotal: (total) => `共 ${total} ${opts?.countUnit || "条"}`
            }}
            scroll={{ x: "max-content" }}
          />
        ) : null}
      </>
    );
  };

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <Spin spinning={loading}>
      <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Link href="/construct/education/score-library" className="mb-1 inline-block text-sm text-[#2563eb]">
            返回成绩库
          </Link>
          <Typography.Title level={4} className="!mb-1">
            <UploadOutlined className="mr-2" />
            成绩导入
          </Typography.Title>
          <Typography.Text className="oc-muted">
            教科院材料两步导入：成绩宽表 → 各科小题分
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadBatches()} loading={loading}>
            刷新批次
          </Button>
          <Button
            onClick={() => {
              papersReqId.current += 1;
              setStep(0);
              setBatchId(null);
              setPapers([]);
              setMissingSubjects([]);
              setDuplicateSubjects([]);
              setPapersStatus("idle");
              resetImportState();
              message.success("已重置向导");
            }}
          >
            重新开始
          </Button>
        </Space>
      </div>

      <div className={`${PANEL_CARD} mb-4`}>
        <Steps
          current={step}
          onChange={(value) => {
            if (canVisitStep(value)) setStep(value as WizardStep);
          }}
          items={STEP_META.map((s, idx) => ({
            title: s.title,
            disabled: !canVisitStep(idx),
            description:
              idx === 0 && papersReady
                ? `已有 ${papers.length} 科试卷`
                : idx === 1 && overviewReady
                  ? "已导入"
                  : s.description
          }))}
        />
      </div>

        {step === 0 ? (
          <div className={PANEL_CARD}>
            <div className="mb-4 rounded-xl border border-[#bfdbfe] bg-[#eff6ff] p-4 text-sm text-[#1d4ed8] dark:border-[#1e3a5f] dark:bg-[#1e3a5f]/40 dark:text-[#93c5fd]">
              导入顺序：先导入成绩宽表，再按已有试卷导入小题分。批次里已建哪科试卷，就可以导入该科成绩；缺卷科目可以以后补。宽表成功后会扫描异常提醒，不会重算达线。
            </div>
            <Space direction="vertical" size="middle" className="w-full max-w-2xl">
              <div>
                <Typography.Text className="mb-1 block">考试批次</Typography.Text>
                <Space wrap>
                  <Select
                    className="min-w-[320px]"
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择考试批次"
                    value={batchId ?? undefined}
                    onChange={onSelectBatch}
                    options={batches.map((b) => ({
                      value: b.id,
                      label: b.batch_name
                    }))}
                  />
                  <Button onClick={() => setCreateOpen(true)}>新建批次</Button>
                </Space>
              </div>
              {!selectedBatch ? null : papersStatus === "loading" ? (
                <Alert type="info" showIcon message="正在加载试卷…" />
              ) : papersStatus === "failed" ? (
                <Alert type="warning" showIcon message="试卷加载失败，请刷新后重试" />
              ) : duplicateSubjects.length > 0 ? (
                <Alert
                  type="error"
                  showIcon
                  message="存在同科多卷，无法进入宽表导入"
                  description={<div>同科多卷：{duplicateSubjects.join("、")}</div>}
                />
              ) : papersReady ? (
                <Alert
                  type={missingSubjects.length > 0 ? "info" : "success"}
                  showIcon
                  message={
                    overviewReady
                      ? `已有${readySubjectText}试卷，宽表已导入，可以导入这些科目的小题分`
                      : `已有${readySubjectText}试卷，可以导入成绩宽表和这些科目的小题分`
                  }
                  description={
                    <div>
                      {missingSubjects.length > 0 ? (
                        <div>
                          尚未建卷：{missingSubjects.join("、")}。这些科目的小题分等试卷建好后再导入。
                          <Link href="/construct/education/exam-library">去试卷库</Link>
                        </div>
                      ) : null}
                      {overviewReady && dbOverview ? <div>{overviewImportedHint(dbOverview)}</div> : null}
                    </div>
                  }
                />
              ) : (
                <Alert type="warning" showIcon message="该批次暂无可用试卷" />
              )}
            </Space>
            <div className="mt-4 flex justify-end">
              <Space>
                {overviewReady ? (
                  <Button type="primary" disabled={!papersReady} onClick={() => setStep(2)}>
                    去导入小题分
                  </Button>
                ) : null}
                <Button type={overviewReady ? "default" : "primary"} disabled={!papersReady} onClick={() => setStep(1)}>
                  {overviewReady ? "重新导入宽表" : "下一步"}
                </Button>
              </Space>
            </div>
          </div>
        ) : null}

        {step === 1 ? (
          <div className={PANEL_CARD}>
            {dbOverview?.imported && !overviewMessage ? (
              <Alert
                className="mb-4"
                type="info"
                showIcon
                message={overviewImportedHint(dbOverview)}
                action={
                  <Button type="primary" onClick={() => setStep(2)}>
                    去导入小题分
                  </Button>
                }
              />
            ) : null}
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-slate-800 dark:text-slate-100">成绩宽表</div>
                <div className="mt-0.5 text-xs text-slate-500">按模板填写，表头列名不要改</div>
              </div>
              <Button icon={<DownloadOutlined />} onClick={() => void downloadTemplate("overview")}>
                下载宽表模板
              </Button>
            </div>
            <Upload.Dragger
              accept=".xlsx"
              maxCount={1}
              beforeUpload={(file) => {
                setOverviewFile(file);
                setOverviewPreview(null);
                setOverviewDone(false);
                setOverviewMessage("");
                setJcManual(false);
                return false;
              }}
              onRemove={() => {
                setOverviewFile(null);
                setOverviewPreview(null);
                setOverviewDone(false);
                setOverviewMessage("");
                setJcManual(false);
              }}
              fileList={
                overviewFile
                  ? [{ uid: "overview", name: overviewFile.name, status: "done" }]
                  : []
              }
            >
              <p className="ant-upload-text">点击或拖拽上传成绩宽表（.xlsx）</p>
            </Upload.Dragger>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <span className="text-sm text-slate-600 dark:text-slate-300">届次</span>
              <InputNumber
                min={1990}
                max={2099}
                precision={0}
                value={jcYear ?? undefined}
                placeholder="如 2026"
                onChange={(value) => {
                  setJcManual(true);
                  setJcYear(typeof value === "number" ? value : null);
                }}
              />
              <span className="text-xs text-slate-400">
                须与考试批次届次一致
                {selectedBatch?.jc ? `（本批次 ${selectedBatch.jc}届）` : ""}
                。文件名或宽表中的届次不同时会拒绝导入。
              </span>
            </div>
            <Space className="mt-4">
              <Button
                disabled={!overviewFile}
                loading={loading}
                onClick={() => void runOverview("overview-preview")}
              >
                预览校验
              </Button>
              <Button
                type="primary"
                disabled={!overviewCanExecute}
                loading={loading}
                onClick={() => void runOverview("overview-execute")}
              >
                确认导入
              </Button>
            </Space>
            {overviewMessage ? (
              <Alert
                className="mt-4"
                type="success"
                showIcon
                icon={<CheckCircleOutlined />}
                message={overviewMessage}
                description={
                  <Link href="/construct/education/anomaly-alerts">去异常提醒查看</Link>
                }
                action={
                  <Button type="primary" onClick={() => setStep(2)}>
                    下一步
                  </Button>
                }
              />
            ) : null}
            <div className="mt-4">
              {renderKpis(overviewPreview)}
              {renderPreviewTables(
                overviewPreview,
                overviewDone
                  ? { imported: true, importedHint: overviewMessage || "宽表导入成功，数据已写入" }
                  : undefined
              )}
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className={PANEL_CARD}>
            {!overviewReady ? (
              <Alert className="mb-4" type="warning" showIcon message="请先完成宽表导入后再预览/导入小题分" />
            ) : null}
            {papers.some((p) => p.detail?.imported) ? (
              <Alert
                className="mb-4"
                type="warning"
                showIcon
                message={`以下科目已导入过小题分：${papers
                  .filter((p) => p.detail?.imported)
                  .map((p) => p.subject)
                  .join("、")}`}
                description="再次导入会覆盖原数据，请确认后再操作。"
              />
            ) : null}
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-slate-800 dark:text-slate-100">各科小题分</div>
                <div className="mt-0.5 text-xs text-slate-500">点开后选择科目，模板按该卷题目生成</div>
              </div>
              <Dropdown
                trigger={["click"]}
                disabled={!templatePapers.length}
                menu={{
                  items: templatePapers.map((paper) => ({
                    key: String(paper.exam_id),
                    label: paper.subject,
                    onClick: () => void downloadTemplate("detail", paper.exam_id)
                  }))
                }}
              >
                <Button icon={<DownloadOutlined />} disabled={!templatePapers.length}>
                  下载模板
                  <DownOutlined className="ml-1 text-[10px]" />
                </Button>
              </Dropdown>
            </div>
            <Upload.Dragger
              accept=".xls,.xlsx"
              multiple
              disabled={!overviewReady}
              beforeUpload={(file) => {
                const guess = guessSubjectFromFilename(file.name);
                const matched = papers.find((p) => p.subject === guess);
                setDetailItems((prev) => [
                  ...prev,
                  {
                    uid: `${file.name}-${file.size}-${Date.now()}-${prev.length}`,
                    file,
                    subjectGuess: guess,
                    examId: matched?.exam_id ?? null,
                    status: "pending",
                    preview: null,
                    message: ""
                  }
                ]);
                return false;
              }}
              showUploadList={false}
            >
              <p className="ant-upload-text">点击或拖拽上传各科小题分（.xls / .xlsx）</p>
            </Upload.Dragger>
            <Space className="mt-4" wrap>
              <Button
                disabled={!overviewReady || !detailItems.length}
                loading={loading}
                onClick={() => void previewAllDetails()}
              >
                全部预览
              </Button>
              <Button
                type="primary"
                disabled={!overviewReady || !detailItems.some((i) => i.status === "passed")}
                loading={loading}
                onClick={() => void executePassedDetails()}
              >
                导入全部通过项
              </Button>
            </Space>
            <div className="mt-4 space-y-3">
              {detailItems.map((item) => {
                const detected = String(item.preview?.summary?.detected_subject || item.subjectGuess || "");
                const paper = papers.find((p) => p.exam_id === item.examId);
                const alreadyInDb = Boolean(paper?.detail?.imported) && item.status !== "imported";
                const cardClass =
                  item.status === "imported"
                    ? "rounded-xl border border-[#86efac] bg-[#f0fdf4] p-4 dark:border-[#166534] dark:bg-[#14532d]/30"
                    : item.status === "passed"
                      ? "rounded-xl border border-[#93c5fd] bg-[#eff6ff] p-4 dark:border-[#1e3a5f] dark:bg-[#1e3a5f]/40"
                      : "rounded-xl border border-[#e2e8f0] p-4 dark:border-[#334155]";
                return (
                  <div key={item.uid} className={cardClass}>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="font-medium">{item.file.name}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                          {detected ? (
                            <span className="inline-flex items-center rounded-full bg-[#eff6ff] px-2 py-0.5 text-[#1d4ed8] dark:bg-[#1e3a5f]/60 dark:text-[#93c5fd]">
                              {detected}·自动识别
                            </span>
                          ) : (
                            <span>未识别科目（可用文件名回退）</span>
                          )}
                          <Tag color={DETAIL_STATUS_COLOR[item.status]}>
                            {DETAIL_STATUS_LABEL[item.status]}
                          </Tag>
                          {item.message ? <span>{item.message}</span> : null}
                        </div>
                      </div>
                      <Space wrap>
                        <Select
                          className="min-w-[200px]"
                          placeholder="选择试卷"
                          value={item.examId ?? undefined}
                          onChange={(examId) =>
                            patchDetail(item.uid, {
                              examId,
                              status: "pending",
                              preview: null,
                              message: ""
                            })
                          }
                          options={sortDetailPapers(papers).map((p) => ({ value: p.exam_id, label: paperLabel(p) }))}
                        />
                        <Button
                          disabled={!overviewReady || item.examId == null}
                          loading={loading}
                          onClick={() => {
                            setLoading(true);
                            void runDetail(item, "detail-preview").finally(() => setLoading(false));
                          }}
                        >
                          预览校验
                        </Button>
                        <Button
                          type="primary"
                          disabled={!overviewReady || item.status !== "passed"}
                          loading={loading}
                          onClick={() => {
                            if (alreadyInDb && paper) {
                              Modal.confirm({
                                title: `${paper.subject}小题分已导入过`,
                                content: `${detailImportedHint(paper)}确定覆盖导入？`,
                                okText: "覆盖导入",
                                cancelText: "取消",
                                onOk: () => {
                                  setLoading(true);
                                  return runDetail(item, "detail-execute").finally(() =>
                                    setLoading(false)
                                  );
                                }
                              });
                              return;
                            }
                            setLoading(true);
                            void runDetail(item, "detail-execute").finally(() => setLoading(false));
                          }}
                        >
                          确认导入
                        </Button>
                        <Button
                          onClick={() =>
                            setDetailItems((prev) => prev.filter((row) => row.uid !== item.uid))
                          }
                        >
                          移除
                        </Button>
                      </Space>
                    </div>
                    {alreadyInDb && paper ? (
                      <Alert
                        className="mt-3"
                        type="warning"
                        showIcon
                        message={detailImportedHint(paper)}
                      />
                    ) : null}
                    {item.preview ? (
                      <div className="mt-3">
                        {renderPreviewTables(item.preview, {
                          imported: item.status === "imported",
                          importedHint: item.message,
                          countUnit: "名学生"
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

      {step > 0 ? (
        <div className="mt-4">
          <Button onClick={() => setStep((step - 1) as WizardStep)}>上一步</Button>
        </div>
      ) : null}
      </div>
      </Spin>

      <Modal
        title="新建考试批次"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void createBatch()}
        confirmLoading={loading}
      >
        <Space direction="vertical" className="w-full">
          <Input
            placeholder="批次名称"
            value={newBatchName}
            onChange={(e) => {
              const name = e.target.value;
              setNewBatchName(name);
              const matched = name.match(/(\d{4})届/);
              const year = matched ? Number(matched[1]) : null;
              if (year && year >= 1990 && year <= 2099 && newBatchJc == null) {
                setNewBatchJc(year);
              }
            }}
          />
          <InputNumber
            className="w-full"
            min={1990}
            max={2099}
            precision={0}
            placeholder="届次，如 2027"
            value={newBatchJc ?? undefined}
            onChange={(value) => setNewBatchJc(typeof value === "number" ? value : null)}
          />
          <DatePicker
            className="w-full"
            value={newExamTime}
            onChange={(v) => setNewExamTime(v)}
          />
        </Space>
      </Modal>
    </div>
  );
}
