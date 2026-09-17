import { apiRequest } from "@/api/client";

export interface SystemUser {
  id: number;
  account: string;
  name: string;
  email?: string;
  oid: number;
  status: number;
  language: string;
  origin: number;
  create_time: number;
  edu_role?: string | null;
  edu_role_label?: string | null;
  school_id?: string | null;
  school_name?: string | null;
  class_names?: string[] | null;
  student_id?: string | null;
  edu_remark?: string | null;
  sys_role?: string | null;
}

export interface Workspace {
  id: number;
  name: string;
  create_time: number;
}

export interface WorkspaceMember {
  id: number;
  uid: number;
  oid: number;
  weight: number;
  account: string;
  name: string;
  email?: string;
}

export const systemApi = {
  pagerUsers: (page = 1, pageSize = 20) =>
    apiRequest<{ total: number; items: SystemUser[] }>(`/user/pager/${page}/${pageSize}`),
  createUser: (payload: Partial<SystemUser> & { account: string; name: string; password?: string }) =>
    apiRequest<{ id: number }>("/user", { method: "POST", body: JSON.stringify(payload) }),
  batchCreateUsers: (payload: { csv: string }) =>
    apiRequest<{ success: number; failed: Array<{ row: number; account: string; reason: string }> }>(
      "/user/batch",
      { method: "POST", body: JSON.stringify(payload) }
    ),
  updateUserStatus: (id: number, status: number) =>
    apiRequest<{ id: number; status: number }>("/user/status", {
      method: "PATCH",
      body: JSON.stringify({ id, status })
    }),
  deleteUser: (id: number) => apiRequest<{ id: number }>(`/user/${id}`, { method: "DELETE" }),
  setUserSysRole: (id: number, sys_role: "platform_admin" | "") =>
    apiRequest<{ id: number; sys_role: string | null }>(`/user/${id}/sys-role`, {
      method: "PATCH",
      body: JSON.stringify({ sys_role })
    }),
  listWorkspaces: () => apiRequest<Workspace[]>("/system/workspace"),
  createWorkspace: (name: string) =>
    apiRequest<{ id: number; name: string }>("/system/workspace", {
      method: "POST",
      body: JSON.stringify({ name })
    }),
  updateWorkspace: (id: number, name: string) =>
    apiRequest<{ id: number; name: string }>("/system/workspace", {
      method: "PUT",
      body: JSON.stringify({ id, name })
    }),
  deleteWorkspace: (id: number) =>
    apiRequest<{ id: number }>(`/system/workspace/${id}`, {
      method: "DELETE"
    }),
  pagerWorkspaceMembers: (oid: number, page = 1, pageSize = 20) =>
    apiRequest<{ total: number; items: WorkspaceMember[] }>(
      `/system/workspace/uws/pager/${page}/${pageSize}?oid=${oid}`
    ),
  searchWorkspaceMembers: (oid: number, keyword: string, page = 1, pageSize = 20) =>
    apiRequest<{ total: number; items: WorkspaceMember[] }>(
      `/system/workspace/uws/pager/${page}/${pageSize}?oid=${oid}&keyword=${encodeURIComponent(keyword)}`
    ),
  addWorkspaceMember: (uid: number, oid: number, weight = 0) =>
    apiRequest<{ id: number }>("/system/workspace/uws", {
      method: "POST",
      body: JSON.stringify({ uid, oid, weight })
    }),
  addWorkspaceMembers: (uidList: number[], oid: number, weight = 0) =>
    apiRequest<{ created: number }>("/system/workspace/uws", {
      method: "POST",
      body: JSON.stringify({ uid_list: uidList, oid, weight })
    }),
  updateWorkspaceMemberType: (uid: number, oid: number, weight: number) =>
    apiRequest<{ id: number; weight: number }>("/system/workspace/uws", {
      method: "PUT",
      body: JSON.stringify({ uid, oid, weight })
    }),
  removeWorkspaceMembers: (uidList: number[], oid: number) =>
    apiRequest<{ uid_list: number[]; oid: number }>("/system/workspace/uws", {
      method: "DELETE",
      body: JSON.stringify({ uid_list: uidList, oid })
    }),
  workspaceOptionPager: (oid: number, page = 1, pageSize = 100, keyword = "") =>
    apiRequest<{ total: number; items: Array<{ id: number; name: string; account: string; email?: string }> }>(
      `/system/workspace/uws/option/pager/${page}/${pageSize}?oid=${oid}&keyword=${encodeURIComponent(keyword)}`
    ),
  workspaceOptionByKeyword: (keyword: string) =>
    apiRequest<{ id?: number; name?: string; account?: string; email?: string }>(
      `/system/workspace/uws/option?keyword=${encodeURIComponent(keyword)}`
    )
};
