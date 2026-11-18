# 学情总览按权限分层设计

## 1. 背景

当前 `/` 学情总览把“学校达线率排名”当作所有账号的主图：

- 前端按 `districts[].schools` 生成学校序列（[overview.tsx:58-66](../frontend-pro/src/pages/overview.tsx#L58-L66)）；
- 后端对 `school_admin` / `teacher` 会清空 `schools`（[api.py:1429-1431](../src/agent/education/api.py#L1429-L1431)、[api.py:1492-1496](../src/agent/education/api.py#L1492-L1496)）；
- 单校、年级、班级账号因此得到空图，并显示“当前账号仅覆盖本校数据，学校排名需要多所学校范围”。

同时，班级级账号无法回答“本班在年级/理科班中排第几”，因为班级级授权只覆盖本班，`grade_baseline` 只释放年级整体基准，没有班级间排名能力。

目标不是给每个角色复制一份页面，而是**用同一路由承载“当前权限范围内有意义的下一层排名/明细”**：

- 市级 → 区县/学校排名
- 区县级 → 本区学校排名
- 学校级 → 本校班级排名
- 年级级 → 本年级班级排名
- 班级级 → 本班学生分布/排名
- 学生级 → 个人趋势/各科

## 2. 设计原则

1. **服务端决定视角**：`level`、`chart.dimension`、允许的筛选都由后端根据 `ReportScope` 推导，前端不传 `level`，避免越权试探。
2. **一个页面，多套 profile**：共享标题、筛选、KPI、异常、空态组件；按 profile 切换主图和明细表，不复制页面。
3. **排名只排“下一层”**：有多个可比单位才排名；只有一个单位时返回该单位并提示“仅 1 个班级/学校，排名无意义”，不要返回空图。
4. **范围内聚合不需要 capability**：学校/年级看本校/本年级班级排名，属于授权范围内的普通聚合；`grade_baseline` 只用于班级级用户对照年级整体，不用于班级排名。
5. **班级级不排名其他班级**：班级级只显示本班学生分布/排名；需要班级间排名时，提示升级为年级/学校级授权，或新增 `class_rank` 能力。
6. **隐私不变**：学生明细只使用 `anon_stu_id`；`xm/sfzh/ksh` 仍禁止；结果层继续脱敏。
7. **无数据与无权限分开**：空态文案区分“无成绩”“只有 1 个单位”“未开通能力”“无授权”，不复用一句“本校数据”。

## 3. 权限 → 总览 profile

| level | 识别 | 标题/范围徽标 | KPI | 主图 | 明细表 | 次级模块 | 明确不展示 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `city` | `citywide=true` 或 `levels` 含 `city` | 全市学情总览 / 全市 | 全市考生、特控线、本科线、待关注异常 | 区县达线率排名，可下钻学校 | 区县 + 学校 | 全市报告 | — |
| `district` | `levels` 含 `district`，不含更高 | XX区学情总览 / 本区 | 本区考生、特控线、本科线、异常 | 本区学校达线率排名 | 学校 | 区县报告 | 其他区县 |
| `school` | `levels` 含 `school`，不含更高 | XX中学学情总览 / 本校 | 本校考生、特控线、本科线、异常 | 本校**班级**排名（达线率/均分/及格率） | 班级 | 年级分布、异常 | 其他学校 |
| `grade` | `levels` 含 `grade`，不含更高 | XX中学2026届学情总览 / 本年级 | 本年级考生、特控线、本科线、异常 | 本年级**班级**排名 | 班级 | 异常 | 其他届次、其他学校 |
| `class` | `levels` 含 `class`，不含更高 | 高三(1)班学情总览 / 本班 | 本班考生、特控线、本科线、异常 | **本班学生**分数段/班内排名 + 学科均分 | 学生（匿名） | 本班 vs 年级（需 `grade_baseline`） | 其他班级、其他学校、班级排名 |
| `student` | `levels` 仅 `student` | 学生个人学情 / 本人 | 个人总分、各科、班内排名 | 历次考试趋势/学科雷达 | 个人成绩 | — | 他人、班级/学校明细 |

补充：

- 多层级授权取**最高层级**作为主视角；多学校（`schools.length > 1`）时增加学校选择器，选中后展示该校班级排名；多班级（`class_names.length > 1`）时增加班级选择器。
- 旧角色映射：`bureau_admin → city`、`school_admin → school`、`teacher → class`、`student → student`；有 v2 `levels` 时以 `levels` 为准。
- `school` 且有多个届次时增加“届次”筛选；`grade` 的届次固定，不提供切换。

## 4. 页面结构

统一 shell：

```
Header: [范围徽标] [标题] [范围说明] [刷新]
FilterBar: 考试 / 科类 / 分数线 / （届次|学校|班级|科目按 profile）
KpiRow: 4 张卡（覆盖学生、特控线、本科线、待关注异常）
Main: 左图 + 右排名/明细（按 profile 切换）
Secondary: 学科均分 / 分数段 / 年级对照 / 异常列表（按 profile）
Actions: 本范围报告、班级报告、学生画像、异常入口
Notice: single_group / capability_missing / no_data / no_scope
```

关键变化：

- 主图不再固定“学校排名”，而是由 `chart.dimension` 决定：`district`、`school`、`class`、`student`、`exam`。
- 只有一个单位时`chart.rows`仍返回该单位，前端画单柱 + `Notice(single_group)`，不再进入空态。
- 班级级页面不出现“学校排名”Tab；班级排名请求改为提示“需年级/学校级授权”，并给出本班学生分布图作为替代。
- 学校/年级页面的班级排名严格限定在授权范围内；不允许通过筛选看到其他学校/届次班级。

## 5. 后端接口

新增 `GET /api/v1/education/overview`，不复用并改写 `/dashboards/line-reach`，避免影响现有达线看板。

### 5.1 请求

```
GET /api/v1/education/overview
  ?exam_name=2026届高三11月期中
  &track=物理类
  &line_key=tz
  &cohort=2026届
  &class_name=高三(1)班
  &subject=数学
```

`level` 不是请求参数，由后端根据当前用户 grants 推导。传入不存在的筛选值按无数据处理或忽略，不改变范围。

### 5.2 响应

```json
{
  "level": "school",
  "scope": {
    "label": "XX中学",
    "levels": ["school"],
    "schools": ["A01XX中学"],
    "cohorts": ["2026届"],
    "classes": ["高三(1)班", "高三(2)班"],
    "capabilities": ["grade_baseline"],
    "multi_school": false,
    "multi_class": true
  },
  "filters": {
    "exams": ["2026届高三11月期中"],
    "tracks": ["物理类", "历史类"],
    "lines": [{"line_key": "tz", "label": "特控线", "threshold": 520}],
    "cohorts": ["2026届"],
    "classes": ["高三(1)班", "高三(2)班"],
    "subjects": ["语文", "数学", "英语"]
  },
  "kpis": {
    "candidates": 820,
    "by_line": [{"line_key": "tz", "label": "特控线", "reached": 300, "rate": 36.6}]
  },
  "chart": {
    "dimension": "class",
    "metric": "rate",
    "rows": [
      {"id": "高三(1)班", "label": "高三(1)班", "candidates": 52,
       "by_line": [{"line_key": "tz", "reached": 52, "rate": 100}]}
    ]
  },
  "table": {
    "dimension": "class",
    "columns": ["班级", "参考人数", "均分", "特控率", "本科率"],
    "rows": [["高三(1)班", 52, 602.3, 100, 100]]
  },
  "secondary": {
    "dimension": "grade",
    "rows": []
  },
  "notices": [],
  "actions": [
    {"label": "班级报告", "href": "/construct/analysis?report_type=class_overview"},
    {"label": "全校报告", "href": "/construct/analysis?report_type=grade_comparison"}
  ]
}
```

### 5.3 视角推导

```text
level = highest(scope.levels)  # city > district > school > grade > class > student
dimension = {
  city: "district",
  district: "school",
  school: "class",
  grade: "class",
  class: "student",
  student: "exam"
}[level]
```

- 若 `level=school` 且 `schools.length > 1`：`dimension=school`，先展示多校对比，选中学校后再请求 `dimension=class`。
- 若 `level=class` 且 `classes.length > 1`：`class_name` 由选择器传入，只允许 `scope.classes` 内的值。
- `level=grade` 的 `cohort` 固定为 grants 的 cohort；请求其他届次直接无数据/拒绝。

### 5.4 数据来源

| dimension | SQL 来源 | 说明 |
| --- | --- | --- |
| `district` / `school` | 现有 `/dashboards/line-reach` 的 `_overview_agg_sql` | 保持现有口径 |
| `class` | `tb_score_overview` 按 `bj` 分组，`ReportScope.apply_sql` 下推 `xx + jc`（学校/年级）或 `xx + jc + bj`（班级） | 新增 `payload_from_class_agg` |
| `student` | `tb_score_overview` 按 `anon_stu_id` 或分数段分组 | 班级级只返回本班；匿名标识 |
| `exam` | `tb_score_overview` 按 `exam_batch_id` 分组 | 学生级趋势 |

`ReportScope` 已能按 grants 下推范围（[education_report_scope.py:362-435](../src/datasource/service/education_report_scope.py#L362-L435)），新增分组只改 SELECT/GROUP BY，不改权限谓词。

### 5.5 空态与提示

`notices` 使用稳定 code，前端只负责文案：

| code | 触发 | 前端表现 |
| --- | --- | --- |
| `single_group` | 排名维度只有 1 个单位 | 画单柱/单行，提示“仅 1 个班级/学校，排名无意义” |
| `no_peers` | 没有可比单位 | 展示本范围 KPI + 明细入口 |
| `capability_missing` | 班级级无 `grade_baseline` | 隐藏年级对照，展示“申请开通” |
| `no_data` | 该考试/筛选无成绩 | “该考试暂无成绩数据” |
| `no_scope` | 无有效 grants | 拒绝并跳转权限说明 |

## 6. 前端实现

推荐目录：

```
frontend-pro/src/pages/overview/
  index.tsx                 # 单页 shell + 数据请求
  profiles.ts               # level -> 标题/KPI/筛选项/图表/动作
  components/ScopeBadge.tsx
  components/OverviewKpis.tsx
  components/OverviewChart.tsx   # dimension 分派
  components/OverviewTable.tsx
  components/OverviewNotice.tsx
  components/ScopeActions.tsx
```

- `profiles.ts` 只做展示配置，不参与权限判定；`level` 与 `chart.dimension` 以后端响应为准。
- `OverviewChart` 支持 `district`/`school`/`class`/`student`/`exam` 五种形态，学校/班级/学生共用柱状/表格基础组件。
- 删除 `singleSchoolScope` 及“需要多所学校范围”的硬编码空态，改用 `notices`。
- 城市/区县保留现有视觉；学校/年级主图改为班级排名；班级主图改为学生分数段 + 学科均分；学生主图改为趋势。
- 在 KPI 区增加范围徽标（全市/本区/本校/本年级/本班/本人），消除“本校”误用。

## 7. 权限与安全

- 所有查询走 `ReportScope.apply_sql`；班级/年级分组在授权范围内完成，不新增越权读取。
- 学校/年级的班级排名不需要 capability；班级级的年级对照仍只通过 `grade_baseline` 的整体基准，不返回其他班逐班值。
- 学生明细只取 `anon_stu_id`；`xm/sfzh/ksh` 禁止；`apply_result_privacy` 继续生效。
- 不接受客户端传 `level`/`group_by` 改变数据范围；只接受筛选项，且筛选项必须在 `scope` 内。
- 旧账号按 `edu_role` 映射；无权限账号不进入总览。

## 8. 验收用例

1. 市级账号：区县排名、学校下钻、全市 KPI；不能只看到本校。
2. 区县账号：本区学校排名；不出现其他区县学校。
3. 学校账号：本校班级排名，覆盖所有届次/班级；不出现其他学校；只有一个班时单柱 + `single_group`。
4. 年级账号：本年级班级排名；不出现其他届次/学校；只有一个班时单柱 + `single_group`。
5. 班级账号：本班学生分数段/班内排名；不出现班级排名 Tab；无 `grade_baseline` 时隐藏年级对照。
6. 学生账号：只返回本人；无班级/年级明细。
7. 无 grants：`no_scope`，不返回数据。
8. 多学校/多班级账号：选择器只列出授权范围值；切换不扩大范围。
9. 隐私：学生明细无 `xm/sfzh/ksh`，匿名 ID 可用。
10. 回归：`/dashboards/line-reach` 原页面行为不变。

## 9. 分期

| 阶段 | 内容 | 交付价值 |
| --- | --- | --- |
| P0 | 前端按 `edu_scope.levels` 显示正确范围徽标/空态；非城市/区县隐藏学校排名空态，改为 KPI + 明细入口 | 快速消除“空图”误解，不改后端 |
| P1 | 新增 `/education/overview`；学校/年级按班级聚合；前端 profile 化 + 班级排名图 | 解决学校/年级主图无数据；学校/年级可看班级对比 |
| P2 | 班级级学生分数段/班内排名；`grade_baseline` 对照卡；异常/报告动作 | 解决班级级总览；补齐能力提示 |
| P3 | 学生级趋势；多学校/多班级选择器；灰度与回滚 | 完整分层总览 |

## 10. 待确认

1. 目标前端是 `frontend-pro`（截图）还是 `frontend-react`，或两端同步。
2. 是否允许新增 `/education/overview` 后端接口（推荐），还是只扩展现有 `line-reach`。
3. 学校/年级的班级排名是否需要 **达线率、均分、及格率** 三种指标，还是先做达线率。
4. 班级级学生明细是否只显示匿名 `anon_stu_id`（当前隐私策略），还是需要按权限开放姓名。
5. 是否需要学生个人总览页，还是学生账号继续走个人报告。

## 11. 当前实现状态（2026-11-18）

已按 P1+P2 落地第一版：

- 后端新增 `GET /api/v1/education/overview`，代码在 `src/agent/education/overview_api.py`；纯函数在 `src/agent/education/overview.py`。
- 视角由 `ReportScope` 推导：市级/区县按学校，学校/年级按班级，班级按学生，学生按考试。
- 学校/年级的班级排名、班级学生分数段/班内排名、`grade_baseline` 本班 vs 年级对照已接入。
- 班级级不展示班级间排名；无 `grade_baseline` 时返回 `capability_missing` 提示。
- 单校/单班不再返回空排名：`chart.rows` 保留唯一单位，并返回 `single_group` 提示。
- 前端 `frontend-pro/src/pages/overview.tsx` 对 v2 账号使用 `ScopeOverview`，旧版权限继续使用 `LegacyOverview`；新增 `src/api/educationOverview.ts`。
- 测试：`tests/agent/test_education_overview.py` 覆盖学校、年级、班级（含/不含 `grade_baseline`）四种视角。

当前仍待真实库灰度验收：`tb_score_overview` 的 `bj`/`jc`/`xkkm` 实际取值、分数段满分、以及多届次同名班级的展示。
