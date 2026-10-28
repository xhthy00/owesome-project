import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";

const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

export interface SchoolItem {
  id: string;
  name: string;
  s_name: string;
  district: string;
  stage: string;
  type: string;
}

export interface SchoolPayload {
  s_name: string;
  district: string;
  stage: string;
  type: string;
}

export interface SchoolListResult {
  items: SchoolItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface SchoolDependency {
  key: string;
  label: string;
  count: number;
  unit: string;
}

export interface SchoolFieldChange {
  field: string;
  label: string;
  before: string;
  after: string;
  effect: string;
}

export interface SchoolImpact {
  school_id: string;
  s_name: string;
  can_delete: boolean;
  can_rename: boolean;
  dependencies: SchoolDependency[];
  changes: SchoolFieldChange[];
  notes: string[];
}

export interface SchoolTypeCount {
  type: string;
  count: number;
}

export interface SchoolMeta {
  districts: string[];
  types: string[];
  stages: string[];
  total: number;
  type_counts: SchoolTypeCount[];
}

export interface SchoolQuery {
  page: number;
  pageSize: number;
  keyword?: string;
  district?: string;
  schoolType?: string;
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

function schoolPath(id: string): string {
  return encodeURIComponent(id);
}

export const schoolApi = {
  async list(query: SchoolQuery): Promise<SchoolListResult> {
    const params = new URLSearchParams();
    params.set("page", String(query.page));
    params.set("page_size", String(query.pageSize));
    if (query.keyword) params.set("keyword", query.keyword);
    if (query.district) params.set("district", query.district);
    if (query.schoolType) params.set("school_type", query.schoolType);
    const resp = await fetch(`${getApiBaseUrl()}/education/schools?${params.toString()}`, {
      headers: await authHeaders()
    });
    return readJson<SchoolListResult>(resp, "加载学校失败");
  },

  async meta(): Promise<SchoolMeta> {
    const resp = await fetch(`${getApiBaseUrl()}/education/schools/meta`, {
      headers: await authHeaders()
    });
    return readJson<SchoolMeta>(resp, "加载学校筛选项失败");
  },

  async create(payload: SchoolPayload): Promise<SchoolItem> {
    const resp = await fetch(`${getApiBaseUrl()}/education/schools`, {
      method: "POST",
      headers: { ...(await authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readJson<SchoolItem>(resp, "新增学校失败");
  },

  async update(id: string, payload: SchoolPayload): Promise<SchoolItem> {
    const resp = await fetch(`${getApiBaseUrl()}/education/schools/${schoolPath(id)}`, {
      method: "PUT",
      headers: { ...(await authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readJson<SchoolItem>(resp, "保存学校失败");
  },

  async remove(id: string): Promise<void> {
    const resp = await fetch(`${getApiBaseUrl()}/education/schools/${schoolPath(id)}`, {
      method: "DELETE",
      headers: await authHeaders()
    });
    await readJson(resp, "删除学校失败");
  },

  async impact(id: string): Promise<SchoolImpact> {
    const resp = await fetch(`${getApiBaseUrl()}/education/schools/${schoolPath(id)}/impact`, {
      headers: await authHeaders()
    });
    return readJson<SchoolImpact>(resp, "评估学校影响失败");
  },

  async preview(id: string, payload: SchoolPayload): Promise<SchoolImpact> {
    const resp = await fetch(`${getApiBaseUrl()}/education/schools/${schoolPath(id)}/impact`, {
      method: "POST",
      headers: { ...(await authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readJson<SchoolImpact>(resp, "评估学校影响失败");
  }
};
