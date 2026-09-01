import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";

const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

export type ScoreImportType = "total" | "detail";

export interface ScoreImportErrorRow {
  row: number;
  field: string;
  message: string;
}

export interface ScoreImportResult {
  total_rows: number;
  valid_rows: number;
  error_rows: ScoreImportErrorRow[];
  summary: {
    inserted: number;
    updated: number;
    score_upserted: number;
    students_to_create?: number;
    students_created?: number;
  };
  preview_sample: Array<Record<string, unknown>>;
  anomaly_alerts_pending?: boolean;
  anomaly_alerts?: {
    inserted?: number;
    updated?: number;
    skipped?: number;
    detected?: number;
    exams?: number;
  };
}

export interface ScoreImportResponse {
  ok: boolean;
  message: string;
  data: ScoreImportResult | null;
}

export interface RawImportWarning {
  row: number;
  message: string;
}

export interface RawPaperOption {
  exam_id: number;
  subject: string;
  exam_score?: number | null;
}

export interface RawPapersResponse {
  papers: RawPaperOption[];
  missing_subjects: string[];
  duplicate_subjects: string[];
}

export interface RawImportResult {
  total_rows: number;
  valid_rows: number;
  error_rows: ScoreImportErrorRow[];
  warnings?: RawImportWarning[];
  summary: Record<string, unknown>;
  preview_sample: Array<Record<string, unknown>>;
}

export interface RawImportResponse {
  ok: boolean;
  message: string;
  data: RawImportResult | null;
}

const RAW_IMPORT_TIMEOUT_MS = 300_000;

function rawImportTimeoutSignal(): AbortSignal {
  const timeout = AbortSignal as typeof AbortSignal & {
    timeout?: (ms: number) => AbortSignal;
  };
  if (typeof timeout.timeout === "function") {
    return timeout.timeout(RAW_IMPORT_TIMEOUT_MS);
  }
  const controller = new AbortController();
  setTimeout(() => controller.abort(), RAW_IMPORT_TIMEOUT_MS);
  return controller.signal;
}

export interface GenerateReportRequest {
  datasource_id: number;
  report_type: string;
  audience?: string;
  filters?: Record<string, string>;
  include_charts?: boolean;
}

export interface GenerateReportResult {
  title: string;
  html: string;
  report_type: string;
  report_type_label: string;
  error: string | null;
}

export interface GenerateReportResponse {
  ok: boolean;
  message: string;
  data: GenerateReportResult | null;
}

export interface MetaOptions {
  schools: string[];
  exams: string[];
  classes: string[];
  subjects: string[];
}

export interface MetaOptionsQuery {
  datasource_id: number;
  school_name?: string;
  exam_name?: string;
  class_name?: string;
  subject?: string;
}

export interface BatchReportRequest {
  datasource_id: number;
  class_names: string[];
  report_type?: string;
  question?: string;
  filters?: Record<string, string>;
  audience?: string;
  include_charts?: boolean;
}

export interface BatchReportItem {
  class_name: string;
  template_name?: string;
  html_length: number;
  report_type: string;
  title?: string;
  error: string | null;
}

export interface BatchReportResponse {
  ok: boolean;
  message: string;
  data: { items: BatchReportItem[] } | null;
}

export interface SaveReportHistoryRequest {
  datasource_id: number;
  title: string;
  html: string;
  report_type?: string;
  report_type_label?: string;
  question?: string;
}

export interface SaveReportHistoryResult {
  conversation_id: number;
  record_id: number;
  title: string;
}

export interface SaveReportHistoryResponse {
  ok: boolean;
  message: string;
  data: SaveReportHistoryResult | null;
}

export interface ReportHistoryItem {
  conversation_id: number;
  record_id: number;
  title: string;
  conversation_title: string;
  report_type: string;
  report_type_label: string;
  datasource_id: number | null;
  datasource_name: string;
  question: string;
  summary: string;
  create_time: string | null;
  html_length: number;
  html?: string;
}

export interface ReportHistoryListResponse {
  ok: boolean;
  message: string;
  data: { total: number; items: ReportHistoryItem[] } | null;
}

