const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

type ApiEnvelope<T> = {
  code?: number;
  message?: string;
  data?: T;
};

export function getApiBaseUrl() {
  return API_BASE_URL;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  const payload = (await response.json()) as T | ApiEnvelope<T>;
  if (payload && typeof payload === "object" && "data" in payload) {
    return (payload as ApiEnvelope<T>).data as T;
  }
  return payload as T;
}
