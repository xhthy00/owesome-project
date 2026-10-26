import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";
import type { CurrentUser } from "@/api/auth";

const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

export interface ExamLibraryItem {
  id: number;
  exam_name: string;
  exam_time: string | null;
  subject: string;
  exam_score: number | null;
  exam_type: "city" | "school" | "teacher";
  exam_type_label: string;
  school_id: string | null;
  school_name?: string | null;
  owner_id: string | null;
  exam_batch_id: number | null;
  exam_batch_name?: string | null;
  question_count: number;
  score_count?: number;
  detail_count?: number;
  question_score_sum?: number;
  unmatched_knowledge_count?: number;
}

export interface KnowledgeLink {
  knowledge_id: number;
  knowledge_name?: string;
  weight?: number | null;
}

export interface ExamQuestion {
  id: number;
  question_no: string;
  question_type: string | null;
  question_score: number | null;
  content: string;
  knowledge_links: KnowledgeLink[];
}

export interface KnowledgeOption {
  id: number;
  knowledge_name: string;
  stage?: string | null;
  grade?: string | null;
  module?: string | null;
  chapter?: string | null;
  secondary?: string | null;
}

export interface ExamDetail {
  exam: ExamLibraryItem;
  questions: ExamQuestion[];
  knowledge_options: KnowledgeOption[];
}

export interface ExamBatchItem {
  id: number;
  batch_name: string;
  exam_time: string | null;
  exam_count: number;
  school_id?: string | null;
  owner_id?: string | null;
  exam_type?: string | null;
  exam_type_label?: string | null;
  jc?: string | null;
  writable?: boolean;
}

export interface ExamPayload {
  exam_name: string;
  exam_type: string;
  subject: string;
  exam_score: number;
  exam_time?: string | null;
  school_id?: string | null;
  owner_id?: string | null;
  exam_batch_id?: number | null;
}

export interface MatchSuggestion {
  knowledge_id: number;
  knowledge_name: string;
  confidence?: number;
  reason?: string;
}

export interface MatchItem {
  id: number;
  question_id: number;
  question_no: string;
  content: string;
  current_links: KnowledgeLink[];
  add_suggestions: MatchSuggestion[];
  remove_suggestions: MatchSuggestion[];
  verdict: string;
  adopted: boolean;
}

export function canManageExamBatches(user: CurrentUser | null): boolean {
  if (!user) return false;
  return Boolean(
    user.is_platform_admin || user.is_super_admin || user.edu_scope?.edu_role === "bureau_admin"
  );
}

export function canCreateExam(user: CurrentUser | null): boolean {
  if (!user) return false;
  return canManageExamBatches(user) || user.edu_scope?.edu_role === "school_admin";
}

export function canCreateBatch(user: CurrentUser | null): boolean {
  return canCreateExam(user);
}

export function canMaintainBatch(
  user: CurrentUser | null,
  batch: { school_id?: string | null; owner_id?: string | null; writable?: boolean } | null | undefined
): boolean {
  if (!user || !batch) return false;
  if (canManageExamBatches(user)) return true;
  if (user.edu_scope?.edu_role !== "school_admin") return false;
  if (batch.writable === true) return true;
  const school = String(batch.school_id || "").trim();
  const mine = String(user.edu_scope?.school_id || "").trim();
  if (school && mine && school === mine) return true;
  const owner = String(batch.owner_id || "").trim();
  const account = String(user.account || "").trim();
  return Boolean(owner && account && owner === account);
}

export function canMaintainExam(
  user: CurrentUser | null,
  exam: { exam_type?: string | null; school_id?: string | null; owner_id?: string | null } | null | undefined
): boolean {
  if (!user || !exam) return false;
  if (canManageExamBatches(user)) return true;
  if (user.edu_scope?.edu_role === "school_admin" && exam.exam_type === "school") {
    const school = String(exam.school_id || "").trim();
    const mine = String(user.edu_scope?.school_id || "").trim();
    if (school && mine && school === mine) return true;
  }
  const owner = String(exam.owner_id || "").trim();
  const account = String(user.account || "").trim();
  return Boolean(owner && account && owner === account);
}

