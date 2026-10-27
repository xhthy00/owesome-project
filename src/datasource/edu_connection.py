"""问数连接参数：只解析 ``DATABASE_EDU_URL``，不读数据源表里的加密配置。"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit


def parse_edu_database_url(url: str) -> tuple[str, dict[str, Any]]:
    """把连接串解析成 ``(db_type, config)``。``config`` 含 host/port/username/password/database。"""
    parsed = urlsplit((url or "").strip())
    scheme = (parsed.scheme or "").split("+", 1)[0].lower()
    if scheme in {"postgresql", "postgres"}:
        db_type = "pg"
        default_port = 5432
    elif scheme == "mysql":
        db_type = "mysql"
        default_port = 3306
    else:
        raise ValueError("DATABASE_EDU_URL 仅支持 postgresql")
    database = parsed.path.lstrip("/").split("/", 1)[0]
    if not parsed.hostname or not database:
        raise ValueError("DATABASE_EDU_URL 缺少主机或库名")
    return db_type, {
        "host": parsed.hostname,
        "port": parsed.port or default_port,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


def edu_db_connection() -> tuple[str, dict[str, Any]]:
    """问数使用的库类型和连接参数。未配置或格式不对时抛 ``ValueError``。"""
    from src.common.core.config import get_settings

    url = (get_settings().database_edu_url or "").strip()
    if not url:
        raise ValueError("未配置 DATABASE_EDU_URL")
    return parse_edu_database_url(url)
