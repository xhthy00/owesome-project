import { clearAccessToken, getAccessToken } from "@/auth/session";

/** 与 pages/_app.tsx LayoutWrapper 中 WORKSPACE_OID_KEY 保持一致 */
const WORKSPACE_OID_STORAGE_KEY = "frontend_react_workspace_oid";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";
const AUTH_EXPIRED_TIP_KEY = "auth_expired_tip";

function handleUnauthorizedRedirect() {
  clearAccessToken();
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(AUTH_EXPIRED_TIP_KEY, "1");
  const redirect = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?redirect=${redirect}`;
}

type ApiEnvelope<T> = {
  code?: number;
  message?: string;
  data?: T;
};

export function getApiBaseUrl() {
  return API_BASE_URL;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAccessToken();
  const wsOid =
    typeof window !== "undefined" ? window.localStorage.getItem(WORKSPACE_OID_STORAGE_KEY) : null;
  const baseHeaders: HeadersInit = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(wsOid ? { "X-Workspace-Oid": wsOid.trim() } : {})
  };
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { ...baseHeaders, ...(init?.headers ?? {}) },
    ...init
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorizedRedirect();
      throw new Error("Unauthorized");
    }
    throw new Error(`Request failed: ${response.status}`);
  }

  const payload = (await response.json()) as T | ApiEnvelope<T>;
  if (payload && typeof payload === "object" && "code" in payload) {
    const envelope = payload as ApiEnvelope<T>;
    const code = envelope.code ?? 200;
    if (code === 401) {
      handleUnauthorizedRedirect();
      throw new Error(envelope.message || "Unauthorized");
    }
    if (code !== 200) {
      throw new Error(envelope.message || `Request failed: ${code}`);
    }
    return envelope.data as T;
  }
  return payload as T;
}
