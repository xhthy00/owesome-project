import { clearAccessToken, getAccessToken } from "@/auth/session";

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
  const baseHeaders: HeadersInit = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {})
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
  if (
    payload &&
    typeof payload === "object" &&
    "code" in payload &&
    (payload as ApiEnvelope<T>).code === 401
  ) {
    handleUnauthorizedRedirect();
    throw new Error((payload as ApiEnvelope<T>).message || "Unauthorized");
  }
  if (payload && typeof payload === "object" && "data" in payload) {
    return (payload as ApiEnvelope<T>).data as T;
  }
  return payload as T;
}
