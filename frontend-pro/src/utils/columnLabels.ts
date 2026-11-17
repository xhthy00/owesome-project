/** 查询结果列名 → 中文表头。译不出来时保留原始列名,绝不退化成同一个占位词。 */

const COLUMN_LABELS: Record<string, string> = {
  exam_name: "考试",
  exam: "考试",
  exam_batch: "考试批次",
  exam_batch_id: "考试批次",
  ksmc: "考试",
  batch_name: "考试批次",
  paper_name: "试卷",
  exam_time: "考试时间",
  district: "区县",
  dq: "区县",
  qx: "区县",
  line_name: "分数线",
  line_code: "分数线代码",
  threshold: "分数线",
  track: "选科",
  xkkm: "选科",
  xkfx: "选科方向",
  reached_count: "达线人数",
  candidates: "参考人数",
  reach_rate: "达线率",
  school_name: "学校名称",
  school_id: "学校代码",
  xx: "学校",
  xxlb: "校类",
  class_name: "班级",
  class: "班级",
  bj: "班级",
  student_id: "学号",
  anon_stu_id: "学号",
  student_name: "姓名",
  xm: "姓名",
  xsxz: "学生性质",
  subject: "科目",
  subject_name: "科目",
  score: "分数",
  exam_score: "满分",
  full_score: "满分",
  avg_score: "均分",
  avg: "均分",
  city_avg: "全市均分",
  city_rank: "全市排名",
  rank: "排名",
  rk: "排名",
  total_classes: "全市班级数",
  scope: "范围",
  ref_count: "参考人数",
  pass_rate: "及格率",
  excellent_rate: "优秀率",
  stdev: "标准差",
  std: "标准差",
  n_school: "学校数",
  school_count: "学校数",
  total_schools: "学校数",
  city_pct: "全市占比",
  n_class: "班级数",
  class_count: "班级数",
  gap: "分差",
  recorded_count: "录入人数",
  valid_count: "有效人数",
  total_score: "总分",
  average_score: "平均分",
  min_score: "最低分",
  max_score: "最高分",
  pass_count: "及格人数",
  classified_count: "已分类人数",
  own_class_mean: "本班均分",
  school_grade_mean: "年级均分",
  own_class_score: "本班得分",
  school_grade_score: "年级得分",
  pass_line: "及格线",
  excellent_line: "优秀线",
  max: "最高分",
  min: "最低分",
  n: "人数",
  cnt: "人数",
  count: "人数",
  total: "合计",
  zf6m: "六门总分",
  zf4m: "四门总分",
  zf3m: "三门总分",
  yw: "语文",
  sx: "数学",
  yy: "英语",
  wl: "物理",
  hx: "化学",
  sw: "生物",
  zz: "政治",
  ls: "历史",
  dl: "地理",
  hxzh: "化学转换分",
  swzh: "生物转换分",
  zzzh: "政治转换分",
  dlzh: "地理转换分",
  question_no: "题号",
  question_score: "题目满分",
  item_score: "小题得分",
  question_type: "题型",
  knowledge_name: "知识点",
  ability_level: "能力层级",
  score_rate: "得分率",
};

const SUBJECT_CODES = ["hxzh", "swzh", "zzzh", "dlzh", "yw", "sx", "yy", "wl", "hx", "sw", "zz", "ls", "dl"];
const METRIC_CODES = ["avg", "n", "cnt", "count", "rank", "rk"];

/** 语义层常见英文列名的词素，避免 KPI / 表头直接暴露 own_class_mean。 */
const PREFIX_LABELS: Record<string, string> = {
  own: "本校",
  own_class: "本班",
  class: "班级",
  school: "学校",
  school_grade: "年级",
  grade: "年级",
  city: "全市",
  district: "区县",
  county: "区县",
  region: "区域",
  subject: "科目",
  student: "学生",
  exam: "考试",
  exam_batch: "考试批次",
  recorded: "录入",
  valid: "有效",
  classified: "已分类",
  pass: "及格",
  total: "总",
  average: "平均",
  avg: "平均",
  min: "最低",
  max: "最高"
};

