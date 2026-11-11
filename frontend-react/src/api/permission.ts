import { apiRequest } from "@/api/client";

export interface RoleItem {
  id: number;
  code: string;
  name: string;
  description?: string;
}

export interface UserRoleGrant {
  id: number;
  user_id: number;
  account: string;
  role_codes: string[];
  oid: number;
}

export interface ResourceGrant {
  id: number;
  principal_type: "user" | "role";
  principal: string;
  resource_type: "datasource" | "chat";
  resource_ids: number[];
}

export interface DataRuleItem {
  id: number;
  scope: "row" | "column";
  datasource_id: number;
  table_name: string;
  rule: string;
  enabled: boolean;
}

export interface PermissionRuleDetail {
  id?: number;
  name: string;
  type: "row" | "column";
  ds_id?: number;
  table_name?: string;
  expression_tree?: string;
  permissions?: string;
}

export interface PermissionGroup {
  id: number;
  name: string;
  users: number[];
  permissions: PermissionRuleDetail[];
}

export interface EduRoleItem {
  code: string;
  label: string;
  required_fields: string[];
}

export interface EduScope {
  edu_role?: string;
  school_id?: string;
  school_name?: string;
  class_names?: string[];
  student_id?: string;
  edu_remark?: string;
}

export interface EduEffectiveResult {
  edu_scope: Record<string, unknown>;
  edu_predicates: string[];
  original_sql?: string;
  merged_sql?: string;
}

export interface MenuVisibilityMap {
  [menuKey: string]: boolean;
}

export interface EduPrivacySetting {
  anonymize_display: boolean;
}

export interface EduSchoolItem {
  id: string;
  name?: string;
  s_name?: string;
  label: string;
}

export type EduScopeLevel = "city" | "district" | "school" | "grade" | "class" | "student";
export interface EduGrantV2 {
  id: string;
  workspace_oid: number;
  datasource_id: number;
  level: EduScopeLevel;
  city_id: string;
  district_id?: string | null;
  school_id?: string | null;
  cohort_id?: string | null;
  class_id?: string | null;
  student_id?: string | null;
  capabilities: string[];
  can_delegate?: boolean;
  enabled: boolean;
  valid_from?: string | null;
  valid_to?: string | null;
  history_from?: string | null;
  history_to?: string | null;
  remark?: string;
}
export interface EduAuthorizationV2 {
  schema_version: number;
  revision: number;
  grants: EduGrantV2[];
}
export interface EduSourceInfo {
  datasource_id: number;
  workspace_oid: number;
  name: string;
  source_type: "DATABASE_EDU_URL";
  city_id: string;
  city_label: string;
  supports_history: boolean;
}
export interface EduPermissionUser {
  id: number;
  account: string;
  name: string;
  status: number;
  manageable: boolean;
  managed?: boolean;
  active_count?: number;
  document?: EduAuthorizationV2;
  message?: string;
}

