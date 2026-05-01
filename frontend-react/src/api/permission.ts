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
  table_id?: number;
  expression_tree?: string;
  permissions?: string;
}

export interface PermissionGroup {
  id: number;
  name: string;
  users: number[];
  permissions: PermissionRuleDetail[];
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
    })
};
