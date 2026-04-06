from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction

from ..models import KnowledgeDocument
from .rag_service import delete_document_from_index, index_document
from .sqlserver_service import fetch_rows

SQLSERVER_JOB_CARD_FIELDS = (
    "ID",
    "MC_NO",
    "Description",
    "LOCATION",
    "impact_quality",
    "J_CREATE_DATE",
    "ASSIGN_TEAM",
    "REPAIR_DETAIL",
    "REPAIR_START_DATE",
    "REPAIR_END_DATE",
    "REPAIR_FNAME1",
    "REPAIR_FNAME2",
    "REPAIR_FNAME3",
    "REPAIR_PROBLEM_BY",
    "Position_name",
    "Problem",
    "Problem_Cause",
    "Problem_detail",
)


@dataclass(slots=True)
class SQLServerJobCardImportSummary:
    total_rows: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0


def _quote_identifier(identifier: str) -> str:
    normalized = (identifier or "").strip()
    if not normalized:
        raise ValueError("ชื่อ schema/view ว่างไม่ได้")
    return f"[{normalized.replace(']', ']]')}]"


def _normalize_text_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if text in {"-", "[NULL]", "NULL", "None"}:
        return ""
    return " ".join(text.split())


def _format_datetime_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return _normalize_text_value(value)