export const permissionApi = {
  eduV2Users: (page: number, pageSize: number, keyword = "") =>
    apiRequest<{ items: EduPermissionUser[]; total: number; page: number; page_size: number }>(
      `/permission/edu/v2/users?page=${page}&page_size=${pageSize}&keyword=${encodeURIComponent(keyword)}`),
  eduV2Source: () => apiRequest<EduSourceInfo>("/permission/edu/v2/source"),
  eduV2Bindings: () => apiRequest<Array<{ datasource_id: number; name: string }>>("/permission/edu/v2/bindings"),
  eduV2Catalog: (datasourceId?: number, refresh = false) => apiRequest<{
    workspace_oid: number; datasource_id: number; items: Array<Record<string, string>>;
  }>(`/permission/edu/v2/catalog?refresh=${refresh}${datasourceId ? `&datasource_id=${datasourceId}` : ""}`),
  getEduV2: (userId: number, datasourceId: number) =>
    apiRequest<EduAuthorizationV2>(`/permission/edu/v2/grants/${userId}?datasource_id=${datasourceId}`),
  saveEduV2: (userId: number, datasourceId: number, expectedRevision: number, grants: EduGrantV2[]) =>
    apiRequest<EduAuthorizationV2>(`/permission/edu/v2/grants/${userId}?datasource_id=${datasourceId}`, {
      method: "PUT", body: JSON.stringify({ expected_revision: expectedRevision, grants })
    }),
  previewEduV2: (userId: number, datasourceId: number, sql?: string) =>
    apiRequest<Record<string, unknown>>("/permission/edu/v2/effective", {
      method: "POST", body: JSON.stringify({ user_id: userId, datasource_id: datasourceId, sql: sql || null })
    }),
  batchEduV2: (datasourceId: number, csv: string, apply = false) =>
    apiRequest<{ validated: number; applied: number }>(`/permission/edu/v2/batch-bind/${apply ? "apply" : "preview"}`, {
      method: "POST", body: JSON.stringify({ datasource_id: datasourceId, csv })
    }),
  listRoles: () => apiRequest<RoleItem[]>("/permission/roles"),
  listUserRoleGrants: () => apiRequest<UserRoleGrant[]>("/permission/grants/user-role"),
  listResourceGrants: () => apiRequest<ResourceGrant[]>("/permission/grants/resource"),
  listDataRules: () => apiRequest<DataRuleItem[]>("/permission/data-rules"),
  listPermissionGroups: () => apiRequest<PermissionGroup[]>("/ds_permission/list", { method: "POST" }),
  savePermissionGroup: (payload: {
    id?: number;
    name: string;
    users: number[];
    permissions: PermissionRuleDetail[];
  }) =>
    apiRequest<{ id: number }>("/ds_permission/save", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  deletePermissionGroup: (id: number) =>
    apiRequest<{ id: number }>(`/ds_permission/delete/${id}`, {
      method: "POST"
    }),
  listEduRoles: () => apiRequest<EduRoleItem[]>("/permission/edu/roles"),
  listEduSchools: (datasourceId?: number) =>
    apiRequest<{ datasource_id: number | null; items: EduSchoolItem[] }>(
      `/permission/edu/schools${datasourceId ? `?datasource_id=${datasourceId}` : ""}`
    ),
  getUserEduScope: (userId: number) =>
    apiRequest<EduScope & { user_id: number; account?: string }>(`/user/${userId}/edu-scope`),
  updateUserEduScope: (userId: number, payload: EduScope & { edu_role: string }, datasourceId?: number) =>
    apiRequest<EduScope & { user_id: number }>(
      `/user/${userId}/edu-scope${datasourceId ? `?datasource_id=${datasourceId}` : ""}`,
      {
        method: "PUT",
        body: JSON.stringify(payload)
      }
    ),
  deleteUserEduScope: (userId: number) =>
    apiRequest<{ user_id: number }>(`/user/${userId}/edu-scope`, {
      method: "DELETE"
    }),
  batchBindEduScope: (payload: {
    csv?: string;
    rows?: Array<Record<string, unknown>>;
    datasource_id?: number;
  }) =>
    apiRequest<{ success: number; failed: Array<{ row: number; account: string; reason: string }> }>(
      "/permission/edu/batch-bind",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  previewEduEffective: (payload: { user_id: number; sql?: string; datasource_id?: number }) =>
    apiRequest<EduEffectiveResult>("/permission/edu/effective", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getMenuVisibility: () => apiRequest<MenuVisibilityMap>("/permission/menu-visibility"),
  setMenuVisibility: (menuKey: string, visible: boolean) =>
    apiRequest<void>("/permission/menu-visibility", {
      method: "POST",
      body: JSON.stringify({ menu_key: menuKey, visible })
    }),
  getEduPrivacy: () => apiRequest<EduPrivacySetting>("/permission/edu-privacy"),
  setEduPrivacy: (anonymize_display: boolean) =>
    apiRequest<EduPrivacySetting>("/permission/edu-privacy", {
      method: "PUT",
      body: JSON.stringify({ anonymize_display })
    })
};
