import {
  AimOutlined,
  AlertOutlined,
  ApartmentOutlined,
  ArrowLeftOutlined,
  AuditOutlined,
  DashboardOutlined,
  DotChartOutlined,
  DownloadOutlined,
  ExpandOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  FundProjectionScreenOutlined,
  HistoryOutlined,
  IdcardOutlined,
  LineChartOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message
} from "antd";
import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { datasourceApi, type DatasourceItem } from "@/api/datasource";
import {
  educationApi,
  type BatchReportItem,
  type GenerateReportResult,
  type MetaOptions
} from "@/api/education";
import { exportReportAsWord, sanitizeFileName } from "@/utils/exportReportWord";

/** 分析工具已开放的报告类型 */
const OPEN_REPORT_TYPES = new Set([
  "class_overview",
  "grade_comparison",
  "subject_diagnosis",
  "student_profile",
  "trend_tracking",
  "tier_alert",
  "group_feature",
  "comprehensive",
  "diagnostic_report",
  "line_reach",
  "subject_avg",
  "assign_grade",
  "rank_bucket",
  "contribution",
  "combo_reach",
  "elite_roster"
]);

const AUDIENCE_OPTIONS = [
  { value: "default", label: "默认" },
  { value: "principal", label: "校长/教务" },
  { value: "grade_head", label: "年级主任" },
  { value: "head_teacher", label: "班主任" },
  { value: "subject_teacher", label: "任课教师" },
  { value: "parent", label: "家长" }
];

interface SkillItem {
  id: string;
  name: string;
  icon: string;
  tags: string[];
  report_type: string;
  audience_default: string;
  desc: string;
}

interface SkillsConfig {
  skills: SkillItem[];
}

const ICON_MAP: Record<string, ReactNode> = {
  "dashboard-outlined": <DashboardOutlined />,
  "apartment-outlined": <ApartmentOutlined />,
  "aim-outlined": <AimOutlined />,
  "idcard-outlined": <IdcardOutlined />,
  "line-chart-outlined": <LineChartOutlined />,
  "alert-outlined": <AlertOutlined />,
  "dot-chart-outlined": <DotChartOutlined />,
  "fund-projection-screen-outlined": <FundProjectionScreenOutlined />,
  "audit-outlined": <AuditOutlined />
};

type FormValues = {
  datasource_id: number;
  audience: string;
  class_name?: string;
  /** 单选为 string；区域诊断等多场为 string[] */
  exam_name?: string | string[];
  subject?: string;
  school_name?: string;
  student_name?: string;
  include_charts: boolean;
};

function fieldsForType(reportType: string): {
  class_name?: boolean;
  exam_name?: boolean;
  /** 考试可多选（区域结构化诊断等） */
  exam_multi?: boolean;
  subject?: boolean;
  school_name?: boolean;
  student_name?: boolean;
} {
  switch (reportType) {
    case "class_overview":
      return { class_name: true, exam_name: true, subject: true };
    case "grade_comparison":
      return { school_name: true, exam_name: true, subject: true };
    case "subject_diagnosis":
      return { school_name: true, class_name: true, exam_name: true, subject: true };
    case "student_profile":
      return { student_name: true, exam_name: true, subject: true, class_name: true };
    case "trend_tracking":
      return { class_name: true, subject: true, school_name: true };
    case "tier_alert":
      return { class_name: true, exam_name: true, subject: true, school_name: true };
    case "group_feature":
      return { school_name: true, exam_name: true, subject: true };
    case "comprehensive":
      return { class_name: true, subject: true, school_name: true };
    case "diagnostic_report":
      return { school_name: true, exam_name: true, exam_multi: true, subject: true };
    case "line_reach":
      return { exam_name: true };
    case "subject_avg":
    case "assign_grade":
    case "rank_bucket":
    case "contribution":
    case "combo_reach":
    case "elite_roster":
      return { exam_name: true };
    default:
      return { class_name: true, exam_name: true };
  }
}

/** 多选考试用 ;; 序列化，与后端 _split_exam_filter 对齐 */
function serializeExamFilter(exam: string | string[] | undefined): string | undefined {
  if (Array.isArray(exam)) {
    const parts = exam.map((s) => String(s || "").trim()).filter(Boolean);
    return parts.length ? parts.join(";;") : undefined;
  }
  const s = String(exam || "").trim();
  return s || undefined;
}

