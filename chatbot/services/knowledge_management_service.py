from typing import Any, Dict, List, Optional

from django.db import transaction

from .knowledge_access_service import (
    get_accessible_knowledge_queryset,
    get_knowledge_visibility_label,
    get_manageable_knowledge_queryset,
)
from .rag_service import delete_document_from_index


def list_knowledge_documents(
    limit: int = 5,
    offset: int = 0,
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> Dict[str, Any]:
    limit = max(1, int(limit))
    offset = max(0, int(offset))

    queryset = (
        get_accessible_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        )
        .select_related("owner")
        .order_by("-created_at")
    )
    total = queryset.count()

    if total == 0:
        return {
            "total": 0,
            "manageable_total": 0,
            "limit": limit,
            "offset": 0,
            "results": [],
            "has_prev": False,
            "has_next": False,
        }

    if offset >= total:
        offset = ((total - 1) // limit) * limit

    documents = list(queryset[offset : offset + limit])
    manageable_ids = set(
        get_manageable_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        )
        .filter(id__in=[doc.id for doc in documents])
        .values_list("id", flat=True)
    )
    manageable_total = get_manageable_knowledge_queryset(
        user_id=user_id,
        can_manage_all=can_manage_all,
    ).count()

    return {
        "total": total,
        "manageable_total": manageable_total,
        "limit": limit,
        "offset": offset,
        "results": [
            {
                "id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M"),
                "content_preview": doc.content[:100].strip(),
                "visibility": doc.visibility,
                "visibility_label": get_knowledge_visibility_label(doc.visibility),
                "owner_username": (
                    doc.owner.get_username() if doc.owner_id is not None else "-"
                ),
                "can_delete": doc.id in manageable_ids,
            }
            for doc in documents
        ],
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
    }


def get_knowledge_document_summary(
    document_id: int,
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> Dict[str, Any]:
    document = (
        get_accessible_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        )
        .select_related("owner")
        .get(id=document_id)
    )
    can_delete = get_manageable_knowledge_queryset(
        user_id=user_id,
        can_manage_all=can_manage_all,
    ).filter(id=document_id).exists()

    return {
        "id": document.id,
        "title": document.title,
        "source": document.source,
        "visibility": document.visibility,
        "visibility_label": get_knowledge_visibility_label(document.visibility),
        "owner_username": (
            document.owner.get_username() if document.owner_id is not None else "-"
        ),
        "can_delete": can_delete,
    }


def delete_knowledge_document(
    document_id: int,
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> Dict[str, Any]:
    with transaction.atomic():
        document = get_manageable_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        ).get(id=document_id)
        delete_document_from_index(document.id)

        result = {
            "document_id": document.id,
            "title": document.title,
            "source": document.source,
            "visibility": document.visibility,
        }
        document.delete()

    return result


def delete_all_knowledge_documents(
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> Dict[str, Any]:
    documents = list(
        get_manageable_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        ).order_by("-created_at")
    )

    if not documents:
        return {
            "deleted_count": 0,
            "deleted_titles": [],
        }

    with transaction.atomic():
        for document in documents:
            delete_document_from_index(document.id)

        deleted_titles: List[str] = [document.title for document in documents]
        get_manageable_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        ).delete()

    return {
        "deleted_count": len(deleted_titles),
        "deleted_titles": deleted_titles[:10],
    }
