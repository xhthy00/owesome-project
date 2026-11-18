import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";

export type OverviewLevel = "city" | "district" | "school" | "grade" | "class" | "student";

export interface OverviewLine {
  line_key: string;
  line_name?: string;
  label?: string;
  threshold?: number;
  track?: string;
}

export interface OverviewRow {
  id: string;
  label: string;
  candidates?: number;
  by_line?: Array<{
    line_key: string;
    line_name?: string;
    label?: string;
    threshold?: number;
    track?: string;
    reached: number;
    rate: number;
  }>;
  total?: number | null;
  rank?: number;
  class_name?: string;
  track?: string;
  subjects?: Record<string, number | null>;
}

export interface OverviewScope {
  label?: string;
  levels?: string[];
  schools?: string[];
  districts?: string[];
  cohorts?: string[];
  classes?: string[];
  capabilities?: string[];
  multi_school?: boolean;
  multi_class?: boolean;
}

export interface OverviewFilters {
  exams?: string[];
  tracks?: string[];
  lines?: OverviewLine[];
  cohorts?: string[];
  classes?: string[];
  subjects?: Array<{ key: string; label: string }>;
}

export interface OverviewResult {
  level: OverviewLevel | "";
  scope?: OverviewScope;
  filters?: OverviewFilters;
  kpis?: {
    candidates?: number;
    by_line?: Array<{
      line_key: string;
      line_name?: string;
      label?: string;
      threshold?: number;
      reached: number;
      rate: number;
    }>;
  };
  chart?: {
    dimension?: string;
    metric?: string;
    rows?: OverviewRow[];
  };
  table?: {
    dimension?: string;
    columns?: string[];
    rows?: OverviewRow[];
  };
  secondary?: {
    class_trends?: ClassExamTrend[];
    score_bands?: {
      segments?: Array<{ label: string; count: number; ratio: number }>;
      count?: number;
      avg?: number | null;
      max?: number | null;
      min?: number | null;
      pass_rate?: number | null;
    };
    subject_avg?: Array<{ key: string; label: string; avg: number; count: number }>;
    grade_baseline?: {
      avg?: number | null;
      n?: number;
      units?: number;
      own_avg?: number | null;
      gap?: number | null;
    };
  };
  notices?: Array<{ code: string; message?: string }>;
  actions?: Array<{ label: string; href: string }>;
  fallback?: string;
}

export interface ClassExamTrend {
  exam: string;
  date: string;
  avg: number | null;
  valid_count: number;
  special: {
    rate: number;
    reached: number;
    candidates: number;
    thresholds: Array<{ track: string; score: number }>;
  } | null;
}

export interface OverviewQuery {
  exam_name?: string;
  track?: string;
  line_key?: string;
  cohort?: string;
  class_name?: string;
}

async function authHeaders(): Promise<HeadersInit> {
  const token = getAccessToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
}

export async function getEducationOverview(query: OverviewQuery = {}): Promise<OverviewResult> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value) params.set(key, value);
  }
  const qs = params.toString();
  const resp = await fetch(`${getApiBaseUrl()}/education/overview${qs ? `?${qs}` : ""}`, {
    headers: await authHeaders()
  });
  if (resp.status === 401) throw new Error("Unauthorized");
  const body = (await resp.json()) as {
    code?: number;
    message?: string;
    data?: OverviewResult;
  };
  if ((body.code ?? 200) === 403) {
    throw new Error(body.message || "当前账号无权查看学情总览");
  }
  if ((body.code ?? 200) !== 200 || !body.data) {
    throw new Error(body.message || "加载学情总览失败");
  }
  return body.data;
}
