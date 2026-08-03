"""学校名称不可逆短混淆。

需求：edu 库 ``tb_school.name`` 原存学校中文全称明文，web 平台展示与问数结果
会暴露校名。将 ``name`` 改存为「看不出原文、短、不可逆」的混淆 token，作为
``tb_school.id`` 与 ``tb_school.name`` 共用值（数据库为测试数据可随时清空，
外键 ``tb_score.school_id`` 同步存同一 token 即可一致）。

方案：学段前缀 + HMAC-SHA256(secret, 校名) 取中段 8 hex → ``gz_2d2b5c7b`` 形 token。
- 不可逆：HMAC 单向，token 泄露推不出校名；
- 确定性：同一校名 + 同一 secret 永远同一 token，入库一次即可；
- 短：11 字符（3 前缀 + 8 hex），字符集 ``[a-z0-9_]``，命中
  ``tools._looks_like_school_id`` 正则 ``^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$``，
  过滤自动走 ``sc.school_id =``；
- 学段前缀：``gz_``/``cz_``/``xx_`` 按学段分类，只暴露学段类别不暴露校名；
- hex 部分无固定前缀：取 SHA256 hex 中段，首字符 16 种全分散，无人工痕迹。

注意：
- 同名校名（如完中高中部/初中部同名）会生成同一 hex，但因学段前缀不同，
  高中/初中 token 仍可区分（如 ``gz_ca4b627d`` vs ``cz_ca4b627d``）；
- 本方案为「轻度不可逆混淆」，secret 为简单固定串，适用于「客户不希望一眼看出
  校名 / id 映射」的威胁模型，不适用于对抗能拿到 secret 的攻击者。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

_DEFAULT_SECRET = b"yz_edu_k1"
_TOKEN_HEX_LEN = 8  # 8 hex = 32 bit；107 所无真实碰撞（同名校名共享 hex 合理）
_HEX_START = 12  # 取 SHA256 hex 中段，避开首尾边界

#: 学段 → 前缀。只暴露学段类别，不暴露校名。
_STAGE_PREFIX = {
    "小学": "xx_",
    "初中": "cz_",
    "高中": "gz_",
}


def _resolve_secret(secret: str | bytes | None) -> bytes:
    if secret is None:
        env = os.environ.get("SCHOOL_CIPHER_KEY")
        secret = env if env else _DEFAULT_SECRET
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return secret


def encode_school_name(
    name: str,
    *,
    stage: str = "",
    secret: str | bytes | None = None,
) -> str:
    """校名 → 不可逆短 token，如 ``江苏省高邮中学(高中) → gz_2d2b5c7b``。

    Args:
        name: 学校中文全称（明文）。
        stage: 学段（``小学``/``初中``/``高中``）；命中则加对应前缀，未命中则无前缀。
        secret: 混淆密钥；默认取环境变量 ``SCHOOL_CIPHER_KEY``，再退回 ``yz_edu_k1``。

    Returns:
        ``学段前缀 + 8 hex``（如 ``gz_2d2b5c7b``），命中 ``_looks_like_school_id`` 正则。
    """
    sec = _resolve_secret(secret)
    digest = hmac.new(sec, str(name).encode("utf-8"), hashlib.sha256).hexdigest()
    hex_part = digest[_HEX_START : _HEX_START + _TOKEN_HEX_LEN]
    prefix = _STAGE_PREFIX.get(str(stage or "").strip(), "")
    return prefix + hex_part


_S_NAME_IDENT = re.compile(
    r"\b([A-Za-z_][\w]*\.)?s_name\b",
    re.IGNORECASE,
)


def _map_sql_outside_string_literals(sql: str, transform) -> str:
    """只改写 SQL 标识符区域，字符串字面量原样保留。"""
    parts: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            q = ch
            j = i + 1
            while j < n:
                if sql[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if sql[j] == q:
                    # SQL 标准：'' 转义单引号
                    if q == "'" and j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            parts.append(sql[i:j])
            i = j
            continue
        j = i
        while j < n and sql[j] not in "'\"":
            j += 1
        parts.append(transform(sql[i:j]))
        i = j
    return "".join(parts)


def rewrite_sql_school_s_name(sql: str) -> tuple[str, bool]:
    """将 SQL 中的 ``s_name`` 标识符改写为 ``name``（保留表别名）。

    edu 库 ``tb_school.name`` 已为脱敏码，``s_name`` 仍可能存中文全称。
    Agent 经 describe/sample 发现 ``s_name`` 后会 SELECT 明文；此处在执行前强制改写。

    Returns:
        (rewritten_sql, changed)
    """
    text = str(sql or "")
    if not text or "s_name" not in text.lower():
        return text, False

    changed = False

    def _chunk(chunk: str) -> str:
        nonlocal changed

        def _sub(m: re.Match[str]) -> str:
            nonlocal changed
            changed = True
            prefix = m.group(1) or ""
            return f"{prefix}name"

        return _S_NAME_IDENT.sub(_sub, chunk)

    out = _map_sql_outside_string_literals(text, _chunk)
    return out, changed


def strip_s_name_from_query_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 ``SELECT *`` / 采样结果中去掉 ``s_name`` 列，避免明文回灌给 LLM。"""
    if not isinstance(result, dict):
        return result
    columns = list(result.get("columns") or [])
    rows = list(result.get("rows") or [])
    drop_idx = [i for i, c in enumerate(columns) if str(c).lower() == "s_name"]
    if not drop_idx and not any(str(c).lower() == "s_name" for c in columns):
        # dict rows may still carry s_name
        if rows and isinstance(rows[0], dict):
            if not any("s_name" in {str(k).lower() for k in r} for r in rows if isinstance(r, dict)):
                return result
            new_rows = [
                {k: v for k, v in r.items() if str(k).lower() != "s_name"}
                if isinstance(r, dict)
                else r
                for r in rows
            ]
            out = dict(result)
            out["rows"] = new_rows
            return out
        return result

    drop = set(drop_idx)
    new_columns = [c for i, c in enumerate(columns) if i not in drop]
    new_rows: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            new_rows.append({k: v for k, v in row.items() if str(k).lower() != "s_name"})
        elif isinstance(row, (list, tuple)):
            new_rows.append([v for i, v in enumerate(row) if i not in drop])
        else:
            new_rows.append(row)
    out = dict(result)
    out["columns"] = new_columns
    out["rows"] = new_rows
    return out


__all__ = [
    "encode_school_name",
    "rewrite_sql_school_s_name",
    "strip_s_name_from_query_result",
]