const MIDDLE_LABELS: Record<string, string> = {
  pass: "及格",
  excellent: "优秀",
  reach: "达线",
  class: "班级",
  grade: "年级",
  school: "学校",
  city: "全市",
  district: "区县",
  own: "本班"
};

const SUFFIX_LABELS: Record<string, string> = {
  mean: "均分",
  avg: "均分",
  score: "分数",
  rate: "率",
  ratio: "占比",
  percent: "百分比",
  count: "人数",
  num: "人数",
  n: "人数",
  gap: "分差",
  diff: "差值",
  rank: "排名",
  rk: "排名",
  total: "合计",
  max: "最高",
  min: "最低",
  std: "标准差",
  stdev: "标准差",
  sum: "合计",
  id: "编号",
  code: "编码",
  no: "编号"
};

/** 多词短语优先于单词，避免 own_class_mean → 「本班级均分」。 */
const PHRASE_LABELS: Record<string, string> = {
  own_class: "本班",
  school_grade: "年级",
  exam_batch: "考试批次",
  class_name: "班级",
  school_name: "学校",
  student_name: "姓名",
  subject_name: "科目",
  exam_name: "考试",
  total_score: "总分",
  average_score: "平均分",
  min_score: "最低分",
  max_score: "最高分",
  pass_count: "及格人数",
  recorded_count: "录入人数",
  valid_count: "有效人数",
  classified_count: "已分类人数",
  class_count: "班级数",
  school_count: "学校数",
  student_count: "学生人数",
  question_no: "题号",
  question_score: "题目满分",
  item_score: "小题得分"
};

/**
 * 单词词素表。模型生成的别名不固定，不能只靠 COLUMN_LABELS 白名单；
 * 这里覆盖教育问数常见词素，组合出中文标签，未知词素丢弃后回退原始列名。
 */
const TOKEN_LABELS: Record<string, string> = {
  own: "本",
  class: "班级",
  school: "学校",
  grade: "年级",
  city: "全市",
  district: "区县",
  county: "区县",
  region: "区域",
  exam: "考试",
  batch: "批次",
  student: "学生",
  teacher: "教师",
  subject: "科目",
  term: "学期",
  semester: "学期",
  year: "年份",
  month: "月份",
  date: "日期",
  time: "时间",
  day: "日",
  week: "周",
  total: "总",
  average: "平均",
  avg: "平均",
  mean: "均分",
  min: "最低",
  max: "最高",
  median: "中位",
  pass: "及格",
  excellent: "优秀",
  good: "良好",
  fail: "不及格",
  valid: "有效",
  invalid: "无效",
  recorded: "录入",
  classify: "分类",
  classified: "已分类",
  unclassified: "未分类",
  count: "人数",
  cnt: "人数",
  num: "数量",
  number: "数量",
  qty: "数量",
  amount: "金额",
  sum: "合计",
  rate: "率",
  ratio: "占比",
  percent: "百分比",
  pct: "占比",
  score: "分",
  scores: "分数",
  point: "分",
  points: "分",
  mark: "分数",
  marks: "分数",
  rank: "排名",
  rk: "排名",
  order: "名次",
  id: "编号",
  code: "编码",
  no: "编号",
  sn: "编号",
  uuid: "标识",
  key: "标识",
  name: "名称",
  title: "标题",
  type: "类型",
  status: "状态",
  level: "层级",
  tier: "层级",
  label: "标签",
  description: "说明",
  metric: "指标",
  value: "数值",
  flag: "标记",
  knowledge: "知识点",
  question: "题目",
  ability: "能力",
  difficulty: "难度",
  topic: "专题",
  reach: "达线",
  line: "线",
  target: "目标",
  gap: "分差",
  diff: "差值",
  delta: "变化",
  growth: "增长",
  increase: "增长",
  decrease: "下降",
  progress: "进步",
  regression: "退步",
  high: "高",
  low: "低",
  middle: "中",
  first: "首",
  last: "末",
  previous: "上期",
  current: "本期",
  custom: "自定义"
};

