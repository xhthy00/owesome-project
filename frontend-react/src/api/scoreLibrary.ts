import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";

const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

export interface ScoreLibraryItem {
  exam_batch_id: number | null;
  exam_name: string;
  xx: string;
  bj: string;
  xkkm: string;
  sfzh: string;
  xm: string;
  anon_stu_id: string;
  zf6m: number | null;
  yw: number | null;
  sx: number | null;
  yy: number | null;
  wl: number | null;
  hxzh: number | null;
  swzh: number | null;
  ls: number | null;
  zzzh: number | null;
  dlzh: number | null;
}

export interface ScoreDetailItem {
  question_no: string;
  content: string;
  score: number | null;
  question_score: number | null;
}

export interface ScoreClassOption {
  xx: string;
  bj: string;
  grade: string;
}

export interface ScoreFilterOptions {
  schools: string[];
  grades: string[];
  classes: ScoreClassOption[];
  tracks: string[];
}

export interface ScoreDetail {
  subject: string;
  exam_id: number | null;
  exam_name: string;
  paper_name: string;
  ksh: string;
  xm: string;
  items: ScoreDetailItem[];
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

async function readData<T>(resp: Response, fallback: string): Promise<T> {
  if (resp.status === 401) throw new Error("Unauthorized");
  const body = (await resp.json()) as { code?: number; message?: string; data?: T };
  if ((body.code ?? resp.status) !== 200 || body.data == null) {
    throw new Error(body.message || fallback);
  }
  return body.data;
}

export const scoreLibraryApi = {
  async options(examBatchId?: number) {
    const params = new URLSearchParams();
    if (examBatchId) params.set("exam_batch_id", String(examBatchId));
    const resp = await fetch(`${getApiBaseUrl()}/education/score-library/options?${params.toString()}`, {
      headers: await authHeaders()
    });
    return readData<ScoreFilterOptions>(resp, "加载筛选项失败");
  },

  async list(query: {
    examBatchId?: number;
    school?: string;
    grade?: string;
    className?: string;
    track?: string;
    subject?: string;
    student?: string;
    page: number;
    pageSize: number;
  }) {
    const params = new URLSearchParams();
    params.set("page", String(query.page));
    params.set("page_size", String(query.pageSize));
    if (query.examBatchId) params.set("exam_batch_id", String(query.examBatchId));
    if (query.school) params.set("school", query.school);
    if (query.grade) params.set("grade", query.grade);
    if (query.className) params.set("class_name", query.className);
    if (query.track) params.set("track", query.track);
    if (query.subject) params.set("subject", query.subject);
    if (query.student) params.set("student", query.student);
    const resp = await fetch(`${getApiBaseUrl()}/education/score-library?${params.toString()}`, {
      headers: await authHeaders()
    });
    return readData<{ items: ScoreLibraryItem[]; total: number; page: number; page_size: number }>(
      resp,
      "加载成绩库失败"
    );
  },

  async detail(query: { examBatchId: number; anonStuId: string; subject: string }) {
    const params = new URLSearchParams();
    params.set("exam_batch_id", String(query.examBatchId));
    params.set("anon_stu_id", query.anonStuId);
    params.set("subject", query.subject);
    const resp = await fetch(`${getApiBaseUrl()}/education/score-library/detail?${params.toString()}`, {
      headers: await authHeaders()
    });
    return readData<ScoreDetail>(resp, "加载小题分失败");
  }
};
