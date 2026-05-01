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

export function isAuthed() {
  return !!getAccessToken();
}