export interface ReportHistoryDetailResponse {
  ok: boolean;
  message: string;
  data: ReportHistoryItem | null;
}

async function authHeaders(): Promise<HeadersInit> {
  const token = getAccessToken();
  const wsOid =
    typeof window !== "undefined" ? window.localStorage.getItem(WORKSPACE_OID_STORAGE_KEY) : null;
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(wsOid ? { "X-Workspace-Oid": wsOid.trim() } : {})
  };
}

export const educationApi = {
  async downloadTemplate(importType: ScoreImportType): Promise<void> {
    const resp = await fetch(`${getApiBaseUrl()}/education/score-import/templates/${importType}`, {
      headers: await authHeaders()
    });
    if (!resp.ok) {
      throw new Error("下载模板失败");
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = importType === "total" ? "脱敏成绩_仅总分.xlsx" : "脱敏成绩_小题分明细.xlsx";
    a.click();
    URL.revokeObjectURL(url);
  },

  async postScoreImport(
    endpoint: "preview" | "execute",
    formData: FormData
  ): Promise<ScoreImportResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/score-import/${endpoint}`, {
      method: "POST",
      headers: await authHeaders(),
      body: formData
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const payload = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: ScoreImportResult;
    };
    return {
      ok: (payload.code ?? 200) === 200,
      message: payload.message || "",
      data: payload.data ?? null
    };
  },

  async generateReport(payload: GenerateReportRequest): Promise<GenerateReportResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/generate-report`, {
      method: "POST",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: GenerateReportResult;
    };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || "",
      data: body.data ?? null
    };
  },

  async listMetaOptions(query: MetaOptionsQuery): Promise<MetaOptions> {
    const params = new URLSearchParams();
    params.set("datasource_id", String(query.datasource_id));
    if (query.school_name) params.set("school_name", query.school_name);
    if (query.exam_name) params.set("exam_name", query.exam_name);
    if (query.class_name) params.set("class_name", query.class_name);
    if (query.subject) params.set("subject", query.subject);
    const resp = await fetch(`${getApiBaseUrl()}/education/meta/options?${params.toString()}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: MetaOptions;
    };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载筛选项失败");
    }
    return {
      schools: body.data.schools || [],
      exams: body.data.exams || [],
      classes: body.data.classes || [],
      subjects: body.data.subjects || []
    };
  },

  async batchReport(payload: BatchReportRequest): Promise<BatchReportResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/batch-report`, {
      method: "POST",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: { items: BatchReportItem[] };
    };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || "",
      data: body.data ?? null
    };
  },

  async saveReportHistory(payload: SaveReportHistoryRequest): Promise<SaveReportHistoryResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/save-report-history`, {
      method: "POST",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: SaveReportHistoryResult;
    };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || "",
      data: body.data ?? null
    };
  },

  async listReportHistory(limit = 50): Promise<ReportHistoryListResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/report-history?limit=${limit}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: { total: number; items: ReportHistoryItem[] };
    };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || "",
      data: body.data ?? null
    };
  },

  async getReportHistoryDetail(recordId: number): Promise<ReportHistoryDetailResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/report-history/${recordId}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: ReportHistoryItem;
    };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || "",
      data: body.data ?? null
    };
  },

  async deleteReportHistory(conversationId: number): Promise<{ ok: boolean; message: string }> {
    const resp = await fetch(`${getApiBaseUrl()}/education/report-history/${conversationId}`, {
      method: "DELETE",
      headers: await authHeaders()
    });
    if (resp.status === 401) {
      throw new Error("Unauthorized");
    }
    const body = (await resp.json()) as { code?: number; message?: string };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || ""
    };
  },

  async getReportConfig(): Promise<ReportConfig> {
    const resp = await fetch(`${getApiBaseUrl()}/education/report-config`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as { code?: number; message?: string; data?: ReportConfig };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载配置失败");
    }
    return body.data;
  },

  async updateReportConfig(payload: Partial<ReportConfigUpdate>): Promise<ReportConfig> {
    const resp = await fetch(`${getApiBaseUrl()}/education/report-config`, {
      method: "PUT",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as { code?: number; message?: string; data?: ReportConfig };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "保存配置失败");
    }
    return body.data;
  },

  async resetReportConfig(): Promise<ReportConfig> {
    const resp = await fetch(`${getApiBaseUrl()}/education/report-config/reset`, {
      method: "POST",
      headers: await authHeaders()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as { code?: number; message?: string; data?: ReportConfig };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "重置配置失败");
    }
    return body.data;
  },

  async listAnomalyAlerts(query?: {
    status?: "pending" | "confirmed" | "";
    limit?: number;
    offset?: number;
  }): Promise<AnomalyAlertListResult> {
    const params = new URLSearchParams();
    if (query?.status) params.set("status", query.status);
    if (query?.limit != null) params.set("limit", String(query.limit));
    if (query?.offset != null) params.set("offset", String(query.offset));
    const qs = params.toString();
    const resp = await fetch(
      `${getApiBaseUrl()}/education/anomaly-alerts${qs ? `?${qs}` : ""}`,
      { headers: await authHeaders() }
    );
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: AnomalyAlertListResult;
    };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载异常提醒失败");
    }
    return body.data;
  },

  async getAnomalyAlert(alertId: number): Promise<AnomalyAlertItem> {
    const resp = await fetch(`${getApiBaseUrl()}/education/anomaly-alerts/${alertId}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: AnomalyAlertItem;
    };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载详情失败");
    }
    return body.data;
  },

  async confirmAnomalyAlert(alertId: number, note?: string): Promise<AnomalyAlertItem> {
    const resp = await fetch(`${getApiBaseUrl()}/education/anomaly-alerts/${alertId}/confirm`, {
      method: "POST",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ note: note || "" })
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: AnomalyAlertItem;
    };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "确认失败");
    }
    return body.data;
  },

  async getLineReachMeta(datasourceId: number): Promise<LineReachMeta> {
    const params = new URLSearchParams();
    params.set("datasource_id", String(datasourceId));
    const resp = await fetch(`${getApiBaseUrl()}/education/dashboards/line-reach/meta?${params}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as { code?: number; message?: string; data?: LineReachMeta };
    if ((body.code ?? 200) === 403) {
      throw new Error(body.message || "无权查看达线看板");
    }
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载筛选项失败");
    }
    return body.data;
  },

  async getLineReach(query: LineReachQuery): Promise<LineReachResult> {
    const params = new URLSearchParams();
    params.set("datasource_id", String(query.datasource_id));
    if (query.exam_name) params.set("exam_name", query.exam_name);
    if (query.track) params.set("track", query.track);
    const resp = await fetch(`${getApiBaseUrl()}/education/dashboards/line-reach?${params}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as { code?: number; message?: string; data?: LineReachResult };
    if ((body.code ?? 200) === 403) {
      throw new Error(body.message || "无权查看达线看板");
    }
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载达线看板失败");
    }
    return body.data;
  },

  async listFractionBar(datasourceId: number): Promise<FractionBarListResult> {
    const params = new URLSearchParams();
    params.set("datasource_id", String(datasourceId));
    const resp = await fetch(`${getApiBaseUrl()}/education/fraction-bar?${params}`, {
      headers: await authHeaders()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: FractionBarListResult;
    };
    if ((body.code ?? 200) === 403) {
      throw new Error(body.message || "无权维护预测分数线");
    }
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载分数线失败");
    }
    return body.data;
  },

  async upsertFractionBar(payload: FractionBarUpsertPayload): Promise<{
    exam_name: string;
    indicator_rows: number;
    empty_scores?: boolean;
    message?: string;
  }> {
    const resp = await fetch(`${getApiBaseUrl()}/education/fraction-bar`, {
      method: "PUT",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: { exam_name: string; indicator_rows: number; empty_scores?: boolean };
    };
    if ((body.code ?? 200) === 403) {
      throw new Error(body.message || "无权维护预测分数线");
    }
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "保存分数线失败");
    }
    return { ...body.data, message: body.message };
  },

  async recomputeScoreIndicator(
    datasourceId: number,
    examName?: string
  ): Promise<{ indicator_rows: number; skipped?: string[] }> {
    const resp = await fetch(`${getApiBaseUrl()}/education/score-indicator/recompute`, {
      method: "POST",
      headers: {
        ...(await authHeaders()),
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        datasource_id: datasourceId,
        exam_name: examName || undefined
      })
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: { indicator_rows: number; skipped?: string[] };
    };
    if ((body.code ?? 200) === 403) {
      throw new Error(body.message || "无权重算达线指标");
    }
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "重算失败");
    }
    return body.data;
  },

  async listRawImportBatches(): Promise<ExamBatchOption[]> {
    const resp = await fetch(`${getApiBaseUrl()}/education/raw-score-import/batches`, {
      headers: await authHeaders(),
      signal: rawImportTimeoutSignal()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: { batches?: ExamBatchOption[] };
    };
    if ((body.code ?? 200) !== 200) {
      throw new Error(body.message || "加载批次失败");
    }
    return body.data?.batches ?? [];
  },

  async createRawImportBatch(
    batchName: string,
    examTime: string
  ): Promise<{ ok: boolean; message: string; data: ExamBatchOption | null }> {
    const fd = new FormData();
    fd.append("batch_name", batchName);
    fd.append("exam_time", examTime);
    const resp = await fetch(`${getApiBaseUrl()}/education/raw-score-import/batches`, {
      method: "POST",
      headers: await authHeaders(),
      body: fd,
      signal: rawImportTimeoutSignal()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: ExamBatchOption;
    };
    return {
      ok: (body.code ?? 200) === 200,
      message: body.message || "",
      data: body.data ?? null
    };
  },

  async listRawImportPapers(examBatchId: number): Promise<RawPapersResponse> {
    const params = new URLSearchParams({ exam_batch_id: String(examBatchId) });
    const resp = await fetch(
      `${getApiBaseUrl()}/education/raw-score-import/papers?${params.toString()}`,
      {
        headers: await authHeaders(),
        signal: rawImportTimeoutSignal()
      }
    );
    if (resp.status === 401) throw new Error("Unauthorized");
    const body = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: RawPapersResponse;
    };
    if ((body.code ?? 200) !== 200 || !body.data) {
      throw new Error(body.message || "加载试卷失败");
    }
    return {
      papers: body.data.papers ?? [],
      missing_subjects: body.data.missing_subjects ?? [],
      duplicate_subjects: body.data.duplicate_subjects ?? []
    };
  },

  async postRawOverviewImport(
    endpoint: "overview-preview" | "overview-execute",
    formData: FormData
  ): Promise<RawImportResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/raw-score-import/${endpoint}`, {
      method: "POST",
      headers: await authHeaders(),
      body: formData,
      signal: rawImportTimeoutSignal()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const payload = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: RawImportResult;
    };
    return {
      ok: (payload.code ?? 200) === 200,
      message: payload.message || "",
      data: payload.data ?? null
    };
  },

  async postRawDetailImport(
    endpoint: "detail-preview" | "detail-execute",
    formData: FormData
  ): Promise<RawImportResponse> {
    const resp = await fetch(`${getApiBaseUrl()}/education/raw-score-import/${endpoint}`, {
      method: "POST",
      headers: await authHeaders(),
      body: formData,
      signal: rawImportTimeoutSignal()
    });
    if (resp.status === 401) throw new Error("Unauthorized");
    const payload = (await resp.json()) as {
      code?: number;
      message?: string;
      data?: RawImportResult;
    };
    return {
      ok: (payload.code ?? 200) === 200,
      message: payload.message || "",
      data: payload.data ?? null
    };
  }
};

export interface AnomalyAlertItem {
  id: number;
  workspace_oid: number;
  datasource_id: number;
  school_id: string;
  class_name: string;
  student_id: string;
  exam_id: string;
  exam_name: string;
  subject_name: string;
  anomaly_type: string;
  anomaly_type_label: string;
  title: string;
  reason: string;
  payload: Record<string, unknown>;
  counts?: {
    critical: number;
    regression: number;
    imbalanced: number;
  };
  source: string;
  status: "pending" | "confirmed" | string;
  confirmed_by?: number | null;
  confirmed_at?: string | null;
  confirm_note?: string;
  create_time?: string | null;
  update_time?: string | null;
}

export interface AnomalyAlertListResult {
  accessible: boolean;
  total: number;
  items: AnomalyAlertItem[];
  message?: string;
}

export interface AnomalyRuleItem {
  id: string;
  anomaly_type: string;
  enabled: boolean;
  threshold?: number | null;
  compare_target: string;
  consecutive_n: number;
  fluctuation_mode: string;
  fluctuation_value?: number | null;
  range_lo?: number | null;
  range_hi?: number | null;
  range_lo_offset?: number | null;
  range_hi_offset?: number | null;
}

export interface ReportConfig {
  pass_ratio: number;
  excellent_ratio: number;
  pass_percent: number;
  excellent_percent: number;
  pass_threshold: number;
  excellent_threshold: number;
  default_full_score: number;
  critical_margin: number;
  regression_threshold: number;
  imbalance_score_gap: number;
  anomaly_rules: AnomalyRuleItem[];
}

export type ReportConfigUpdate = {
  pass_ratio?: number;
  excellent_ratio?: number;
  pass_threshold?: number;
  excellent_threshold?: number;
  default_full_score?: number;
  critical_margin?: number;
  regression_threshold?: number;
  imbalance_score_gap?: number;
  anomaly_rules?: AnomalyRuleItem[];
};

export interface FractionBarLine {
  track: string;
  line_code: string;
  line_name: string;
  threshold: number | null;
  column?: string;
}

export interface FractionBarLineCatalog {
  line_code: string;
  line_name: string;
  wl_column?: string | null;
  ls_column?: string | null;
}

export interface FractionBarExam {
  exam_name: string;
  exam_batch_id?: number | null;
  lines: FractionBarLine[];
}

export interface ExamBatchOption {
  id: number;
  batch_name: string;
  exam_time?: string | null;
}

export interface FractionBarListResult {
  columns: string[];
  line_catalog: FractionBarLineCatalog[];
  exams: FractionBarExam[];
  batches: ExamBatchOption[];
}

export interface FractionBarUpsertPayload {
  datasource_id: number;
  exam_batch_id?: number | null;
  exam_name?: string;
  lines: Array<{ track: string; line_code: string; threshold: number | null }>;
}

export interface LineReachLineMeta {
  line_name: string;
  threshold: number;
  label?: string;
  track?: string;
  line_key?: string;
  threshold_note?: string;
}

export interface LineReachLineStat {
  line_name: string;
  threshold: number;
  reached: number;
  rate: number;
  label?: string;
  track?: string;
  line_key?: string;
  threshold_note?: string;
}

export interface LineReachSchoolRow {
  school_id: string;
  school_name: string;
  candidates: number;
  by_line: LineReachLineStat[];
}

export interface LineReachDistrictRow {
  district: string;
  candidates: number;
  by_line: LineReachLineStat[];
  schools: LineReachSchoolRow[];
}

export interface LineReachMeta {
  accessible: boolean;
  exams: string[];
  tracks: string[];
  lines: LineReachLineMeta[];
}

export interface LineReachQuery {
  datasource_id: number;
  exam_name?: string;
  track?: string;
}

export interface LineReachView {
  lines: LineReachLineMeta[];
  kpis: { candidates: number; by_line: LineReachLineStat[] };
  districts: LineReachDistrictRow[];
}

export interface LineReachResult {
  accessible: boolean;
  exam_name: string;
  track: string;
  exams: string[];
  tracks: string[];
  lines: LineReachLineMeta[];
  kpis: { candidates: number; by_line: LineReachLineStat[] };
  districts: LineReachDistrictRow[];
  views?: {
    all?: LineReachView;
    physics?: LineReachView;
    history?: LineReachView;
  };
  message?: string;
}
