"""MariaDB connection helpers for OIS."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import mysql.connector
from mysql.connector import Error as MySQLError

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def settings() -> dict:
    return {
        "host": os.getenv("OIS_DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("OIS_DB_PORT", "3306")),
        "user": os.getenv("OIS_DB_USER", "ois"),
        "password": os.getenv("OIS_DB_PASSWORD", "ois"),
        "database": os.getenv("OIS_DB_NAME", "ois_db"),
    }


def connect(include_database: bool = True):
    cfg = settings()
    kwargs = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
        "autocommit": False,
    }
    if include_database:
        kwargs["database"] = cfg["database"]
    return mysql.connector.connect(**kwargs)


@contextmanager
def get_connection(include_database: bool = True):
    conn = connect(include_database=include_database)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _statements(script: str) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                chunks.append(stmt)
            buf = []
    leftover = "\n".join(buf).strip().rstrip(";").strip()
    if leftover:
        chunks.append(leftover)
    return chunks


def _run_sql_file(conn, path: Path) -> None:
    cursor = conn.cursor()
    try:
        for stmt in _statements(path.read_text(encoding="utf-8")):
            cursor.execute(stmt)
    finally:
        cursor.close()


def ping() -> None:
    """Raise a clear error if MariaDB is unreachable."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
    except MySQLError as exc:
        cfg = settings()
        raise SystemExit(
            f"Cannot reach MariaDB at {cfg['host']}:{cfg['port']} "
            f"as {cfg['user']} ({exc}).\n"
            "Start it with:  docker compose up -d"
        ) from exc


def init_schema() -> None:
    schema = ROOT / "sql" / "schema.sql"
    seed = ROOT / "sql" / "seed.sql"
    with get_connection() as conn:
        _run_sql_file(conn, schema)
        _run_sql_file(conn, seed)


def execute_raw_sql(query: str) -> dict[str, Any]:
    """Execute arbitrary SQL query for the interactive Shell console."""
    import time
    clean_sql = query.strip()
    if not clean_sql:
        return {"status": "error", "error": "Query string is empty"}

    t_start = time.perf_counter()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(clean_sql)
            t_elapsed = (time.perf_counter() - t_start) * 1000.0

            if cursor.description:
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
                cursor.close()
                return {
                    "status": "ok",
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "duration_ms": round(t_elapsed, 2),
                }
            else:
                affected = cursor.rowcount
                cursor.close()
                return {
                    "status": "ok",
                    "affected_rows": affected,
                    "duration_ms": round(t_elapsed, 2),
                    "message": f"Query OK, {affected} row(s) affected",
                }
    except Exception as exc:
        t_elapsed = (time.perf_counter() - t_start) * 1000.0
        return {
            "status": "error",
            "error": str(exc),
            "duration_ms": round(t_elapsed, 2),
        }