function normalizeKey(raw: string): string {
  return raw
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[\s-]+/g, "_")
    .toLowerCase();
}

function translateByTokens(key: string): string {
  const tokens = key.split("_").filter(Boolean);
  let output = "";
  for (let index = 0; index < tokens.length; ) {
    let matched = false;
    for (let length = Math.min(3, tokens.length - index); length >= 1; length -= 1) {
      const phrase = tokens.slice(index, index + length).join("_");
      const label = PHRASE_LABELS[phrase] || (length === 1 ? TOKEN_LABELS[phrase] : undefined);
      if (!label) continue;
      output += label;
      index += length;
      matched = true;
      break;
    }
    if (!matched) index += 1;
  }
  // mean→均分 再拼 score→分 会出现「均分分」,压掉重复字尾
  return output.replace(/分分$/, "分");
}

function composeSemanticName(key: string): string | null {
  const tokens = key.split("_").filter(Boolean);
  if (tokens.length < 2) return null;
  for (let index = tokens.length - 1; index >= 1; index -= 1) {
    const prefix = PREFIX_LABELS[tokens.slice(0, index).join("_")];
    const suffix = SUFFIX_LABELS[tokens[tokens.length - 1]];
    if (!prefix || !suffix) continue;
    const middle = tokens.slice(index, -1);
    if (middle.some((token) => !MIDDLE_LABELS[token])) continue;
    return `${prefix}${middle.map((token) => MIDDLE_LABELS[token]).join("")}${suffix}`;
  }
  return null;
}

function composeSubjectMetric(subjectCode: string, metricCode: string): string | null {
  const subject = COLUMN_LABELS[subjectCode];
  const metric = COLUMN_LABELS[metricCode];
  if (!subject || !metric) return null;
  return `${subject}${metric}`;
}

export function labelColumn(name: string): string {
  const raw = (name || "").trim();
  if (!raw) return raw;
  const key = normalizeKey(raw.split(".").pop() || raw);
  if (COLUMN_LABELS[key]) return COLUMN_LABELS[key];

  for (const metric of METRIC_CODES) {
    if (key.startsWith(`${metric}_`)) {
      const composed = composeSubjectMetric(key.slice(metric.length + 1), metric);
      if (composed) return composed;
    }
  }

  const semantic = composeSemanticName(key);
  if (semantic) return semantic;

  const mixed = raw.match(/^([\u4e00-\u9fff]+)([a-z]{2,4})$/i);
  if (mixed) {
    const prefix = mixed[1];
    const code = mixed[2].toLowerCase();
    const subject = COLUMN_LABELS[code];
    if (subject && SUBJECT_CODES.includes(code)) return `${subject}${prefix}`;
  }

  // 兜底:已知词素组合出中文,译不出来就保留原始列名。
  // 早先这里返回常量「字段」,导致 c1/c2/c3 这类占位别名全部渲染成同一个词,
  // 列与列彻底无法区分(图表标题因此出现「字段、字段、字段」)。
  const translated = translateByTokens(key);
  if (translated) return translated;
  return raw;
}

/**
 * 批量翻译列名,并消歧:翻译结果重名时补上原始列名,保证表头两两不同。
 * 单个列名请用 labelColumn。
 */
export function labelColumns(names: string[]): string[] {
  const labels = names.map((name) => labelColumn(name));
  const counts = new Map<string, number>();
  labels.forEach((label) => counts.set(label, (counts.get(label) ?? 0) + 1));
  return labels.map((label, index) => {
    if ((counts.get(label) ?? 0) <= 1) return label;
    const raw = (names[index] || "").trim();
    return raw && raw !== label ? `${label}(${raw})` : `${label}#${index + 1}`;
  });
}
