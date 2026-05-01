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
  updateUserStatus: (id: number, status: number) =>
    apiRequest<{ id: number; status: number }>("/user/status", {
      method: "PATCH",
      body: JSON.stringify({ id, status })
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
