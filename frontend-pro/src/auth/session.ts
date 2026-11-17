const ACCESS_TOKEN_KEY = "sqlbot_access_token";

function canUseStorage() {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function getAccessToken() {
  if (!canUseStorage()) return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setAccessToken(token: string) {
  if (!canUseStorage()) return;
  window.localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

export function clearAccessToken() {
  if (!canUseStorage()) return;
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
}

/** 主动退出：清 token 和工作空间，避免换账号沿用上一用户的空间选择。 */
export function clearAuthSession() {
  clearAccessToken();
  if (!canUseStorage()) return;
  window.localStorage.removeItem("frontend_react_workspace_oid");
}

export function isAuthed() {
  return !!getAccessToken();
}
