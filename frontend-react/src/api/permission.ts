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
}

export interface EduEffectiveResult {
  edu_scope: Record<string, unknown>;
  edu_predicates: string[];
  original_sql?: string;
  merged_sql?: string;
}

export const permissionApi = {
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
  getUserEduScope: (userId: number) =>
    apiRequest<EduScope & { user_id: number; account?: string }>(`/user/${userId}/edu-scope`),
  updateUserEduScope: (userId: number, payload: EduScope & { edu_role: string }) =>
    apiRequest<EduScope & { user_id: number }>(`/user/${userId}/edu-scope`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  deleteUserEduScope: (userId: number) =>
    apiRequest<{ user_id: number }>(`/user/${userId}/edu-scope`, {
      method: "DELETE"
    }),
  batchBindEduScope: (payload: { csv?: string; rows?: Array<Record<string, unknown>> }) =>
    apiRequest<{ success: number; failed: Array<{ row: number; account: string; reason: string }> }>(
      "/permission/edu/batch-bind",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  previewEduEffective: (payload: { user_id: number; sql?: string; datasource_id?: number }) =>
    apiRequest<EduEffectiveResult>("/permission/edu/effective", {
      method: "POST",
      body: JSON.stringify(payload)
    })
};