export default function AnalysisToolPage() {
  const router = useRouter();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [selected, setSelected] = useState<SkillItem | null>(null);
  const [datasources, setDatasources] = useState<DatasourceItem[]>([]);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<GenerateReportResult | null>(null);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [exportingWord, setExportingWord] = useState(false);
  const [savingHistory, setSavingHistory] = useState(false);
  const [showExpand, setShowExpand] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchClasses, setBatchClasses] = useState<string[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchItems, setBatchItems] = useState<BatchReportItem[]>([]);
  const [metaLoading, setMetaLoading] = useState(false);
  const [meta, setMeta] = useState<MetaOptions>({
    schools: [],
    exams: [],
    classes: [],
    subjects: []
  });
  const reportIframeRef = useRef<HTMLIFrameElement | null>(null);
  const deepLinkApplied = useRef(false);
  const [form] = Form.useForm<FormValues>();

  const datasourceId = Form.useWatch("datasource_id", form);
  const schoolName = Form.useWatch("school_name", form);
  const examName = Form.useWatch("exam_name", form);
  const className = Form.useWatch("class_name", form);
  const subjectName = Form.useWatch("subject", form);

  useEffect(() => {
    let cancelled = false;
    fetch("/education_skills.json", { cache: "no-cache" })
      .then((r) => r.json() as Promise<SkillsConfig>)
      .then((cfg) => {
        if (!cancelled) setSkills(Array.isArray(cfg.skills) ? cfg.skills : []);
      })
      .catch(() => {
        if (!cancelled) message.error("报告类型配置加载失败");
      })
      .finally(() => {
        if (!cancelled) setSkillsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void datasourceApi
      .list({ limit: 200 })
      .then((res) => {
        if (cancelled) return;
        const items = res.items || [];
        setDatasources(items);
        if (items.length > 0) {
          form.setFieldsValue({ datasource_id: items[0].id });
        }
      })
      .catch(() => {
        if (!cancelled) message.error("加载数据源失败");
      });
    return () => {
      cancelled = true;
    };
  }, [form]);

  useEffect(() => {
    if (!datasourceId) {
      setMeta({ schools: [], exams: [], classes: [], subjects: [] });
      return;
    }
    let cancelled = false;
    setMetaLoading(true);
    void educationApi
      .listMetaOptions({
        datasource_id: datasourceId,
        school_name: schoolName || undefined,
        exam_name: Array.isArray(examName)
          ? examName[0] || undefined
          : examName || undefined,
        class_name: className || undefined,
        subject: subjectName || undefined
      })
      .then((opts) => {
        if (!cancelled) setMeta(opts);
      })
      .catch((err) => {
        if (!cancelled) {
          message.warning(err instanceof Error ? err.message : "筛选项加载失败，可手动输入");
        }
      })
      .finally(() => {
        if (!cancelled) setMetaLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasourceId, schoolName, examName, className, subjectName]);

  const toSelectOptions = (items: string[]) => items.map((v) => ({ value: v, label: v }));

  const fieldFlags = useMemo(
    () => (selected ? fieldsForType(selected.report_type) : {}),
    [selected]
  );

  const selectSkill = (skill: SkillItem) => {
    if (!OPEN_REPORT_TYPES.has(skill.report_type)) {
      message.info("该报告类型暂未开放");
      return;
    }
    setSelected(skill);
    setResult(null);
    setBatchItems([]);
    form.setFieldsValue({
      audience: skill.audience_default || "default",
      include_charts: true,
      class_name: undefined,
      exam_name: undefined,
      subject: undefined,
      school_name: undefined,
      student_name: undefined
    });
  };

  // 技能页「一键生成」：?report_type=xxx 自动进入对应表单
  useEffect(() => {
    if (deepLinkApplied.current || skillsLoading || skills.length === 0) return;
    const raw = router.query.report_type;
    const rt = typeof raw === "string" ? raw.trim() : Array.isArray(raw) ? raw[0] : "";
    if (!rt) return;
    const skill = skills.find((s) => s.report_type === rt);
    if (!skill) return;
    deepLinkApplied.current = true;
    selectSkill(skill);
  }, [skills, skillsLoading, router.query.report_type]);

  const onGenerate = async (values: FormValues) => {
    if (!selected) return;
    setGenerating(true);
    setResult(null);
    try {
      const filters: Record<string, string> = {};
      if (values.class_name?.trim()) filters.class_name = values.class_name.trim();
      const examFilter = serializeExamFilter(values.exam_name);
      if (examFilter) filters.exam_name = examFilter;
      if (values.subject?.trim()) filters.subject = values.subject.trim();
      if (values.school_name?.trim()) filters.school_name = values.school_name.trim();
      if (values.student_name?.trim()) filters.student_name = values.student_name.trim();

      const res = await educationApi.generateReport({
        datasource_id: values.datasource_id,
        report_type: selected.report_type,
        audience: values.audience,
        filters,
        include_charts: values.include_charts
      });
      if (!res.ok || !res.data) {
        message.error(res.message || "生成失败");
        return;
      }
      if (res.data.error) {
        message.error(res.data.error);
        setResult(res.data);
        return;
      }
      setResult(res.data);
      message.success("报告已生成");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "生成失败");
    } finally {
      setGenerating(false);
    }
  };

  const buildFiltersFromForm = (): Record<string, string> => {
    const values = form.getFieldsValue();
    const filters: Record<string, string> = {};
    if (values.class_name?.trim()) filters.class_name = values.class_name.trim();
    const examFilter = serializeExamFilter(values.exam_name);
    if (examFilter) filters.exam_name = examFilter;
    if (values.subject?.trim()) filters.subject = values.subject.trim();
    if (values.school_name?.trim()) filters.school_name = values.school_name.trim();
    if (values.student_name?.trim()) filters.student_name = values.student_name.trim();
    return filters;
  };

  const onSaveHistory = async () => {
    if (!result?.html || result.error || !selected) {
      message.error("请先成功生成报告");
      return;
    }
    const dsId = form.getFieldValue("datasource_id");
    if (!dsId) {
      message.error("请选择数据源");
      return;
    }
    setSavingHistory(true);
    try {
      const filters = buildFiltersFromForm();
      const filterHint = Object.entries(filters)
        .map(([k, v]) => `${k}=${v}`)
        .join("，");
      const res = await educationApi.saveReportHistory({
        datasource_id: dsId,
        title: result.title || selected.name,
        html: result.html,
        report_type: result.report_type,
        report_type_label: result.report_type_label,
        question: `分析工具 · ${selected.name}${filterHint ? `（${filterHint}）` : ""}`
      });
      if (!res.ok || !res.data) {
        message.error(res.message || "保存失败");
        return;
      }
      message.success("已保存到报告历史");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSavingHistory(false);
    }
  };

  const onBatchGenerate = async () => {
    if (!selected) return;
    if (batchClasses.length === 0) {
      message.warning("请至少选择一个班级");
      return;
    }
    const dsId = form.getFieldValue("datasource_id");
    if (!dsId) {
      message.error("请选择数据源");
      return;
    }
    setBatchLoading(true);
    setBatchItems([]);
    try {
      const filters = buildFiltersFromForm();
      delete filters.class_name;
      const res = await educationApi.batchReport({
        datasource_id: dsId,
        report_type: selected.report_type,
        class_names: batchClasses,
        audience: form.getFieldValue("audience"),
        filters,
        include_charts: form.getFieldValue("include_charts") !== false
      });
      if (!res.ok || !res.data) {
        message.error(res.message || "批量生成失败");
        return;
      }
      setBatchItems(res.data.items || []);
      const failed = (res.data.items || []).filter((i) => i.error).length;
      if (failed > 0) {
        message.warning(`批量完成：成功 ${(res.data.items || []).length - failed}，失败 ${failed}`);
      } else {
        message.success(res.message || "批量生成完成");
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "批量生成失败");
    } finally {
      setBatchLoading(false);
    }
  };

  const sanitizeName = (name: string) => sanitizeFileName(name);

  const reportTitle = result?.title || selected?.name || "report";
  const safeReportHtml = result?.html || "";

  const exportHtml = () => {
    if (!safeReportHtml.trim()) {
      message.error("暂无报告内容");
      return;
    }
    const title = sanitizeName(reportTitle);
    const blob = new Blob([safeReportHtml], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title}.html`;
    a.click();
    URL.revokeObjectURL(url);
    message.success("HTML 已下载");
  };

  const exportReportWord = async () => {
    setExportingWord(true);
    try {
      await exportReportAsWord({
        title: reportTitle,
        html: safeReportHtml,
        iframe: reportIframeRef.current
      });
      message.success("Word 已导出");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "Word 导出失败");
      // eslint-disable-next-line no-console
      console.error(e);
    } finally {
      setExportingWord(false);
    }
  };

  const exportReportPdf = async () => {
    const iframe = reportIframeRef.current;
    const doc = iframe?.contentDocument;
    if (!doc || !doc.body) {
      message.error("无法访问报告内容，请稍候预览加载完成后再试");
      return;
    }
    setExportingPdf(true);
    try {
      const [{ default: html2canvas }, jspdfMod] = await Promise.all([
        import("html2canvas"),
        import("jspdf")
      ]);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const JsPDF = (jspdfMod as any).jsPDF;
      const canvas = await html2canvas(doc.body, {
        scale: 2,
        useCORS: true,
        backgroundColor: "#ffffff"
      });
      const pdf = new JsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
      const pageW = pdf.internal.pageSize.getWidth();
      const pageH = pdf.internal.pageSize.getHeight();
      const imgW = pageW;
      const imgH = (canvas.height * imgW) / canvas.width;
      let remaining = imgH;
      let position = 0;
      const imgData = canvas.toDataURL("image/png");
      pdf.addImage(imgData, "PNG", 0, position, imgW, imgH);
      remaining -= pageH;
      while (remaining > 0) {
        position -= pageH;
        pdf.addPage();
        pdf.addImage(imgData, "PNG", 0, position, imgW, imgH);
        remaining -= pageH;
      }
      pdf.save(`${sanitizeName(reportTitle)}.pdf`);
      message.success("PDF 已导出");
    } catch (e) {
      message.error("PDF 导出失败");
      // eslint-disable-next-line no-console
      console.error(e);
    } finally {
      setExportingPdf(false);
    }
  };

  return (
    <div className="dbgpt-ui-font flex h-[calc(100vh-3.5rem)] min-h-0 flex-col overflow-hidden px-4 pb-3 pt-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Typography.Title level={4} style={{ margin: 0 }}>
          分析工具
        </Typography.Title>
        <Link href="/construct/analysis/history">
          <Button icon={<HistoryOutlined />}>报告历史</Button>
        </Link>
      </div>

      {!selected ? (
        <div className="grid flex-1 grid-cols-1 content-start gap-3 overflow-y-auto px-1 pb-2 pt-1 sm:grid-cols-2 lg:grid-cols-3">
          {skillsLoading
            ? null
            : skills.map((skill) => {
                const enabled = OPEN_REPORT_TYPES.has(skill.report_type);
                return (
                  <Card
                    key={skill.id}
                    variant="borderless"
                    className={[
                      "h-full rounded-2xl border border-[#e2e8f0]",
                      "bg-gradient-to-br from-white via-[#fafcff] to-[#f1f6ff]",
                      "shadow-[0_2px_14px_rgba(15,23,42,0.06)] transition-all duration-200",
                      "dark:border-[#334155] dark:from-[#141923] dark:via-[#11161f] dark:to-[#0f141c]",
                      enabled
                        ? "cursor-pointer hover:-translate-y-0.5 hover:border-[#93c5fd] hover:shadow-[0_10px_28px_rgba(37,99,235,0.12)] dark:hover:border-[#3b82f6]/50"
                        : "cursor-not-allowed opacity-55"
                    ].join(" ")}
                    styles={{
                      header: {
                        borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
                        padding: "12px 14px",
                        minHeight: 52,
                        borderRadius: "16px 16px 0 0"
                      },
                      body: { padding: "12px 14px 14px" }
                    }}
                    onClick={() => selectSkill(skill)}
                    title={
                      <div className="flex min-w-0 items-center gap-2.5 pr-1">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eff6ff] text-[16px] text-[#2563eb] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
                          {ICON_MAP[skill.icon] ?? <ThunderboltOutlined />}
                        </span>
                        <Typography.Text
                          ellipsis={{ tooltip: skill.name }}
                          className="text-[15px] font-semibold leading-snug text-[#0f172a] dark:text-[#f1f5f9]"
                        >
                          {skill.name}
                        </Typography.Text>
                      </div>
                    }
                  >
                    <Typography.Paragraph
                      ellipsis={{ rows: 3 }}
                      className="!mb-0 text-[13px] leading-relaxed text-[#64748b] dark:text-[#94a3b8]"
                    >
                      {skill.desc}
                    </Typography.Paragraph>
                  </Card>
                );
              })}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <Button
            type="link"
            icon={<ArrowLeftOutlined />}
            onClick={() => {
              setSelected(null);
              setResult(null);
              setShowExpand(false);
            }}
            style={{ paddingLeft: 0, alignSelf: "flex-start", height: 28 }}
          >
            返回选择报告类型
          </Button>

          <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row lg:items-stretch">
            <Card
              title={selected.name}
              className="w-full shrink-0 overflow-hidden rounded-2xl lg:w-[320px] xl:w-[360px] lg:overflow-y-auto"
              styles={{ body: { paddingTop: 12 } }}
            >
              <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                {selected.desc}
              </Typography.Paragraph>
              <Form
                form={form}
                layout="vertical"
                onFinish={onGenerate}
                initialValues={{ audience: selected.audience_default, include_charts: true }}
                size="middle"
              >
                <Form.Item
                  name="datasource_id"
                  label="数据源"
                  rules={[{ required: true, message: "请选择数据源" }]}
                >
                  <Select
                    options={datasources.map((d) => ({
                      value: d.id,
                      label: d.name || `数据源 #${d.id}`
                    }))}
                    placeholder="选择数据源"
                    onChange={() => {
                      form.setFieldsValue({
                        school_name: undefined,
                        class_name: undefined,
                        exam_name: undefined,
                        subject: undefined
                      });
                    }}
                  />
                </Form.Item>
                <Form.Item name="audience" label="报告受众" rules={[{ required: true }]}>
                  <Select options={AUDIENCE_OPTIONS} />
                </Form.Item>
                {fieldFlags.school_name ? (
                  <Form.Item name="school_name" label="学校">
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.schools)}
                      placeholder="选择学校"
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.class_name ? (
                  <Form.Item
                    name="class_name"
                    label="班级"
                    rules={
                      selected.report_type === "class_overview"
                        ? [{ required: true, message: "请选择班级" }]
                        : undefined
                    }
                  >
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.classes)}
                      placeholder="选择班级"
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.exam_name ? (
                  <Form.Item
                    name="exam_name"
                    label="考试"
                    extra={
                      fieldFlags.exam_multi
                        ? "可多选；不选则包含全部考试（用于动态性对比）"
                        : undefined
                    }
                  >
                    <Select
                      mode={fieldFlags.exam_multi ? "multiple" : undefined}
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.exams)}
                      placeholder={
                        fieldFlags.exam_multi ? "选择一场或多场考试" : "选择考试"
                      }
                      optionFilterProp="label"
                      maxTagCount="responsive"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.subject ? (
                  <Form.Item
                    name="subject"
                    label="科目"
                    rules={
                      selected.report_type === "subject_diagnosis"
                        ? [{ required: true, message: "请选择科目" }]
                        : undefined
                    }
                  >
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.subjects)}
                      placeholder="选择科目"
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.student_name ? (
                  <Form.Item
                    name="student_name"
                    label="学生"
                    rules={[{ required: true, message: "请填写学生姓名或学号" }]}
                  >
                    <Input placeholder="姓名或学号" />
                  </Form.Item>
                ) : null}
                <Form.Item name="include_charts" valuePropName="checked">
                  <Checkbox>包含图表</Checkbox>
                </Form.Item>
                <Space direction="vertical" style={{ width: "100%" }} size="small">
                  <Button type="primary" htmlType="submit" loading={generating} block>
                    生成报告
                  </Button>
                  {fieldFlags.class_name ? (
                    <Button
                      block
                      onClick={() => {
                        setBatchClasses([]);
                        setBatchItems([]);
                        setBatchOpen(true);
                      }}
                    >
                      批量（多班级）
                    </Button>
                  ) : null}
                </Space>
              </Form>
            </Card>

            <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
              {result?.error ? <Alert type="error" message={result.error} showIcon /> : null}

              {result?.html && !result.error ? (
                <Card
                  title={reportTitle}
                  className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl [&_.ant-card-body]:flex [&_.ant-card-body]:min-h-0 [&_.ant-card-body]:flex-1 [&_.ant-card-body]:flex-col"
                  styles={{ body: { padding: 12 } }}
                  extra={
                    <Space wrap size="small">
                      <Button
                        icon={<HistoryOutlined />}
                        loading={savingHistory}
                        onClick={() => void onSaveHistory()}
                      >
                        保存到任务历史
                      </Button>
                      <Button icon={<DownloadOutlined />} onClick={exportHtml}>
                        下载 HTML
                      </Button>
                      <Button
                        icon={<FilePdfOutlined />}
                        loading={exportingPdf}
                        onClick={() => void exportReportPdf()}
                      >
                        PDF
                      </Button>
                      <Button
                        icon={<FileWordOutlined />}
                        loading={exportingWord}
                        onClick={() => void exportReportWord()}
                      >
                        Word
                      </Button>
                      <Button icon={<ExpandOutlined />} onClick={() => setShowExpand(true)}>
                        展开预览
                      </Button>
                    </Space>
                  }
                >
                  <div
                    className="min-h-0 flex-1 overflow-hidden"
                    style={{
                      borderRadius: 8,
                      border: "1px solid #dbe5f1",
                      background: "#fff"
                    }}
                  >
                    <iframe
                      ref={reportIframeRef}
                      title={reportTitle}
                      srcDoc={safeReportHtml}
                      sandbox="allow-scripts allow-same-origin"
                      referrerPolicy="no-referrer"
                      style={{ width: "100%", height: "100%", minHeight: 480, border: 0, display: "block" }}
                    />
                  </div>
                </Card>
              ) : (
                <Card
                  className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl [&_.ant-card-body]:flex [&_.ant-card-body]:min-h-0 [&_.ant-card-body]:flex-1"
                  styles={{ body: { padding: 12 } }}
                >
                  <div
                    className="flex min-h-[480px] flex-1 items-center justify-center text-center"
                    style={{
                      color: "rgba(0,0,0,0.45)",
                      border: "1px dashed #d9d9d9",
                      borderRadius: 8,
                      background: "#fafafa"
                    }}
                  >
                    <div>
                      <Typography.Text type="secondary">
                        {generating ? "正在生成报告…" : "填写左侧条件后点击「生成报告」"}
                      </Typography.Text>
                      <br />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        预览将显示在此处
                      </Typography.Text>
                    </div>
                  </div>
                </Card>
              )}
            </div>
          </div>

          <Modal
            title={reportTitle}
            open={showExpand}
            onCancel={() => setShowExpand(false)}
            footer={
              <Space wrap>
                <Button
                  icon={<HistoryOutlined />}
                  loading={savingHistory}
                  onClick={() => void onSaveHistory()}
                >
                  保存到任务历史
                </Button>
                <Button icon={<DownloadOutlined />} onClick={exportHtml}>
                  下载 HTML
                </Button>
                <Button
                  icon={<FilePdfOutlined />}
                  loading={exportingPdf}
                  onClick={() => void exportReportPdf()}
                >
                  PDF
                </Button>
                <Button
                  icon={<FileWordOutlined />}
                  loading={exportingWord}
                  onClick={() => void exportReportWord()}
                >
                  Word
                </Button>
                <Button onClick={() => setShowExpand(false)}>关闭</Button>
              </Space>
            }
            width="90vw"
            styles={{ body: { height: "75vh", padding: 0 } }}
            destroyOnClose={false}
          >
            {safeReportHtml ? (
              <iframe
                title={`${reportTitle}-expand`}
                srcDoc={safeReportHtml}
                sandbox="allow-scripts allow-same-origin"
                referrerPolicy="no-referrer"
                style={{ width: "100%", height: "75vh", border: 0 }}
              />
            ) : null}
          </Modal>

          <Modal
            title={`批量生成 · ${selected.name}`}
            open={batchOpen}
            onCancel={() => setBatchOpen(false)}
            okText="开始批量"
            confirmLoading={batchLoading}
            onOk={() => void onBatchGenerate()}
            width={720}
            destroyOnClose={false}
          >
            <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
              选择多个班级，将按当前表单中的考试/科目/受众等条件逐班生成（不含 HTML
              预览，仅返回摘要）。
            </Typography.Paragraph>
            <Select
              mode="multiple"
              allowClear
              showSearch
              style={{ width: "100%" }}
              placeholder="选择班级"
              loading={metaLoading}
              options={toSelectOptions(meta.classes)}
              value={batchClasses}
              onChange={(vals) => setBatchClasses(vals)}
              optionFilterProp="label"
            />
            {batchItems.length > 0 ? (
              <Table
                style={{ marginTop: 16 }}
                size="small"
                pagination={false}
                rowKey={(r) => `${r.class_name}-${r.report_type}`}
                dataSource={batchItems}
                columns={[
                  { title: "班级", dataIndex: "class_name", key: "class_name" },
                  { title: "标题", dataIndex: "title", key: "title", ellipsis: true },
                  {
                    title: "结果",
                    key: "status",
                    render: (_: unknown, row: BatchReportItem) =>
                      row.error ? (
                        <Typography.Text type="danger">{row.error}</Typography.Text>
                      ) : (
                        <Typography.Text type="success">成功（{row.html_length} 字）</Typography.Text>
                      )
                  }
                ]}
              />
            ) : null}
          </Modal>
        </div>
      )}
    </div>
  );
}
