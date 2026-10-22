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
import { getCurrentUser } from "@/api/auth";
import { datasourceApi, type DatasourceItem } from "@/api/datasource";
import {
  educationApi,
  type BatchReportItem,
  type GenerateReportResult,
  type MetaOptions
} from "@/api/education";
import { exportReportAsWord, sanitizeFileName } from "@/utils/exportReportWord";
import {
  bindIframeWatermark,
  captureElementWithWatermark,
  stampHtmlWatermark
} from "@/utils/userWatermark";

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
  "elite_roster",
  "score_band",
  "line_shift",
  "absence",
  "class_line_reach",
  "elite_rank_shift"
]);

/** 校长/老师可见：本校/本班口径，不含全市局端报告 */
const SCHOOL_TEACHER_REPORT_TYPES = new Set([
  "class_overview",
  "grade_comparison",
  "subject_diagnosis",
  "student_profile",
  "trend_tracking",
  "tier_alert",
  "comprehensive",
  "line_shift",
  "absence",
  "class_line_reach",
  "elite_rank_shift"
]);

function isSchoolOrTeacherRole(eduRole: string): boolean {
  return eduRole === "school_admin" || eduRole === "teacher";
}

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
  /** 班级达线率与贡献分对照 / 尖子生排名变化报告的对比考试：不选自动上一场，可选「不对比」 */
  compare_exam?: string;
  /** 尖子生排名变化报告的名次范围：10/30/50/100，默认 30 */
  top_n?: number;
  subject?: string;
  school_name?: string;
  student_name?: string;
  /** 届次，如 2026届 */
  jc?: string;
  include_charts: boolean;
};

