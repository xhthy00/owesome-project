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


__all__ = ["encode_school_name"]

