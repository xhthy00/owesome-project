"""只读的旧视图对象：仅用于展示与既有函数入参，**不参与任何授权判断**。

教育权限的唯一事实源是 ``sys_user.system_variables.edu_auth_v2``（见
``education_authorization`` / ``education_report_scope``）。V1 的角色配置、
谓词注入、校名解析与批量绑定已随 V1 下线删除；这里保留的字段只为兼容
仍然接收 ``EduScope`` 形状参数的展示型函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EduScope:
    edu_role: str = ""
    school_id: str = ""
    school_name: str = ""
    class_names: list[str] = field(default_factory=list)
    student_id: str = ""
    remark: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.edu_role:
            out["edu_role"] = self.edu_role
        if self.school_id:
            out["school_id"] = self.school_id
        if self.school_name:
            out["school_name"] = self.school_name
        if self.class_names:
            out["class_names"] = list(self.class_names)
        if self.student_id:
            out["student_id"] = self.student_id
        if self.remark:
            out["edu_remark"] = self.remark
        return out



__all__ = ["EduScope"]
