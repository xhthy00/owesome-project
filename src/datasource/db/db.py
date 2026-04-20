"""Database connection testing and execution."""

from typing import Any, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def test_db_connection(db_type: str, config: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    """
    Test database connection.

    Returns:
        (success, message, version)
    """
    if db_type == "pg":
        return test_postgresql_connection(config)
    elif db_type == "mysql":
        return test_mysql_connection(config)
    else:
        return False, f"Unsupported database type: {db_type}", None


def test_postgresql_connection(config: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    """Test PostgreSQL connection."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=config.get("host", "localhost"),
            port=config.get("port", 5432),
            user=config.get("username", "postgres"),
            password=config.get("password", ""),
            database=config.get("database", "postgres"),
            connect_timeout=config.get("timeout", 30),
        )

        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return True, "Connection successful", version
    except ImportError:
        return False, "psycopg2 not installed", None
    except Exception as e:
        return False, f"Connection failed: {str(e)}", None


def test_mysql_connection(config: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    """Test MySQL connection."""
    try:
        import pymysql

        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=config.get("port", 3306),
            user=config.get("username", "root"),
            password=config.get("password", ""),
            database=config.get("database", ""),
            connect_timeout=config.get("timeout", 30),
        )

        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return True, "Connection successful", version
    except ImportError:
        return False, "pymysql not installed", None
    except Exception as e:
        return False, f"Connection failed: {str(e)}", None


def execute_sql(db_type: str, config: Dict[str, Any], sql: str) -> Tuple[bool, str, Any]:
    """
    Execute SQL on database.

    Returns:
        (success, message, result)
    """
    if db_type == "pg":
        return execute_postgresql_sql(config, sql)
    elif db_type == "mysql":
        return execute_mysql_sql(config, sql)
    else:
        return False, f"Unsupported database type: {db_type}", None


def execute_postgresql_sql(config: Dict[str, Any], sql: str) -> Tuple[bool, str, Any]:
    """Execute SQL on PostgreSQL."""
    try:
        import psycopg2
        import json

        conn = psycopg2.connect(
            host=config.get("host", "localhost"),
            port=config.get("port", 5432),
            user=config.get("username", "postgres"),
            password=config.get("password", ""),
            database=config.get("database", "postgres"),
            connect_timeout=config.get("timeout", 30),
        )

        cursor = conn.cursor()
        cursor.execute(sql)

        # Check if it's a SELECT query
        if cursor.description:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
            }
        else:
            conn.commit()
            result = {"row_count": cursor.rowcount}

        cursor.close()
        conn.close()

        return True, "Success", result
    except Exception as e:
        return False, f"SQL execution failed: {str(e)}", None


def execute_mysql_sql(config: Dict[str, Any], sql: str) -> Tuple[bool, str, Any]:
    """Execute SQL on MySQL."""
    try:
        import pymysql

        conn = pymysql.connect(
            host=config.get("host", "localhost"),
            port=config.get("port", 3306),
            user=config.get("username", "root"),
            password=config.get("password", ""),
            database=config.get("database", ""),
            connect_timeout=config.get("timeout", 30),
        )

        cursor = conn.cursor()
        cursor.execute(sql)

        # Check if it's a SELECT query
        if cursor.description:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
            }
        else:
            conn.commit()
            result = {"row_count": cursor.rowcount}

        cursor.close()
        conn.close()

        return True, "Success", result
    except Exception as e:
        return False, f"SQL execution failed: {str(e)}", None
