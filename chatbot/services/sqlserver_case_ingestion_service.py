from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction

from ..models import KnowledgeDocument
from .rag_service import delete_document_from_index, index_document
from .sqlserver_service import fetch_rows

SQLSERVER_CASE_FIELDS = (
    "CARD_ID",
    "Problem",
    "Problem_Cause",
    "Problem_detail",
    "Worker",
    "File_path",
    "Act",
    "Create_date",
)


@dataclass(slots=True)
class SQLServerCaseImportSummary:
    total_rows: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0


def _quote_identifier(identifier: str) -> str:
    normalized = (identifier or "").strip()
    if not normalized:
        raise ValueError("ชื่อ schema/table ว่างไม่ได้")
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


def fetch_sqlserver_cases(
    *,
    schema: str,
    table: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected_fields = ",\n            ".join(SQLSERVER_CASE_FIELDS)
    full_table_name = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    top_clause = f"TOP {max(1, int(limit))} " if limit else ""

    query = f"""
        SELECT {top_clause}
            {selected_fields}
        FROM {full_table_name}
        WHERE CARD_ID IS NOT NULL
        ORDER BY Create_date DESC, CARD_ID ASC
    """
    return fetch_rows(query)


def build_sqlserver_case_source(*, schema: str, table: str, card_id: str) -> str:
    return f"sqlserver:{schema}.{table}:{card_id}"


def build_sqlserver_case_title(row: dict[str, Any]) -> str:
    card_id = _normalize_text_value(row.get("CARD_ID"))
    problem = _normalize_text_value(row.get("Problem"))

    if card_id and problem:
        return _truncate_title(f"{card_id} | {problem}")
    if card_id:
        return _truncate_title(card_id)
    if problem:
        return _truncate_title(problem)
    return "SQL Server Case"


def build_sqlserver_case_content(row: dict[str, Any]) -> str:
    card_id = _normalize_text_value(row.get("CARD_ID"))
    problem = _normalize_text_value(row.get("Problem"))
    problem_cause = _normalize_text_value(row.get("Problem_Cause"))
    problem_detail = _normalize_text_value(row.get("Problem_detail"))
    worker = _normalize_text_value(row.get("Worker"))
    file_path = _normalize_text_value(row.get("File_path"))
    action = _normalize_text_value(row.get("Act"))
    create_date = _format_datetime_value(row.get("Create_date"))

    sections = []
    if card_id:
        sections.append(f"รหัสเคส: {card_id}")
    if problem:
        sections.append(f"อาการ: {problem}")
    if problem_cause:
        sections.append(f"สาเหตุ: {problem_cause}")
    if problem_detail:
        sections.append(f"วิธีแก้หรือการดำเนินการ: {problem_detail}")
    if worker:
        sections.append(f"ผู้ปฏิบัติงาน: {worker}")
    if action:
        sections.append(f"การกระทำเพิ่มเติม: {action}")
    if file_path:
        sections.append(f"ไฟล์อ้างอิง: {file_path}")
    if create_date:
        sections.append(f"วันที่บันทึก: {create_date}")

    return "\n".join(sections).strip()


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


def import_sqlserver_cases(
    *,
    schema: str,
    table: str,
    limit: int | None = None,
) -> dict[str, Any]:
    rows = fetch_sqlserver_cases(
        schema=schema,
        table=table,
        limit=limit,
    )
    summary = SQLServerCaseImportSummary(total_rows=len(rows))
    errors: list[dict[str, str]] = []

    card_ids = [
        _normalize_text_value(row.get("CARD_ID"))
        for row in rows
        if _normalize_text_value(row.get("CARD_ID"))
    ]
    source_map = {
        build_sqlserver_case_source(schema=schema, table=table, card_id=card_id): card_id
        for card_id in card_ids
    }

    existing_documents: dict[str, KnowledgeDocument] = {}
    for document in KnowledgeDocument.objects.filter(
        source__in=list(source_map.keys())
    ).order_by("id"):
        if document.source and document.source not in existing_documents:
            existing_documents[document.source] = document

    for row in rows:
        card_id = _normalize_text_value(row.get("CARD_ID"))
        if not card_id:
            summary.error_count += 1
            errors.append(
                {
                    "card_id": "-",
                    "error": "ไม่พบ CARD_ID ในแถวนี้",
                }
            )
            continue

        title = build_sqlserver_case_title(row)
        content = build_sqlserver_case_content(row)
        if not content:
            summary.error_count += 1
            errors.append(
                {
                    "card_id": card_id,
                    "error": "ไม่พบข้อความที่นำไปสร้าง knowledge document ได้",
                }
            )
            continue

        source = build_sqlserver_case_source(
            schema=schema,
            table=table,
            card_id=card_id,
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
                    "card_id": card_id,
                    "error": str(exc),
                }
            )

    return {
        "schema": schema,
        "table": table,
        "summary": summary,
        "errors": errors,
    }
