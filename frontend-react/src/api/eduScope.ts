export type EduScopeLike = {
  edu_role?: string | null;
  levels?: string[] | null;
  citywide?: boolean | null;
};

export function eduLevels(scope?: EduScopeLike | null): string[] {
  return Array.isArray(scope?.levels) ? scope.levels.filter(Boolean) : [];
}

export function isStudentScope(scope?: EduScopeLike | null): boolean {
  const levels = eduLevels(scope);
  return levels.length === 1 && levels[0] === "student";
}

export function isCitywideScope(scope?: EduScopeLike | null): boolean {
  return Boolean(scope?.citywide) || eduLevels(scope).includes("city");
}

/** 仅用于兼容既有建议问句/表单文案：把 V2 层级映射为旧角色语义。 */
export function legacyRoleOf(scope?: EduScopeLike | null): string {
  const levels = eduLevels(scope);
  if (isCitywideScope(scope) || levels.includes("district")) return "bureau_admin";
  if (levels.includes("school") || levels.includes("grade")) return "school_admin";
  if (levels.includes("class")) return "teacher";
  if (levels.includes("student")) return "student";
  return "";
}
