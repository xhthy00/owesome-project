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
}

export interface ScoreImportResponse {
  ok: boolean;
  message: string;
  data: ScoreImportResult | null;
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
  }
};