export interface MatchTask {
  id: number;
  exam_id: number;
  scope: string;
  question_id: number | null;
  status: string;
  total: number;
  done: number;
  failed: number;
  summary: string;
  error: string;
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

async function readJson<T>(resp: Response, fallback: string): Promise<T> {
  if (resp.status === 401) throw new Error("Unauthorized");
  const body = (await resp.json()) as { code?: number; message?: string; data?: T };
  if ((body.code ?? resp.status) !== 200) {
    throw new Error(body.message || fallback);
  }
  return body.data as T;
}

async function send<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const headers = await authHeaders();
  const resp = await fetch(`${getApiBaseUrl()}/education/exam-library${path}`, {
    ...init,
    headers: {
      ...headers,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {})
    }
  });
  return readJson<T>(resp, fallback);
}

export const examLibraryApi = {
  listBatches() {
    return send<ExamBatchItem[]>("/batches", { method: "GET" }, "加载考试批次失败");
  },
  createBatch(payload: { batch_name: string; exam_time: string; exam_type: string; jc: string }) {
    return send<ExamBatchItem>("/batches", { method: "POST", body: JSON.stringify(payload) }, "创建批次失败");
  },
  updateBatch(id: number, payload: { batch_name: string; exam_time: string; exam_type: string; jc: string }) {
    return send<ExamBatchItem>(`/batches/${id}`, { method: "PUT", body: JSON.stringify(payload) }, "更新批次失败");
  },
  removeBatch(id: number) {
    return send<{ id: number }>(`/batches/${id}`, { method: "DELETE" }, "删除批次失败");
  },
  list(query: {
    keyword?: string;
    examType?: string;
    subject?: string;
    examBatchId?: number;
    page: number;
    pageSize: number;
  }) {
    const params = new URLSearchParams();
    params.set("page", String(query.page));
    params.set("page_size", String(query.pageSize));
    if (query.keyword) params.set("keyword", query.keyword);
    if (query.examType) params.set("exam_type", query.examType);
    if (query.subject) params.set("subject", query.subject);
    if (query.examBatchId) params.set("exam_batch_id", String(query.examBatchId));
    return send<{ items: ExamLibraryItem[]; total: number; page: number; page_size: number }>(
      `?${params.toString()}`,
      { method: "GET" },
      "加载试卷库失败"
    );
  },
  detail(id: number) {
    return send<ExamDetail>(`/${id}`, { method: "GET" }, "加载试卷失败");
  },
  create(payload: ExamPayload) {
    return send<ExamLibraryItem>("", { method: "POST", body: JSON.stringify(payload) }, "创建试卷失败");
  },
  update(id: number, payload: ExamPayload) {
    return send<ExamLibraryItem>(`/${id}`, { method: "PUT", body: JSON.stringify(payload) }, "更新试卷失败");
  },
  remove(id: number) {
    return send<{ id: number }>(`/${id}`, { method: "DELETE" }, "删除试卷失败");
  },
  saveQuestions(
    id: number,
    questions: Array<{
      id: number | null;
      question_no: string;
      question_type: string | null;
      question_score: number;
      content: string;
      knowledge_links: Array<{ knowledge_id: number; weight?: number | null }>;
    }>
  ) {
    return send<{ questions: ExamQuestion[] }>(
      `/${id}/questions`,
      { method: "PUT", body: JSON.stringify({ questions }) },
      "保存题目失败"
    );
  },
  startExamMatch(id: number) {
    return send<MatchTask>(`/${id}/match`, { method: "POST" }, "开始核对失败");
  },
  startQuestionMatch(examId: number, questionId: number) {
    return send<MatchTask>(`/${examId}/questions/${questionId}/match`, { method: "POST" }, "开始核对失败");
  },
  task(taskId: number) {
    return send<MatchTask>(`/match/${taskId}`, { method: "GET" }, "读取核对进度失败");
  },
  cancel(taskId: number) {
    return send<{ id: number }>(`/match/${taskId}/cancel`, { method: "POST" }, "取消核对失败");
  },
  report(examId: number, taskId?: number) {
    const query = taskId ? `?task_id=${taskId}` : "";
    return send<{ task: MatchTask | null; items: MatchItem[] }>(
      `/${examId}/match-report${query}`,
      { method: "GET" },
      "读取核对报告失败"
    );
  },
  apply(
    taskId: number,
    operations: Array<{ item_id: number; add_ids: number[]; remove_ids: number[]; weights?: Record<string, number> }>
  ) {
    return send<{ success: number; errors: string[] }>(
      `/match/${taskId}/apply`,
      { method: "POST", body: JSON.stringify({ operations }) },
      "采纳建议失败"
    );
  }
};