def _truncate_title(title: str, limit: int = 255) -> str:
    normalized = " ".join((title or "").split()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _impact_quality_label(value: Any) -> str:
    normalized = _normalize_text_value(value)
    if normalized in {"1", "true", "True"}:
        return "เกี่ยว"
    return "ไม่เกี่ยว"


def fetch_sqlserver_job_cards(
    *,
    schema: str,
    view_name: str,
    limit: int | None = None,
    days: int | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    selected_fields = ",\n            ".join(SQLSERVER_JOB_CARD_FIELDS)
    full_view_name = f"{_quote_identifier(schema)}.{_quote_identifier(view_name)}"
    top_clause = f"TOP {max(1, int(limit))} " if limit else ""
    where_clauses = ["ID IS NOT NULL"]
    params: list[Any] = []

    if since is not None:
        where_clauses.append("J_CREATE_DATE >= ?")
        params.append(since)
    elif days is not None:
        safe_days = max(1, int(days))
        where_clauses.append(f"J_CREATE_DATE >= DATEADD(day, -{safe_days}, GETDATE())")

    where_sql = "\n            AND ".join(where_clauses)

    query = f"""
        SELECT {top_clause}
            {selected_fields}
        FROM {full_view_name}
        WHERE {where_sql}
        ORDER BY J_CREATE_DATE DESC, ID DESC
    """
    return fetch_rows(query, params)


def build_sqlserver_job_card_source(*, schema: str, view_name: str, record_id: str) -> str:
    return f"sqlserver:{schema}.{view_name}:{record_id}"


def build_sqlserver_job_card_title(row: dict[str, Any]) -> str:
    record_id = _normalize_text_value(row.get("ID"))
    machine_no = _normalize_text_value(row.get("MC_NO"))
    description = _normalize_text_value(row.get("Description"))

    parts = [part for part in (record_id, machine_no, description) if part]
    if parts:
        return _truncate_title(" | ".join(parts))
    return "SQL Server Job Card"


def build_sqlserver_job_card_content(row: dict[str, Any]) -> str:
    field_mapping = [
        ("ID", "รหัสรายการ"),
        ("MC_NO", "หมายเลขเครื่องจักร"),
        ("Description", "อาการชำรุด"),
        ("LOCATION", "ทีม"),
        ("J_CREATE_DATE", "แจ้งซ่อม"),
        ("ASSIGN_TEAM", "ทีมที่เข้าซ่อม"),
        ("REPAIR_DETAIL", "รายละเอียดการซ่อม"),
        ("REPAIR_START_DATE", "เริ่มซ่อม"),
        ("REPAIR_END_DATE", "ซ่อมเสร็จ"),
        ("REPAIR_FNAME1", "คนซ่อม1"),
        ("REPAIR_FNAME2", "คนซ่อม2"),
        ("REPAIR_FNAME3", "คนซ่อม3"),
        ("REPAIR_PROBLEM_BY", "เกิดปัญหาจากทีม"),
        ("Position_name", "ตำแหน่งที่เสีย"),
        ("Problem", "ปัญหาเพิ่มเติม"),
        ("Problem_Cause", "ที่มาของปัญหา"),
        ("Problem_detail", "รายละเอียดปัญหา"),
    ]

    sections: list[str] = []
    for key, label in field_mapping:
        value = row.get(key)
        formatted = (
            _format_datetime_value(value)
            if key in {"J_CREATE_DATE", "REPAIR_START_DATE", "REPAIR_END_DATE"}
            else _normalize_text_value(value)
        )
        if formatted:
            sections.append(f"{label}: {formatted}")

    sections.insert(
        4 if len(sections) >= 4 else len(sections),
        f"ผลกระทบทางด้านคุณภาพ: {_impact_quality_label(row.get('impact_quality'))}",
    )

    return "\n".join(item for item in sections if item).strip()


def _update_document_and_reindex(
    document: KnowledgeDocument,
    *,
    title: str,
    content: str,
    source: str,
) -> None:
    original_state = {
        "title": document.title,
        "content": document.content,
        "source": document.source,
        "visibility": document.visibility,
        "owner_id": document.owner_id,
    }

    try:
        with transaction.atomic():
            delete_document_from_index(document.id)
            document.title = title
            document.content = content
            document.source = source
            document.visibility = KnowledgeDocument.VISIBILITY_SHARED
            document.owner_id = None
            document.save(
                update_fields=[
                    "title",
                    "content",
                    "source",
                    "visibility",
                    "owner",
                ]
            )
            index_document(document)
    except Exception:
        document.title = original_state["title"]
        document.content = original_state["content"]
        document.source = original_state["source"]
        document.visibility = original_state["visibility"]
        document.owner_id = original_state["owner_id"]
        try:
            index_document(document)
        except Exception:
            pass
        raise


def import_sqlserver_job_cards(
    *,
    schema: str,
    view_name: str,
    limit: int | None = None,
    days: int | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    rows = fetch_sqlserver_job_cards(
        schema=schema,
        view_name=view_name,
        limit=limit,
        days=days,
        since=since,
    )
    summary = SQLServerJobCardImportSummary(total_rows=len(rows))
    errors: list[dict[str, str]] = []
    latest_job_create_date: datetime | None = None

    record_ids = [
        _normalize_text_value(row.get("ID"))
        for row in rows
        if _normalize_text_value(row.get("ID"))
    ]
    source_map = {
        build_sqlserver_job_card_source(
            schema=schema,
            view_name=view_name,
            record_id=record_id,
        ): record_id
        for record_id in record_ids
    }

    existing_documents: dict[str, KnowledgeDocument] = {}
    for document in KnowledgeDocument.objects.filter(
        source__in=list(source_map.keys())
    ).order_by("id"):
        if document.source and document.source not in existing_documents:
            existing_documents[document.source] = document

    for row in rows:
        record_id = _normalize_text_value(row.get("ID"))
        if not record_id:
            summary.error_count += 1
            errors.append(
                {
                    "id": "-",
                    "error": "ไม่พบ ID ในแถวนี้",
                }
            )
            continue

        title = build_sqlserver_job_card_title(row)
        content = build_sqlserver_job_card_content(row)
        if not content:
            summary.error_count += 1
            errors.append(
                {
                    "id": record_id,
                    "error": "ไม่พบข้อความที่นำไปสร้าง knowledge document ได้",
                }
            )
            continue

        source = build_sqlserver_job_card_source(
            schema=schema,
            view_name=view_name,
            record_id=record_id,
        )
        existing_document = existing_documents.get(source)

        try:
            if existing_document is None:
                with transaction.atomic():
                    document = KnowledgeDocument.objects.create(
                        owner=None,
                        title=title,
                        content=content,
                        source=source,
                        visibility=KnowledgeDocument.VISIBILITY_SHARED,
                    )
                    index_document(document)
                existing_documents[source] = document
                summary.created_count += 1
                continue

            if (
                existing_document.title == title
                and existing_document.content == content
                and existing_document.visibility == KnowledgeDocument.VISIBILITY_SHARED
                and existing_document.owner_id is None
            ):
                summary.skipped_count += 1
                continue

            _update_document_and_reindex(
                existing_document,
                title=title,
                content=content,
                source=source,
            )
            summary.updated_count += 1
        except Exception as exc:
            summary.error_count += 1
            errors.append(
                {
                    "id": record_id,
                    "error": str(exc),
                }
            )

        candidate_datetime = row.get("J_CREATE_DATE")
        if isinstance(candidate_datetime, datetime):
            if latest_job_create_date is None or candidate_datetime > latest_job_create_date:
                latest_job_create_date = candidate_datetime

    return {
        "schema": schema,
        "view_name": view_name,
        "days": days,
        "since": _format_datetime_value(since) if since else None,
        "latest_job_create_date": (
            _format_datetime_value(latest_job_create_date)
            if latest_job_create_date
            else None
        ),
        "summary": summary,
        "errors": errors,
    }
