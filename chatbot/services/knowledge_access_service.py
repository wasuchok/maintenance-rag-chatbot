from typing import Optional

from django.db.models import QuerySet

from ..models import KnowledgeDocument


def normalize_knowledge_visibility(
    visibility: str | None,
    *,
    user_id: Optional[int] = None,
) -> str:
    return KnowledgeDocument.VISIBILITY_SHARED


def get_knowledge_visibility_label(visibility: str) -> str:
    if visibility == KnowledgeDocument.VISIBILITY_PRIVATE:
        return "ส่วนตัว"
    return "แชร์"


def get_accessible_knowledge_queryset(
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> QuerySet[KnowledgeDocument]:
    return KnowledgeDocument.objects.filter(
        visibility=KnowledgeDocument.VISIBILITY_SHARED
    )


def get_manageable_knowledge_queryset(
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> QuerySet[KnowledgeDocument]:
    if can_manage_all:
        return KnowledgeDocument.objects.filter(
            visibility=KnowledgeDocument.VISIBILITY_SHARED
        )

    return KnowledgeDocument.objects.none()


def get_accessible_knowledge_document_ids(
    *,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> list[int]:
    return list(
        get_accessible_knowledge_queryset(
            user_id=user_id,
            can_manage_all=can_manage_all,
        ).values_list("id", flat=True)
    )
