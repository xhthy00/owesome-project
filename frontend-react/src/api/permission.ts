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

export const permissionApi = {
  listRoles: () => apiRequest<RoleItem[]>("/permission/roles"),
  listUserRoleGrants: () => apiRequest<UserRoleGrant[]>("/permission/grants/user-role"),
  listResourceGrants: () => apiRequest<ResourceGrant[]>("/permission/grants/resource"),
  listDataRules: () => apiRequest<DataRuleItem[]>("/permission/data-rules")
};
