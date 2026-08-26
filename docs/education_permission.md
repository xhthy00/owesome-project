# 教育四级数据权限（方案 A）

## 概述

在不新建组织树表的前提下，通过 `sys_user.system_variables` 存储教育角色与数据范围，运行时按模板自动向 SQL 注入行级过滤条件。

四级角色：

| 角色 | `edu_role` | 数据范围 |
|------|------------|----------|
| 教育局（全市） | `bureau_admin` | 数据源内全量（不注入谓词） |
| 学校（校长） | `school_admin` | `school_id = 本校` |
| 班级（老师） | `teacher` | `school_id = 本校 AND class IN (...)` |
| 学生 | `student` | `student_id = 本人学号` |

与现有 **数据权限规则组**（`ds_rules` / `ds_permission`）**叠加**：两者谓词会以 AND 方式合并。

## 配置方式

### 1. 前端「教育权限」页

路径：**权限管理 → 教育权限**

- **单用户配置**：选择用户 → 角色 → 填写学校/班级/学号 → 保存
- **批量导入**：下载 CSV 模板 → 上传 → 执行批量绑定

### 2. API

| 接口 | 说明 |
|------|------|
| `GET /api/v1/permission/edu/roles` | 四级角色定义 |
| `GET /api/v1/user/{id}/edu-scope` | 读取用户教育范围 |
| `PUT /api/v1/user/{id}/edu-scope` | 更新用户教育范围 |
| `POST /api/v1/permission/edu/batch-bind` | CSV/JSON 批量绑定 |
| `POST /api/v1/permission/edu/effective` | 预览生效谓词与合并 SQL |
| `GET /api/v1/permission/edu/template` | 下载 CSV 模板 |

### 3. CSV 模板

```csv
account,edu_role,school_id,school_name,class_names,student_id
zhang_principal,school_admin,1,南京市第一中学,,
li_teacher,teacher,1,南京市第一中学,高一(1)班|高一(2)班,
wang_student,student,1,南京市第一中学,,STU20240002
bureau_user,bureau_admin,,,,
```

## system_variables 结构

```json
{
  "edu_role": "teacher",
  "school_id": "1",
  "school_name": "南京市第一中学",
  "class_names": ["高一(1)班", "高一(2)班"],
  "student_id": "STU20240002"
}
```

## 运行时行为

1. Chat / Agent `execute_sql` 与列裁剪链路不变。
2. `apply_permissions_for_execute` 在 `ds_rules` 谓词之后追加 edu 模板谓词。
3. **校长/老师**：成绩**明细**（名单、逐人分数）仍注入本校（老师再限班）。**全市/区县合计**（`AVG`/`COUNT`/`SUM`、`GROUP BY dq`）不注入学校谓词。点名他校、`GROUP BY xx` 各校排名仍注入本校（他校为 0 行或只剩本校）。学生角色不开放全市。
4. 教育报告工具通过 `user_id` binding 走同一权限链路。

配置文件：[`config/education_permission.json`](../config/education_permission.json)

核心模块：[`src/datasource/service/edu_permission.py`](../src/datasource/service/edu_permission.py)、[`src/datasource/service/query_permission.py`](../src/datasource/service/query_permission.py)

## 手工验收

1. 为测试用户配置 `school_admin` + `school_id=SCH001`（字符串），预览 effective 应含 `"school_id" = 'SCH001'`
2. 配置 `teacher` + 两个班级，谓词应含 `class IN (...)`
3. 配置 `student` + 学号，谓词应含 `student_id = '...'`
4. `bureau_admin` 预览谓词为空
5. 校长问本校学生名单：结果只有本校
6. 校长问全市均分：人数应明显大于本校，SQL 全市支无 `xx`/`school_id`
7. 校长问他校均分或 `GROUP BY xx`：不应出现其他学校

## 限制

- 谓词合并时会按 SQL 中表别名自动限定列名：`school_id`/`class` 仅挂 `tb_score`（sc）；
  `student_id` 优先 sc，仅查 `tb_student` 时用 `st.id`；仅查 `tb_score_detail` 时
  `school_id`/`class` 经 EXISTS 关联 sc。查询须 JOIN `tb_score sc`，`tb_student` 用 `st.id` 关联学号。
- 平台 `admin` 账号仍跳过全部行级过滤（与现有行为一致）
