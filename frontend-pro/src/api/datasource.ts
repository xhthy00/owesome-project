import { apiRequest } from "@/api/client";

export interface DatasourceConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  database: string;
  timeout?: number;
  ssl?: boolean;
}

export interface DatasourceItem {
  id: number;
  name: string;
  description?: string;
  type: string;
  type_name?: string;
  status?: string;
  create_time?: string;
}

export interface DatasourceListResult {
  total: number;
  items: DatasourceItem[];
}

export interface DatasourceDetail extends DatasourceItem {
  config: DatasourceConfig;
}

export interface DatasourceCreatePayload {
  name: string;
  description?: string;
  type: string;
  config: DatasourceConfig;
}

export type DatasourceUpdatePayload = Partial<DatasourceCreatePayload>;

export interface ConnectionTestResult {
  success: boolean;
  message: string;
  version?: string;
}

export interface DatasourceTableItem {
  id: number;
  table_name: string;
  table_comment?: string;
}

export interface DatasourceFieldItem {
  id: number;
  field_name: string;
  field_type?: string;
  field_comment?: string;
}

export const datasourceApi = {
  list: (params?: { skip?: number; limit?: number; oid?: number }) =>
    apiRequest<DatasourceListResult>(`/datasource${params ? `?${new URLSearchParams(params as Record<string, string>).toString()}` : ""}`),
  get: (id: number) => apiRequest<DatasourceDetail>(`/datasource/${id}`),
  add: (data: DatasourceCreatePayload) =>
    apiRequest<DatasourceItem>("/datasource", {
      method: "POST",
      body: JSON.stringify(data)
    }),
  update: (id: number, data: DatasourceUpdatePayload) =>
    apiRequest<DatasourceItem>(`/datasource/${id}`, {
      method: "PUT",
      body: JSON.stringify(data)
    }),
  delete: (id: number) =>
    apiRequest<void>(`/datasource/${id}`, {
      method: "DELETE"
    }),
  testConnection: (id: number) =>
    apiRequest<ConnectionTestResult>(`/datasource/${id}/test-connection`, {
      method: "POST"
    }),
  tableList: (datasourceId: number) =>
    apiRequest<DatasourceTableItem[]>(`/datasource/${datasourceId}/tables`),
  fieldList: (tableId: number) =>
    apiRequest<DatasourceFieldItem[]>(`/datasource/table/${tableId}/fields`)
};
