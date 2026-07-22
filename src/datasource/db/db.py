"""Database connection testing and execution based on SQLBot patterns."""

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, date, time, timedelta
from decimal import Decimal
import logging
import base64

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


class WriteDbSession:
    """受控写库会话：供成绩导入等内部模块使用，事务由调用方 commit/rollback。"""

    def __init__(self, db_type: str, config: Dict[str, Any]):
        self.db_type = db_type
        self.config = config
        self._conn: Any = None
        self._cursor: Any = None

    def connect(self) -> None:
        if self._conn is not None:
            return
        if self.db_type == "pg":
            import psycopg2

            self._conn = psycopg2.connect(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 5432),
                user=self.config.get("username", "postgres"),
                password=self.config.get("password", ""),
                database=self.config.get("database", "postgres"),
                connect_timeout=self.config.get("timeout", 30),
            )
        elif self.db_type == "mysql":
            import pymysql

            self._conn = pymysql.connect(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 3306),
                user=self.config.get("username", "root"),
                password=self.config.get("password", ""),
                database=self.config.get("database", ""),
                connect_timeout=self.config.get("timeout", 30),
            )
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")
        self._cursor = self._conn.cursor()

    def execute_write(
        self,
        sql: str,
        params: Optional[tuple | list] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """执行单条写 SQL（INSERT/UPDATE/DELETE），返回 rowcount。"""
        try:
            self.connect()
            assert self._cursor is not None
            self._cursor.execute(sql, params)
            return True, "Success", {"row_count": self._cursor.rowcount}
        except Exception as e:
            # PG：失败后须 rollback，否则事务处于 aborted 状态，后续语句全部级联失败。
            try:
                self.rollback()
            except Exception:
                pass
            return False, f"SQL execution failed: {str(e)}", {}

    def execute_upsert_batch(
        self,
        table: str,
        cols: list[str],
        conflict_cols: list[str] | tuple[str, ...],
        param_rows: list[tuple],
        *,
        page_size: int = 500,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """批量 UPSERT：PG 用 execute_values，MySQL 用多值 INSERT。"""
        if not param_rows:
            return True, "Success", {"row_count": 0}
        try:
            self.connect()
            assert self._cursor is not None
            quote = '"' if self.db_type == "pg" else "`"
            col_list = ", ".join(f"{quote}{c}{quote}" for c in cols)
            conflict_set = set(conflict_cols)
            update_cols = [c for c in cols if c not in conflict_set]
            if self.db_type == "pg":
                from psycopg2.extras import execute_values

                conflict = ", ".join(conflict_cols)
                if update_cols:
                    updates = ", ".join(
                        f"{quote}{c}{quote} = EXCLUDED.{quote}{c}{quote}" for c in update_cols
                    )
                    conflict_clause = f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
                else:
                    conflict_clause = f"ON CONFLICT ({conflict}) DO NOTHING"
                sql = (
                    f"INSERT INTO {quote}{table}{quote} ({col_list}) VALUES %s "
                    f"{conflict_clause}"
                )
                execute_values(self._cursor, sql, param_rows, page_size=page_size)
                return True, "Success", {"row_count": len(param_rows)}

            # MySQL: multi-row INSERT ... ON DUPLICATE KEY UPDATE / INSERT IGNORE
            total = 0
            for i in range(0, len(param_rows), page_size):
                chunk = param_rows[i : i + page_size]
                placeholders = ", ".join(
                    "(" + ", ".join(["%s"] * len(cols)) + ")" for _ in chunk
                )
                flat: list[Any] = []
                for row in chunk:
                    flat.extend(row)
                if update_cols:
                    updates = ", ".join(
                        f"{quote}{c}{quote} = VALUES({quote}{c}{quote})" for c in update_cols
                    )
                    sql = (
                        f"INSERT INTO {quote}{table}{quote} ({col_list}) VALUES {placeholders} "
                        f"ON DUPLICATE KEY UPDATE {updates}"
                    )
                else:
                    sql = (
                        f"INSERT IGNORE INTO {quote}{table}{quote} ({col_list}) "
                        f"VALUES {placeholders}"
                    )
                self._cursor.execute(sql, flat)
                total += self._cursor.rowcount if self._cursor.rowcount > 0 else len(chunk)
            return True, "Success", {"row_count": total}
        except Exception as e:
            # PG：事务一旦在某条语句上失败即进入 aborted 状态，必须 rollback 才能继续用此连接，
            # 否则后续所有语句都会级联报 "current transaction is aborted, commands ignored"。
            try:
                self.rollback()
            except Exception:
                pass
            return False, f"SQL execution failed: {str(e)}", {}

    def execute_query(
        self,
        sql: str,
        params: Optional[tuple | list] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """在同一会话内执行 SELECT，返回 columns/rows。"""
        try:
            self.connect()
            assert self._cursor is not None
            self._cursor.execute(sql, params)
            if not self._cursor.description:
                return True, "Success", {"columns": [], "rows": [], "row_count": 0}
            columns = [desc[0] for desc in self._cursor.description]
            rows = self._cursor.fetchall()
            converted_rows = [[convert_value(v) for v in row] for row in rows]
            return True, "Success", {
                "columns": columns,
                "rows": converted_rows,
                "row_count": len(converted_rows),
            }
        except Exception as e:
            return False, f"SQL execution failed: {str(e)}", {}

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn is not None:
            self._conn.rollback()

    def close(self) -> None:
        if self._cursor is not None:
            try:
                self._cursor.close()
            except Exception:
                pass
            self._cursor = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "WriteDbSession":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self.rollback()
        self.close()
        return False


def execute_write_sql(
    db_type: str,
    config: Dict[str, Any],
    sql: str,
    params: Optional[tuple | list] = None,
) -> Tuple[bool, str, Any]:
    """执行单条写 SQL 并自动提交（仅供简单场景；批量导入请用 WriteDbSession）。"""
    with WriteDbSession(db_type, config) as session:
        ok, msg, result = session.execute_write(sql, params)
        if not ok:
            return ok, msg, result
        session.commit()
        return ok, msg, result


def execute_sql(db_type: str, config: Dict[str, Any], sql: str) -> Tuple[bool, str, Any]:
    """
    Execute SQL on database.

    Returns:
        (success, message, result)
    """
    # Check if SQL is read-only
    if not check_sql_read(sql, db_type):
        return False, "SQL can only contain read operations (SELECT)", None

    if db_type == "pg":
        return execute_postgresql_sql(config, sql)
    elif db_type == "mysql":
        return execute_mysql_sql(config, sql)
    else:
        return False, f"Unsupported database type: {db_type}", None


def check_sql_read(sql: str, db_type: str) -> bool:
    """
    Check if SQL is read-only using sqlglot.

    Args:
        sql: SQL statement to check
        db_type: Database type (pg/mysql)

    Returns:
        True if SQL is read-only, False otherwise
    """
    try:
        from sqlglot import parse
        from sqlglot import expressions as exp

        dialect = "mysql" if db_type == "mysql" else None

        statements = parse(sql, dialect=dialect)

        if not statements:
            return False

        write_types = (
            exp.Insert, exp.Update, exp.Delete,
            exp.Create, exp.Drop, exp.Alter,
            exp.Merge, exp.Copy
        )

        for stmt in statements:
            if stmt is None:
                continue
            if isinstance(stmt, write_types):
                return False

        return True

    except Exception as e:
        logger.warning(f"SQL parse check failed: {e}, allowing by default")
        return True  # Allow if parse fails, let execution handle errors


def execute_postgresql_sql(config: Dict[str, Any], sql: str) -> Tuple[bool, str, Any]:
    """Execute SQL on PostgreSQL with proper result formatting."""
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
        cursor.execute(sql)

        # Check if it's a SELECT query
        if cursor.description:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

            # Convert values to JSON-serializable types
            converted_rows = []
            for row in rows:
                converted_row = [convert_value(v) for v in row]
                converted_rows.append(converted_row)

            result = {
                "columns": columns,
                "rows": converted_rows,
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
    """Execute SQL on MySQL with proper result formatting."""
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

            # Convert values to JSON-serializable types
            converted_rows = []
            for row in rows:
                converted_row = [convert_value(v) for v in row]
                converted_rows.append(converted_row)

            result = {
                "columns": columns,
                "rows": converted_rows,
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


def convert_value(value: Any, datetime_format: str = 'space') -> Any:
    """
    Convert Python value to JSON-serializable type.

    Args:
        value: The value to convert
        datetime_format: DateTime format ('iso' or 'space')

    Returns:
        JSON-serializable value
    """
    if value is None:
        return None

    # Handle bytes type (including BIT fields)
    if isinstance(value, bytes):
        if len(value) <= 8:
            try:
                int_val = int.from_bytes(value, 'big')
                if int_val in (0, 1):
                    return bool(int_val)
                else:
                    return int_val
            except:
                pass

        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            if any(b < 32 and b not in (9, 10, 13) for b in value):
                return f"0x{value.hex()}"
            else:
                return value.decode('latin-1')

    elif isinstance(value, bytearray):
        return convert_value(bytes(value))

    elif isinstance(value, timedelta):
        return str(value)

    elif isinstance(value, Decimal):
        return float(value)

    elif isinstance(value, datetime):
        if datetime_format == 'iso':
            return value.isoformat()
        else:
            if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
                return value.strftime('%Y-%m-%d')
            else:
                return value.strftime('%Y-%m-%d %H:%M:%S')

    elif isinstance(value, date):
        return value.isoformat()

    elif isinstance(value, time):
        return str(value)

    else:
        return value


def get_schema_info(db_type: str, config: Dict[str, Any]) -> list:
    """
    Get database schema (tables and columns).

    Returns:
        List of table info dicts:
        [
            {
                "name": "users",
                "comment": "User table",
                "fields": [
                    {"name": "id", "type": "bigint", "comment": "Primary key"},
                    {"name": "name", "type": "varchar(255)", "comment": "Name"},
                ]
            },
            ...
        ]
    """
    if db_type == "pg":
        return get_postgresql_schema(config)
    elif db_type == "mysql":
        return get_mysql_schema(config)
    else:
        return []


def get_postgresql_schema(config: Dict[str, Any]) -> list:
    """Get PostgreSQL schema."""
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

        # Get tables
        cursor.execute("""
            SELECT c.relname AS table_name,
                   COALESCE(d.description, '') AS table_comment
            FROM pg_catalog.pg_class c
            LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_description d ON d.objoid = c.oid AND d.objsubid = 0
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'v', 'p', 'm')
              AND c.relname NOT LIKE 'pg_%'
              AND c.relname NOT LIKE 'sql_%'
            ORDER BY c.relname
        """)

        tables = []
        for row in cursor.fetchall():
            table_name, table_comment = row
            table_info = {
                "name": table_name,
                "comment": table_comment,
                "fields": []
            }

            # Get columns for this table
            cursor.execute("""
                SELECT a.attname AS column_name,
                       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                       COALESCE(col_description(c.oid, a.attnum), '') AS column_comment
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON a.attrelid = c.oid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
            """, (table_name,))

            for col_row in cursor.fetchall():
                col_name, data_type, col_comment = col_row
                table_info["fields"].append({
                    "name": col_name,
                    "type": data_type,
                    "comment": col_comment
                })

            tables.append(table_info)

        cursor.close()
        conn.close()
        return tables

    except Exception as e:
        logger.error(f"Failed to get PostgreSQL schema: {e}")
        return []


def get_mysql_schema(config: Dict[str, Any]) -> list:
    """Get MySQL schema."""
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

        # Get tables
        cursor.execute("SHOW TABLES")
        table_names = [row[0] for row in cursor.fetchall()]

        tables = []
        for table_name in table_names:
            # Get table comment
            cursor.execute(f"SELECT TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table_name,))
            result = cursor.fetchone()
            table_comment = result[0] if result else ""

            table_info = {
                "name": table_name,
                "comment": table_comment,
                "fields": []
            }

            # Get columns
            cursor.execute(f"DESCRIBE `{table_name}`")
            for col_row in cursor.fetchall():
                col_name, data_type, nullable, key, default, extra = col_row
                table_info["fields"].append({
                    "name": col_name,
                    "type": data_type,
                    "comment": ""
                })

            tables.append(table_info)

        cursor.close()
        conn.close()
        return tables

    except Exception as e:
        logger.error(f"Failed to get MySQL schema: {e}")
        return []
