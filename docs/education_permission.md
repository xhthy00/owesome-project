# 教育数据权限（V1 已停用）

> **状态：V1 已下线（2026-11-23）。** 本文保留标题只为兼容旧链接。

V1 通过 `sys_user.system_variables` 的四级角色（`bureau_admin` / `school_admin` / `teacher` / `student`）
在运行时向 SQL 注入行级谓词。该实现已整体删除：

- 接口：`/api/v1/permission/edu/{roles,schools,template,batch-bind,effective}`、`/user/{id}/edu-scope`。
- 服务：`datasource/service/edu_permission.py`（角色模板、谓词编译、校名解析、批量绑定）、`config/education_permission.json`。
- 运行时：`apply_permissions_for_execute` 中的教育谓词注入、`strip_city_scope_xx_filters` /
  `expand_*_school_code_literals` 等“聚合放宽”改写、`scope_guard` 问句预拒绝。
- 前端：教育权限页旧面板（`LegacyEduPermissions`）、V1 API 方法、用户管理“教育角色”列。

教育数据权限现在的唯一事实源是 **V2 资源绑定授权**：`sys_user.system_variables.edu_auth_v2`。
配置入口为「权限管理 → 教育权限」（`/api/v1/permission/edu/v2/*`），实现与启用说明见
[education_permission_v2.md](education_permission_v2.md)；迁移与删除过程见
[education_permission_v1_retirement_plan.md](education_permission_v1_retirement_plan.md)。

没有 V2 授权的账号一律拒绝（失败关闭），不会再回退到旧的角色模板。
`datasource/service/edu_scope_view.py::EduScope` 只是展示用的只读视图对象，不参与授权判断。
