import { request } from '@/utils/request'

export interface DatasourceConfig {
  host: string
  port: number
  username: string
  password: string
  database: string
  driver?: string
  extraJdbc?: string
  dbSchema?: string
  timeout?: number
  ssl?: boolean
  type?: string
}

export interface DatasourceItem {
  id: number
  name: string
  description?: string
  type: string
  type_name?: string
  status?: string
  create_time?: string
  create_by?: number
}

export interface DatasourceListResult {
  total: number
  items: DatasourceItem[]
}

export interface DatasourceDetail extends DatasourceItem {
  config: DatasourceConfig
}

export interface DatasourceCreatePayload {
  name: string
  description?: string
  type: string
  config: DatasourceConfig
}

export type DatasourceUpdatePayload = Partial<DatasourceCreatePayload>

export interface ConnectionTestResult {
  success: boolean
  message: string
  version?: string
}

export const datasourceApi = {
  list: (params?: { skip?: number; limit?: number; oid?: number }): Promise<DatasourceListResult> =>
    request.get('/datasource', { params }),
  get: (id: number): Promise<DatasourceDetail> => request.get(`/datasource/${id}`),
  add: (data: DatasourceCreatePayload): Promise<DatasourceItem> => request.post('/datasource', data),
  update: (id: number, data: DatasourceUpdatePayload): Promise<DatasourceItem> =>
    request.put(`/datasource/${id}`, data),
  delete: (id: number): Promise<void> => request.delete(`/datasource/${id}`),
  testConnection: (id: number): Promise<ConnectionTestResult> =>
    request.post(`/datasource/${id}/test-connection`),
}
