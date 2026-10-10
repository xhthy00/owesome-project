import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";

const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

export interface KnowledgeItem {
  id: number;
  knowledge_no: string;
  stage: string;
  grade: string;
  subject: string;
  subject_no: string;
  module: string;
  module_no: string;
  chapter: string;
  chapter_no: string;
  secondary: string | null;
  secondary_no: string | null;
  knowledge_name: string;
  knowledge_content: string | null;
}

export interface KnowledgePayload {
  stage: string;
  grade: string;
  subject: string;
  subject_no: string;
  module: string;
  module_no: string;
  chapter: string;
  chapter_no: string;
  secondary: string;
  secondary_no: string;
  knowledge_name: string;
  knowledge_no: string;
}

export interface KnowledgeListResult {
  items: KnowledgeItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface KnowledgeVersion {
  id: number;
  knowledge_id: number;
  version_no: number;
  knowledge_content: string | null;
  change_type: string;
  operator: string | null;
  source: string | null;
  operated_at: string;
}

export interface KnowledgeImportResult {
  inserted: number;
  updated: number;
  errors: Array<{ row: number; message: string }>;
}

export interface KnowledgeQuery {
  page: number;
  pageSize: number;
  stage?: string;
  grade?: string;
  subject?: string;
  module?: string;
  chapter?: string;
  secondary?: string;
  keyword?: string;
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

export const knowledgeApi = {
  async list(query: KnowledgeQuery): Promise<KnowledgeListResult> {
    const params = new URLSearchParams();
    params.set("page", String(query.page));
    params.set("page_size", String(query.pageSize));
    for (const key of ["stage", "grade", "subject", "module", "chapter", "secondary", "keyword"] as const) {
      const value = query[key];
      if (value) params.set(key, value);
    }
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge?${params.toString()}`, {
      headers: await authHeaders()
    });
    return readJson<KnowledgeListResult>(resp, "加载知识点失败");
  },

  async cascade(level: string, parents: Record<string, string>): Promise<string[]> {
    const params = new URLSearchParams({ level });
    Object.entries(parents).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge/cascade?${params.toString()}`, {
      headers: await authHeaders()
    });
    const data = await readJson<{ options: string[] }>(resp, "加载筛选项失败");
    return data?.options ?? [];
  },

  async create(payload: KnowledgePayload): Promise<KnowledgeItem> {
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge`, {
      method: "POST",
      headers: { ...(await authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readJson<KnowledgeItem>(resp, "保存失败");
  },

  async update(id: number, payload: KnowledgePayload): Promise<KnowledgeItem> {
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge/${id}`, {
      method: "PUT",
      headers: { ...(await authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readJson<KnowledgeItem>(resp, "保存失败");
  },

  async remove(id: number): Promise<void> {
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge/${id}`, {
      method: "DELETE",
      headers: await authHeaders()
    });
    await readJson(resp, "删除失败");
  },

  async versions(id: number): Promise<KnowledgeVersion[]> {
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge/${id}/versions`, {
      headers: await authHeaders()
    });
    const data = await readJson<{ versions: KnowledgeVersion[] }>(resp, "加载历史版本失败");
    return data?.versions ?? [];
  },

  async downloadTemplate(): Promise<void> {
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge/template`, {
      headers: await authHeaders()
    });
    if (!resp.ok) throw new Error("下载模板失败");
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "知识点导入模板.xlsx";
    a.click();
    URL.revokeObjectURL(url);
  },

  async importFile(file: File): Promise<KnowledgeImportResult> {
    const body = new FormData();
    body.append("file", file);
    const resp = await fetch(`${getApiBaseUrl()}/education/knowledge/import`, {
      method: "POST",
      headers: await authHeaders(),
      body
    });
    return readJson<KnowledgeImportResult>(resp, "导入失败");
  }
};
