from typing import Dict, List, Optional

from asgiref.sync import sync_to_async
from chainlit.data.base import BaseDataLayer
from chainlit.types import Feedback, PaginatedResponse, Pagination, ThreadDict, ThreadFilter
from chainlit.user import PersistedUser, User
from django.contrib.auth import get_user_model

from .conversation_management_service import (
    delete_native_thread_by_thread_id,
    get_native_thread_by_thread_id,
    get_thread_author_identifier,
    list_native_threads,
    upsert_conversation_thread,
)

UserModel = get_user_model()


def _build_persisted_user_from_django_user(user) -> PersistedUser:
    metadata = {
        "django_user_id": user.id,
        "username": user.get_username(),
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
    }
    return PersistedUser(
        id=str(user.id),
        identifier=user.get_username(),
        display_name=user.get_full_name() or user.get_username(),
        createdAt=user.date_joined.isoformat(),
        metadata=metadata,
    )


def _get_persisted_user(identifier: str) -> Optional[PersistedUser]:
    user = UserModel.objects.filter(username=identifier).first()
    if not user:
        return None

    return _build_persisted_user_from_django_user(user)


def _create_or_get_persisted_user(user: User) -> Optional[PersistedUser]:
    identifier = (user.identifier or "").strip()
    if not identifier:
        return None

    django_user = UserModel.objects.filter(username=identifier).first()
    if not django_user:
        django_user_id = (user.metadata or {}).get("django_user_id")
        if django_user_id is not None:
            django_user = UserModel.objects.filter(id=django_user_id).first()

    if not django_user:
        return None

    return _build_persisted_user_from_django_user(django_user)


def _update_thread(
    thread_id: str,
    *,
    name: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict] = None,
    tags: Optional[List[str]] = None,
) -> None:
    normalized_user_id = int(user_id) if user_id is not None else None
    normalized_metadata = dict(metadata or {})

    if name is not None and "auto_title" not in normalized_metadata:
        normalized_metadata["auto_title"] = False

    upsert_conversation_thread(
        thread_id,
        user_id=normalized_user_id,
        name=name,
        metadata=normalized_metadata,
        tags=tags,
    )


class DjangoChainlitDataLayer(BaseDataLayer):
    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        return await sync_to_async(_get_persisted_user, thread_sensitive=True)(
            identifier
        )

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        return await sync_to_async(_create_or_get_persisted_user, thread_sensitive=True)(
            user
        )

    async def delete_feedback(self, feedback_id: str) -> bool:
        return True

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return feedback.id or ""

    async def create_element(self, element):
        return None

    async def get_element(self, thread_id: str, element_id: str):
        return None

    async def delete_element(self, element_id: str, thread_id: Optional[str] = None):
        return None

    async def create_step(self, step_dict):
        return None

    async def update_step(self, step_dict):
        return None

    async def delete_step(self, step_id: str):
        return None

    async def get_thread_author(self, thread_id: str) -> str:
        return await sync_to_async(get_thread_author_identifier, thread_sensitive=True)(
            thread_id
        )

    async def delete_thread(self, thread_id: str):
        await sync_to_async(
            delete_native_thread_by_thread_id,
            thread_sensitive=True,
        )(thread_id)

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        if not filters.userId:
            raise ValueError("userId is required")

        return await sync_to_async(list_native_threads, thread_sensitive=True)(
            user_id=int(filters.userId),
            limit=pagination.first,
            cursor=pagination.cursor,
            search=filters.search,
        )

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        return await sync_to_async(
            get_native_thread_by_thread_id,
            thread_sensitive=True,
        )(thread_id)

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ):
        await sync_to_async(_update_thread, thread_sensitive=True)(
            thread_id,
            name=name,
            user_id=user_id,
            metadata=metadata,
            tags=tags,
        )

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        return None

    async def get_favorite_steps(self, user_id: str):
        return []
