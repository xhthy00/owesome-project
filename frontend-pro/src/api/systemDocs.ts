import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";

export interface SystemDoc {
  key: string;
  title: string;
  kind: "docx" | "video";
  filename: string;
  summary: string;
  url: string;
}

async function authHeaders(): Promise<HeadersInit> {
  const token = getAccessToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
}

export async function listSystemDocs(): Promise<SystemDoc[]> {
  const resp = await fetch(`${getApiBaseUrl()}/system/docs`, { headers: await authHeaders() });
  const body = (await resp.json()) as { code?: number; message?: string; data?: SystemDoc[] };
  if (resp.status === 401 || body.code === 401) {
    throw new Error(body.message || "Unauthorized");
  }
  if ((body.code ?? resp.status) !== 200 || !body.data) {
    throw new Error(body.message || "加载系统文档失败");
  }
  return body.data;
}

export async function fetchSystemDocFile(key: string): Promise<Blob> {
  const resp = await fetch(`${getApiBaseUrl()}/system/docs/${key}/file`, {
    headers: await authHeaders()
  });
  const type = resp.headers.get("content-type") || "";
  if (type.includes("application/json") || !resp.ok) {
    const body = (await resp.json().catch(() => null)) as { message?: string } | null;
    throw new Error(body?.message || "文档暂时无法打开");
  }
  return resp.blob();
}