function fieldsForType(reportType: string): {
  class_name?: boolean;
  exam_name?: boolean;
  /** 考试可多选（区域结构化诊断等） */
  exam_multi?: boolean;
  /** 对比考试（班级达线率与贡献分对照 / 尖子生排名变化报告） */
  compare_exam?: boolean;
  /** 名次范围（尖子生排名变化报告） */
  top_n?: boolean;
  subject?: boolean;
  school_name?: boolean;
  student_name?: boolean;
  jc?: boolean;
} {
  let flags: {
    class_name?: boolean;
    exam_name?: boolean;
    exam_multi?: boolean;
    compare_exam?: boolean;
    top_n?: boolean;
    subject?: boolean;
    school_name?: boolean;
    student_name?: boolean;
    jc?: boolean;
  };
  switch (reportType) {
    case "class_overview":
      flags = { school_name: true, class_name: true, exam_name: true, subject: true };
      break;
    case "grade_comparison":
      flags = { school_name: true, exam_name: true, subject: true };
      break;
    case "subject_diagnosis":
      flags = { school_name: true, class_name: true, exam_name: true, subject: true };
      break;
    case "student_profile":
      flags = {
        school_name: true,
        student_name: true,
        exam_name: true,
        subject: true,
        class_name: true
      };
      break;
    case "trend_tracking":
      flags = { class_name: true, subject: true, school_name: true };
      break;
    case "tier_alert":
      flags = { class_name: true, exam_name: true, subject: true, school_name: true };
      break;
    case "group_feature":
      flags = { school_name: true, exam_name: true, subject: true };
      break;
    case "comprehensive":
      flags = {
        class_name: true,
        subject: true,
        school_name: true,
        exam_name: true,
        exam_multi: true
      };
      break;
    case "diagnostic_report":
      flags = { school_name: true, exam_name: true, exam_multi: true, subject: true };
      break;
    case "line_reach":
      flags = { exam_name: true };
      break;
    case "subject_avg":
    case "assign_grade":
    case "rank_bucket":
    case "contribution":
    case "combo_reach":
    case "elite_roster":
    case "score_band":
      flags = { exam_name: true };
      break;
    case "line_shift":
      flags = { school_name: true, class_name: true, exam_name: true, exam_multi: true };
      break;
    case "absence":
      flags = { school_name: true, class_name: true, exam_name: true };
      break;
    case "class_line_reach":
      flags = { school_name: true, class_name: true, exam_name: true, compare_exam: true };
      break;
    case "elite_rank_shift":
      flags = {
        school_name: true,
        class_name: true,
        exam_name: true,
        compare_exam: true,
        top_n: true
      };
      break;
    default:
      flags = { class_name: true, exam_name: true };
  }
  if (flags.exam_name || flags.school_name) {
    flags.jc = true;
  }
  return flags;
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
  const [eduRole, setEduRole] = useState<string | null>(null);
  const [boundSchoolName, setBoundSchoolName] = useState("");
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
    subjects: [],
    cohorts: []
  });
  const reportIframeRef = useRef<HTMLIFrameElement | null>(null);
  const deepLinkApplied = useRef(false);
  const [form] = Form.useForm<FormValues>();

  const datasourceId = Form.useWatch("datasource_id", form);
  const schoolName = Form.useWatch("school_name", form);
  const examName = Form.useWatch("exam_name", form);
  const className = Form.useWatch("class_name", form);
  const subjectName = Form.useWatch("subject", form);
  const jc = Form.useWatch("jc", form);

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
    void getCurrentUser()
      .then((user) => {
        if (!cancelled) {
          setEduRole(String(user.edu_scope?.edu_role || "").trim());
          setBoundSchoolName(String(user.edu_scope?.school_name || "").trim());
        }
      })
      .catch(() => {
        if (!cancelled) setEduRole("");
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
      setMeta({ schools: [], exams: [], classes: [], subjects: [], cohorts: [] });
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
        subject: subjectName || undefined,
        jc: jc || undefined
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
  }, [datasourceId, schoolName, examName, className, subjectName, jc]);

  const toSelectOptions = (items: string[]) => items.map((v) => ({ value: v, label: v }));

  const cohortOptions = useMemo(() => {
    if (meta.cohorts.length) return meta.cohorts;
    const seen = new Set<string>();
    const out: string[] = [];
    for (const exam of meta.exams) {
      const matched = String(exam).match(/(\d{4})\s*届/);
      if (!matched) continue;
      const label = `${matched[1]}届`;
      if (seen.has(label)) continue;
      seen.add(label);
      out.push(label);
    }
    return out.sort((a, b) => b.localeCompare(a));
  }, [meta.cohorts, meta.exams]);

  const fieldFlags = useMemo(
    () => (selected ? fieldsForType(selected.report_type) : {}),
    [selected]
  );

  const visibleSkills = useMemo(() => {
    if (eduRole != null && isSchoolOrTeacherRole(eduRole)) {
      return skills.filter((s) => SCHOOL_TEACHER_REPORT_TYPES.has(s.report_type));
    }
    return skills;
  }, [skills, eduRole]);

  const cardsLoading = skillsLoading || eduRole == null;

  // 校长/老师账号绑定了学校：学校不用选，自动带入本校（后端也会按账号限本校，
  // 这里带入是为了让班级/学生下拉一打开就是本校范围，少一步操作）。
  const autoSchool =
    eduRole != null && isSchoolOrTeacherRole(eduRole) && boundSchoolName
      ? boundSchoolName
      : "";

  useEffect(() => {
    if (autoSchool) {
      form.setFieldsValue({ school_name: autoSchool });
    }
  }, [autoSchool, form]);

  const selectSkill = (skill: SkillItem) => {
    if (!OPEN_REPORT_TYPES.has(skill.report_type)) {
      message.info("该报告类型暂未开放");
      return;
    }
    if (
      eduRole != null &&
      isSchoolOrTeacherRole(eduRole) &&
      !SCHOOL_TEACHER_REPORT_TYPES.has(skill.report_type)
    ) {
      message.info("当前角色不提供该报告");
      return;
    }
    setSelected(skill);
    setResult(null);
    setBatchItems([]);
    form.setFieldsValue({
      // 学生个体画像的技能配置默认家长版，现阶段不用家长版：
      // 分析工具里固定出教师版（详细）。
      audience:
        skill.report_type === "student_profile"
          ? "default"
          : skill.audience_default || "default",
      include_charts: true,
      class_name: undefined,
      exam_name: undefined,
      subject: undefined,
      school_name: autoSchool || undefined,
      student_name: undefined,
      jc: undefined
    });
  };

  // 技能页「一键生成」：?report_type=xxx 自动进入对应表单
  useEffect(() => {
    if (deepLinkApplied.current || cardsLoading || visibleSkills.length === 0) return;
    const raw = router.query.report_type;
    const rt = typeof raw === "string" ? raw.trim() : Array.isArray(raw) ? raw[0] : "";
    if (!rt) return;
    const skill = visibleSkills.find((s) => s.report_type === rt);
    deepLinkApplied.current = true;
    if (!skill) return;
    selectSkill(skill);
  }, [visibleSkills, cardsLoading, router.query.report_type]);

  const onGenerate = async (values: FormValues) => {
    if (!selected) return;
    setGenerating(true);
    setResult(null);
    try {
      const filters: Record<string, string> = {};
      if (values.class_name?.trim()) filters.class_name = values.class_name.trim();
      const examFilter = serializeExamFilter(values.exam_name);
      if (examFilter) filters.exam_name = examFilter;
      if (values.compare_exam?.trim()) filters.compare_exam = values.compare_exam.trim();
      if (values.top_n) filters.top_n = String(values.top_n);
      if (values.subject?.trim()) filters.subject = values.subject.trim();
      if (values.school_name?.trim()) filters.school_name = values.school_name.trim();
      if (values.student_name?.trim()) filters.student_name = values.student_name.trim();
      if (values.jc?.trim()) filters.jc = values.jc.trim();

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
    if (values.compare_exam?.trim()) filters.compare_exam = values.compare_exam.trim();
    if (values.top_n) filters.top_n = String(values.top_n);
    if (values.subject?.trim()) filters.subject = values.subject.trim();
    if (values.school_name?.trim()) filters.school_name = values.school_name.trim();
    if (values.student_name?.trim()) filters.student_name = values.student_name.trim();
    if (values.jc?.trim()) filters.jc = values.jc.trim();
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
    const blob = new Blob([stampHtmlWatermark(safeReportHtml)], { type: "text/html;charset=utf-8" });
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
      const canvas = await captureElementWithWatermark(doc.body, html2canvas, {
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
          {cardsLoading
            ? null
            : visibleSkills.map((skill) => {
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
                initialValues={{
                  audience:
                    selected.report_type === "student_profile"
                      ? "default"
                      : selected.audience_default,
                  include_charts: true
                }}
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
                        school_name: autoSchool || undefined,
                        class_name: undefined,
                        exam_name: undefined,
                        subject: undefined,
                        jc: undefined
                      });
                    }}
                  />
                </Form.Item>
                <Form.Item name="audience" hidden>
                  <Input />
                </Form.Item>
                {fieldFlags.school_name && autoSchool ? (
                  <>
                    <Form.Item
                      label="学校"
                      extra="已按账号绑定学校自动统计，无需选择"
                    >
                      <Input value={autoSchool} disabled />
                    </Form.Item>
                    <Form.Item name="school_name" hidden>
                      <Input />
                    </Form.Item>
                  </>
                ) : fieldFlags.school_name ? (
                  <Form.Item
                    name="school_name"
                    label="学校"
                    extra={
                      selected.report_type === "elite_rank_shift"
                        ? "教育局可不选（看全市前 N）；选一所学校则下钻该校。校长自动统计本校。"
                        : (selected.report_type === "class_overview" ||
                              selected.report_type === "student_profile") &&
                            eduRole !== "school_admin" &&
                            eduRole !== "teacher"
                          ? "必选。先选学校再选班级，避免同名班级、同名学生串校。校长/老师自动按本校统计。"
                          : undefined
                    }
                    rules={
                      (selected.report_type === "line_shift" ||
                        selected.report_type === "absence" ||
                        selected.report_type === "class_line_reach" ||
                        selected.report_type === "class_overview" ||
                        selected.report_type === "student_profile") &&
                      eduRole !== "school_admin" &&
                      eduRole !== "teacher"
                        ? [{ required: true, message: "请选择学校" }]
                        : undefined
                    }
                  >
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.schools)}
                      placeholder={
                        selected.report_type === "elite_rank_shift"
                          ? "不选则全市；下钻可选学校"
                          : "选择学校"
                      }
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.jc ? (
                  <Form.Item name="jc" label="届次">
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(cohortOptions)}
                      placeholder="选择届次"
                      optionFilterProp="label"
                      onChange={(val) => {
                        const exam = form.getFieldValue("exam_name") as
                          | string
                          | string[]
                          | undefined;
                        if (Array.isArray(exam)) {
                          form.setFieldsValue({
                            exam_name: val
                              ? exam.filter((item) => String(item).includes(String(val)))
                              : exam
                          });
                        } else if (exam && val && !String(exam).includes(String(val))) {
                          form.setFieldsValue({ exam_name: undefined });
                        }
                      }}
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
                        : (selected.report_type === "line_shift" ||
                            selected.report_type === "absence" ||
                            selected.report_type === "class_line_reach" ||
                            selected.report_type === "elite_rank_shift") &&
                            eduRole === "teacher"
                          ? [{ required: true, message: "请选择班级" }]
                          : undefined
                    }
                  >
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.classes)}
                      placeholder={
                        selected.report_type === "line_shift" ||
                        selected.report_type === "absence" ||
                        selected.report_type === "class_line_reach" ||
                        selected.report_type === "elite_rank_shift"
                          ? "不选则统计全校；老师必须选择班级"
                          : "选择班级"
                      }
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.exam_name ? (
                  <Form.Item
                    name="exam_name"
                    label="考试"
                    extra={
                      selected.report_type === "line_shift"
                        ? "必须选择两场。有考试时间时，较早的一场是上次。"
                        : selected.report_type === "absence"
                          ? "选一场考试。上一场取考试时间更早、最近的一场，用于识别整场缺考。"
                          : selected.report_type === "class_line_reach"
                            ? "选一场考试。达线率按各自的线分计算，跨场可比。"
                            : selected.report_type === "elite_rank_shift"
                              ? "选一场考试。排名是全市在籍生排名，历场同口径可比。"
                              : selected.report_type === "class_overview"
                                ? "必须选择一场考试。本报告只分析单次考试的班级成绩。"
                                : selected.report_type === "subject_diagnosis"
                                  ? "必须选择一场考试。逐题与知识点诊断只针对单次考试，避免混场。"
                                  : fieldFlags.exam_multi
                                    ? "可多选；不选则包含全部考试（用于动态性对比）"
                                    : undefined
                    }
                    rules={
                      selected.report_type === "line_shift"
                        ? [
                            {
                              validator: (_, value) => {
                                const picked = Array.isArray(value)
                                  ? value.map((item) => String(item || "").trim()).filter(Boolean)
                                  : String(value || "").trim()
                                    ? [String(value).trim()]
                                    : [];
                                if (picked.length !== 2 || picked[0] === picked[1]) {
                                  return Promise.reject(new Error("请选择两场不同的考试"));
                                }
                                return Promise.resolve();
                              }
                            }
                          ]
                        : selected.report_type === "class_line_reach" ||
                            selected.report_type === "elite_rank_shift" ||
                            selected.report_type === "class_overview" ||
                            selected.report_type === "subject_diagnosis"
                          ? [{ required: true, message: "请选择考试" }]
                          : undefined
                    }
                  >
                    <Select
                      mode={fieldFlags.exam_multi ? "multiple" : undefined}
                      maxCount={selected.report_type === "line_shift" ? 2 : undefined}
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={toSelectOptions(meta.exams)}
                      placeholder={
                        selected.report_type === "line_shift"
                          ? "选择两场考试"
                          : fieldFlags.exam_multi
                            ? "选择一场或多场考试"
                            : "选择考试"
                      }
                      optionFilterProp="label"
                      maxTagCount="responsive"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.compare_exam ? (
                  <Form.Item
                    name="compare_exam"
                    label="对比考试"
                    extra="不选则自动对比上一场（考试时间早于本次、最近的一场）。只想看本次考试请选「不对比」。"
                  >
                    <Select
                      allowClear
                      showSearch
                      loading={metaLoading}
                      options={[
                        { value: "不对比", label: "不对比（只看本次考试）" },
                        ...toSelectOptions(meta.exams)
                      ]}
                      placeholder="自动对比上一场"
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.top_n ? (
                  <Form.Item
                    name="top_n"
                    label="名次范围"
                    initialValue={30}
                    extra="教育局=全市前 N；校长/教育局选校=本校前 N（按全市排名取前 N，含并列）；老师=本班前 N。"
                  >
                    <Select
                      options={[
                        { value: 100, label: "前 100 名" },
                        { value: 50, label: "前 50 名" },
                        { value: 30, label: "前 30 名" },
                        { value: 10, label: "前 10 名" }
                      ]}
                      placeholder="前 30 名"
                    />
                  </Form.Item>
                ) : null}
                {fieldFlags.subject ? (
                  <Form.Item
                    name="subject"
                    label="科目"
                    extra={
                      selected.report_type === "grade_comparison"
                        ? "不选则按各科得分合计（每名学生一份总分）对比各班；选了科目则比该科单科。"
                        : undefined
                    }
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
                      placeholder={
                        selected.report_type === "grade_comparison"
                          ? "不选=各科合计；选了=单科对比"
                          : "选择科目"
                      }
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
                      onLoad={(e) => bindIframeWatermark(e.currentTarget)}
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
                onLoad={(e) => bindIframeWatermark(e.currentTarget)}
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
              选择多个班级，将按当前表单中的考试/科目等条件逐班生成（不含 HTML
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
