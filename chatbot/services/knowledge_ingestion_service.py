from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from django.db import transaction

from ..models import KnowledgeDocument
from .knowledge_access_service import (
    get_knowledge_visibility_label,
    normalize_knowledge_visibility,
)
from .rag_service import index_document

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".log",
    ".html",
    ".htm",
    ".xml",
    ".yaml",
    ".yml",
}


def read_text_file(file_path: Path) -> str:
    encodings = ("utf-8", "utf-8-sig", "cp874", "windows-1252")

    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    return file_path.read_text(encoding="utf-8", errors="ignore")


def extract_pdf_text(file_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError(
            "ยังไม่รองรับไฟล์ PDF เพราะยังไม่ได้ติดตั้งแพ็กเกจ pypdf"
        ) from exc

    reader = PdfReader(str(file_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page and page.strip())


def extract_file_content(file_path: Path) -> str:
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_text(file_path)

    if suffix in TEXT_SUFFIXES:
        return read_text_file(file_path)

    raise ValueError(
        "รองรับเฉพาะไฟล์ประเภท pdf, txt, md, csv, json, html, xml, yaml และ log"
    )


def build_document_title(file_path: Path) -> str:
    title = file_path.stem.strip()
    return title or file_path.name


def ingest_knowledge_file(
    file_path: str,
    display_name: str | None = None,
    *,
    user_id: Optional[int] = None,
    visibility: str | None = None,
) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์: {path}")

    content = extract_file_content(path).strip()
    if not content:
        raise ValueError("ไม่พบข้อความที่นำมาใช้งานได้ในไฟล์นี้")

    title = build_document_title(path)
    source = display_name or path.name
    normalized_visibility = normalize_knowledge_visibility(
        visibility,
        user_id=user_id,
    )

    with transaction.atomic():
        document = KnowledgeDocument.objects.create(
            owner_id=user_id,
            title=title,
            content=content,
            source=source,
            visibility=normalized_visibility,
        )
        index_document(document)

    return {
        "document_id": document.id,
        "title": document.title,
        "source": document.source,
        "characters": len(content),
        "visibility": document.visibility,
        "visibility_label": get_knowledge_visibility_label(document.visibility),
    }


def ingest_knowledge_files(
    files: Iterable[Dict[str, str]],
    *,
    user_id: Optional[int] = None,
    visibility: str | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    successes: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for file_info in files:
        path = file_info["path"]
        name = file_info.get("name")

        try:
            result = ingest_knowledge_file(
                path,
                display_name=name,
                user_id=user_id,
                visibility=visibility,
            )
            successes.append(result)
        except Exception as exc:
            errors.append(
                {
                    "name": name or Path(path).name,
                    "error": str(exc),
                }
            )

    return {
        "successes": successes,
        "errors": errors,
    }
