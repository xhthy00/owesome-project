/** 智学助手：全局 AI 助手元信息与对外文案 */

export const ASSISTANT_NAME = "智学助手";
export const ASSISTANT_AVATAR = "/assistant-avatar.png?v=3";
export const ASSISTANT_THINKING = "助手正在抓紧分析您的提问…";
export const ASSISTANT_WELCOME_TITLE = "您好，我是智学助手";
export const ASSISTANT_WELCOME_DESC = "用自然语言提问即可，我会协助分析成绩、生成图表与报告，并给出可行建议。";
export const ASSISTANT_SUGGESTIONS = [
  "生成高三2班班级总览报告",
  "对比各班本次月考成绩",
  "哪些学生需要分层预警"
] as const;

export const STORY_FOLD_TITLE = "助手工作进展";
export const THINK_FOLD_TITLE = "分析思路";

const FOLLOWUPS_BY_TYPE: Record<string, string[]> = {
  class_overview: ["查看需要重点关注的学生", "与其他班级对比", "生成分层预警报告"],
  grade_comparison: ["深入分析某个班级", "生成科目诊断报告", "查看成绩变化趋势"],
  subject_diagnosis: ["查看薄弱知识点", "对比其他科目", "生成班级总览报告"],
  student_profile: ["查看该生成绩趋势", "对比班级平均水平", "生成科目诊断报告"],
  trend_tracking: ["聚焦最近一次考试", "查看成绩下滑学生", "生成分层预警报告"],
  tier_alert: ["查看需关注的学生名单", "生成班级总览报告", "查看成绩变化趋势"],
  group_feature: ["换一个分析维度", "生成综合分析报告", "查看班级对比"],
  comprehensive: ["下钻分析某个班级", "生成分层预警报告", "查看科目诊断"],
  diagnostic_report: ["聚焦薄弱环节", "对比班级整体情况", "生成改进建议"]
};

const DEFAULT_FOLLOWUPS = ["换一个角度继续分析", "生成综合分析报告", "导出当前结论"];

export function getFollowups(reportType?: string): string[] {
  if (!reportType) return DEFAULT_FOLLOWUPS;
  return FOLLOWUPS_BY_TYPE[reportType] ?? DEFAULT_FOLLOWUPS;
}
