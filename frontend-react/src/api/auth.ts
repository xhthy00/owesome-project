import { apiRequest, getApiBaseUrl } from "@/api/client";

export interface LoginPayload {
  username: string;
  password: string;
}

export interface LoginResult {
  access_token: string;
  token_type: string;
}

export interface CurrentUser {
  id: number;
  account: string;
  name: string;
  email?: string;
  oid: number;
  status: number;
  language: string;
  origin: number;
  create_time: number;
}

export async function login(payload: LoginPayload) {
  const body = new URLSearchParams();
  body.set("username", payload.username);
  body.set("password", payload.password);

  const response = await fetch(`${getApiBaseUrl()}/system/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded"
    },
    body
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  const result = (await response.json()) as {
    code?: number;
    message?: string;
    data?: LoginResult;
  };

  if (result.code && result.code !== 200) {
    throw new Error(result.message || "登录失败");
  }

  if (!result.data?.access_token) {
    throw new Error("登录失败，未返回 token");
  }
  return result.data;
}

export async function getCurrentUser() {
  return apiRequest<CurrentUser>("/system/me");
}
