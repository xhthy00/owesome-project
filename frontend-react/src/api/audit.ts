import { apiRequest } from "./client";

export interface AuditAccessLogItem {
  id: number;
  trace_id: string;
  user_id: number | null;
  user_account: string | null;
  workspace_oid: number | null;
  ip: string | null;
  user_agent: string | null;
  success: boolean;
  error_msg: string | null;
  elapsed_ms: number | null;
  created_at: string | null;
  request_method: string;
  request_path: string;
  datasource_id: number | null;
  query_text: string | null;
}

export interface AuditOperationLogItem {
  id: number;
  trace_id: string;
  user_id: number | null;
  user_account: string | null;
  workspace_oid: number | null;
  ip: string | null;
  user_agent: string | null;
  success: boolean;
  error_msg: string | null;
  elapsed_ms: number | null;
  created_at: string | null;
  operation_type: string;
  resource_type: string;
  resource_id: string | null;
  request_method: string | null;
  request_path: string | null;
  detail: string | null;
}

export interface AuditLoginLogItem {
  id: number;
  trace_id: string;
  user_id: number | null;
  user_account: string | null;
  ip: string | null;
  user_agent: string | null;
  success: boolean;
  error_msg: string | null;
  created_at: string | null;
  account: string;
  fail_reason: string | null;
}

export interface PagerResult<T> {
  total: number;
  items: T[];
}

export interface AccessLogQuery {
  user_id?: number;
  datasource_id?: number;
  success?: boolean;
  request_path?: string;
  start_time?: number;
  end_time?: number;
}

export interface OperationLogQuery {
  user_id?: number;
  resource_type?: string;
  operation_type?: string;
  success?: boolean;
  start_time?: number;
  end_time?: number;
}

export interface LoginLogQuery {
  account?: string;
  success?: boolean;
  start_time?: number;
  end_time?: number;
}

function buildQuery(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.append(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const auditApi = {
  pagerAccessLog(page: number, pageSize: number, q: AccessLogQuery = {}) {
    return apiRequest<PagerResult<AuditAccessLogItem>>(
      `/audit/access/pager/${page}/${pageSize}${buildQuery(q as Record<string, unknown>)}`
    );
  },
  pagerOperationLog(page: number, pageSize: number, q: OperationLogQuery = {}) {
    return apiRequest<PagerResult<AuditOperationLogItem>>(
      `/audit/operation/pager/${page}/${pageSize}${buildQuery(q as Record<string, unknown>)}`
    );
  },
  pagerLoginLog(page: number, pageSize: number, q: LoginLogQuery = {}) {
    return apiRequest<PagerResult<AuditLoginLogItem>>(
      `/audit/login/pager/${page}/${pageSize}${buildQuery(q as Record<string, unknown>)}`
    );
  },
};