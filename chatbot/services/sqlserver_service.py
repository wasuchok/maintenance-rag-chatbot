from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Sequence

from django.conf import settings


class SQLServerConfigurationError(RuntimeError):
    pass


class SQLServerDependencyError(RuntimeError):
    pass


def _get_pyodbc():
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        raise SQLServerDependencyError(
            "ยังไม่ได้ติดตั้ง pyodbc กรุณา pip install -r requirements.txt ก่อน"
        ) from exc

    return pyodbc


def _get_pytds():
    try:
        import pytds  # type: ignore
    except ImportError as exc:
        raise SQLServerDependencyError(
            "ยังไม่ได้ติดตั้ง python-tds กรุณา pip install -r requirements.txt ก่อน"
        ) from exc

    return pytds


def is_sqlserver_configured() -> bool:
    return bool(settings.SQLSERVER_HOST and settings.SQLSERVER_DATABASE)


def _require_sqlserver_config() -> None:
    if not settings.SQLSERVER_HOST:
        raise SQLServerConfigurationError("ยังไม่ได้ตั้งค่า SQLSERVER_HOST")

    if not settings.SQLSERVER_DATABASE:
        raise SQLServerConfigurationError("ยังไม่ได้ตั้งค่า SQLSERVER_DATABASE")

    if (
        not settings.SQLSERVER_TRUSTED_CONNECTION
        and (not settings.SQLSERVER_USERNAME or not settings.SQLSERVER_PASSWORD)
    ):
        raise SQLServerConfigurationError(
            "ยังไม่ได้ตั้งค่า SQLSERVER_USERNAME / SQLSERVER_PASSWORD"
        )


def _quote_identifier(identifier: str) -> str:
    normalized = (identifier or "").strip()
    if not normalized:
        raise SQLServerConfigurationError("ชื่อ schema/table ว่างไม่ได้")

    return f"[{normalized.replace(']', ']]')}]"


def build_sqlserver_connection_string() -> str:
    _require_sqlserver_config()

    server = settings.SQLSERVER_HOST
    if settings.SQLSERVER_PORT:
        server = f"{server},{settings.SQLSERVER_PORT}"

    parts = [
        f"DRIVER={{{settings.SQLSERVER_DRIVER}}}",
        f"SERVER={server}",
        f"DATABASE={settings.SQLSERVER_DATABASE}",
        f"Encrypt={'yes' if settings.SQLSERVER_ENCRYPT else 'no'}",
        "TrustServerCertificate="
        + ("yes" if settings.SQLSERVER_TRUST_SERVER_CERTIFICATE else "no"),
    ]

    if settings.SQLSERVER_TRUSTED_CONNECTION:
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={settings.SQLSERVER_USERNAME}")
        parts.append(f"PWD={settings.SQLSERVER_PASSWORD}")

    return ";".join(parts)


def get_sqlserver_client_name() -> str:
    client_name = (settings.SQLSERVER_CLIENT or "pytds").strip().lower()
    if client_name not in {"pytds", "pyodbc"}:
        raise SQLServerConfigurationError(
            "SQLSERVER_CLIENT ต้องเป็น pytds หรือ pyodbc เท่านั้น"
        )
    return client_name


@contextmanager
def sqlserver_connection():
    _require_sqlserver_config()
    client_name = get_sqlserver_client_name()

    if client_name == "pyodbc":
        pyodbc = _get_pyodbc()
        connection = pyodbc.connect(
            build_sqlserver_connection_string(),
            timeout=settings.SQLSERVER_CONNECTION_TIMEOUT,
        )
    else:
        if settings.SQLSERVER_TRUSTED_CONNECTION:
            raise SQLServerConfigurationError(
                "โหมด pytds ยังไม่รองรับ SQLSERVER_TRUSTED_CONNECTION ในโปรเจกต์นี้"
            )

        pytds = _get_pytds()
        connection = pytds.connect(
            server=settings.SQLSERVER_HOST,
            port=settings.SQLSERVER_PORT,
            database=settings.SQLSERVER_DATABASE,
            user=settings.SQLSERVER_USERNAME,
            password=settings.SQLSERVER_PASSWORD,
            timeout=settings.SQLSERVER_CONNECTION_TIMEOUT,
            login_timeout=settings.SQLSERVER_CONNECTION_TIMEOUT,
            autocommit=False,
            as_dict=False,
        )

    try:
        yield connection
    finally:
        connection.close()


def fetch_rows(query: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with sqlserver_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(query, list(params or []))
        columns = [column[0] for column in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]


def test_sqlserver_connection() -> dict[str, Any]:
    rows = fetch_rows(
        """
        SELECT
            CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(255)) AS server_name,
            DB_NAME() AS database_name,
            SYSTEM_USER AS login_name,
            SYSDATETIME() AS checked_at
        """
    )
    return rows[0] if rows else {}


def fetch_table_preview(
    *,
    table: str | None = None,
    schema: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    normalized_table = (table or settings.SQLSERVER_CASES_TABLE or "").strip()
    if not normalized_table:
        raise SQLServerConfigurationError(
            "ยังไม่ได้กำหนด table ที่ต้องการ preview กรุณาระบุ --table หรือ SQLSERVER_CASES_TABLE"
        )

    normalized_schema = (schema or settings.SQLSERVER_CASES_SCHEMA or "dbo").strip()
    safe_limit = max(1, int(limit))
    full_table_name = (
        f"{_quote_identifier(normalized_schema)}.{_quote_identifier(normalized_table)}"
    )

    return fetch_rows(f"SELECT TOP {safe_limit} * FROM {full_table_name}")
