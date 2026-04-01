from collections import defaultdict
from typing import Any, Dict, Optional

from chainlit.step import StepDict
from chainlit.types import PageInfo, PaginatedResponse, ThreadDict
from django.contrib.auth import get_user_model
from django.db.models import Count, Max, OuterRef, Q, QuerySet, Subquery

from ..models import ChatMessage, ConversationThread

UserModel = get_user_model()
CHAINLIT_CHATMESSAGE_PREFIX = "chatmessage-"
AUTO_THREAD_TITLE_METADATA_KEY = "auto_title"


def get_chat_queryset(*, user_id: Optional[int] = None) -> QuerySet[ChatMessage]:
    queryset = ChatMessage.objects.select_related("user").all()

    if user_id is None:
        return queryset.filter(user__isnull=True)

    return queryset.filter(user_id=user_id)


def get_thread_queryset(
    *,
    user_id: Optional[int] = None,
) -> QuerySet[ConversationThread]:
    queryset = ConversationThread.objects.select_related("user").all()

    if user_id is None:
        return queryset.filter(user__isnull=True)

    return queryset.filter(user_id=user_id)


def truncate_text(value: str | None, *, length: int = 60) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "ห้องสนทนาใหม่"
    if len(text) <= length:
        return text
    return text[: length - 3].rstrip() + "..."


def normalize_thread_name(name: str | None) -> str | None:
    text = " ".join((name or "").split())
    if not text:
        return None
    return text[:255]


def build_conversation_title(
    *,
    explicit_name: str | None = None,
    first_user_content: str | None = None,
    latest_content: str | None = None,
) -> str:
    if explicit_name:
        return explicit_name

    return truncate_text(first_user_content or latest_content)


def is_auto_thread_title(thread: Optional[ConversationThread]) -> bool:
    if not thread:
        return False

    metadata = thread.metadata or {}
    return bool(metadata.get(AUTO_THREAD_TITLE_METADATA_KEY))


def get_user_display_name(user) -> str:
    if not user:
        return "คุณ"

    full_name = user.get_full_name()
    if full_name:
        return full_name

    return user.get_username()


def serialize_datetime(value) -> str:
    return value.isoformat() if value else ""


def build_step_from_chat_message(message: ChatMessage) -> StepDict:
    is_user_message = message.role == "user"
    author = (
        get_user_display_name(message.user)
        if is_user_message
        else (message.model_name or "Assistant")
    )

    return StepDict(
        id=build_chainlit_step_id(message.id),
        name=author,
        type="user_message" if is_user_message else "assistant_message",
        threadId=message.conversation_id,
        parentId=None,
        streaming=False,
        metadata={},
        output=message.content,
        createdAt=serialize_datetime(message.created_at),
        start=serialize_datetime(message.created_at),
        end=serialize_datetime(message.created_at),
    )


def build_chainlit_step_id(message_id: int) -> str:
    return f"{CHAINLIT_CHATMESSAGE_PREFIX}{message_id}"


def parse_chat_message_id_from_step_id(step_id: str | None) -> int | None:
    normalized = (step_id or "").strip()
    if not normalized.startswith(CHAINLIT_CHATMESSAGE_PREFIX):
        return None

    raw_id = normalized[len(CHAINLIT_CHATMESSAGE_PREFIX) :]
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def get_chat_message_for_step(
    step_id: str,
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[ChatMessage]:
    queryset = get_chat_queryset(user_id=user_id)

    if conversation_id:
        queryset = queryset.filter(conversation_id=conversation_id)

    message_id = parse_chat_message_id_from_step_id(step_id)
    if message_id is not None:
        return queryset.filter(id=message_id).first()

    normalized_step_id = (step_id or "").strip()
    if not normalized_step_id:
        return None

    return queryset.filter(chainlit_step_id=normalized_step_id).first()


def has_later_user_messages(message: ChatMessage) -> bool:
    ordering_filter = Q(created_at__gt=message.created_at) | Q(
        created_at=message.created_at,
        id__gt=message.id,
    )

    return (
        get_chat_queryset(user_id=message.user_id)
        .filter(
            conversation_id=message.conversation_id,
            role="user",
        )
        .filter(ordering_filter)
        .exists()
    )


def delete_messages_after(message: ChatMessage) -> int:
    ordering_filter = Q(created_at__gt=message.created_at) | Q(
        created_at=message.created_at,
        id__gt=message.id,
    )

    queryset = (
        get_chat_queryset(user_id=message.user_id)
        .filter(conversation_id=message.conversation_id)
        .filter(ordering_filter)
    )
    deleted_count, _ = queryset.delete()
    return deleted_count


def get_editable_user_message_status(
    step_id: str,
    *,
    conversation_id: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    message = get_chat_message_for_step(
        step_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    if not message:
        return {
            "status": "new_message",
            "message": None,
        }

    if message.role != "user":
        return {
            "status": "not_user_message",
            "message": message,
        }

    if has_later_user_messages(message):
        return {
            "status": "not_latest_user_message",
            "message": message,
        }

    return {
        "status": "editable",
        "message": message,
    }


def upsert_auto_conversation_title(
    conversation_id: str,
    *,
    user_id: Optional[int] = None,
    title: str,
) -> str:
    normalized_title = normalize_thread_name(title)
    if not normalized_title:
        return "ห้องสนทนาใหม่"

    existing_thread = get_thread_queryset(user_id=user_id).filter(
        thread_id=conversation_id
    ).first()
    if existing_thread and existing_thread.name and not is_auto_thread_title(existing_thread):
        return existing_thread.name

    metadata = dict(existing_thread.metadata or {}) if existing_thread else {}
    metadata[AUTO_THREAD_TITLE_METADATA_KEY] = True

    thread = upsert_conversation_thread(
        conversation_id,
        user_id=user_id,
        name=normalized_title,
        metadata=metadata,
    )
    return thread.name or normalized_title


def get_conversation_summary_rows(
    *,
    user_id: Optional[int] = None,
):
    queryset = get_chat_queryset(user_id=user_id)
    thread_name = (
        get_thread_queryset(user_id=user_id)
        .filter(thread_id=OuterRef("conversation_id"))
        .values("name")[:1]
    )
    first_user_message = (
        get_chat_queryset(user_id=user_id)
        .filter(conversation_id=OuterRef("conversation_id"), role="user")
        .order_by("created_at")
        .values("content")[:1]
    )
    latest_message = (
        get_chat_queryset(user_id=user_id)
        .filter(conversation_id=OuterRef("conversation_id"))
        .order_by("-created_at")
        .values("content")[:1]
    )

    return (
        queryset.values("conversation_id")
        .annotate(
            message_count=Count("id"),
            latest_at=Max("created_at"),
            thread_name=Subquery(thread_name),
            first_user_content=Subquery(first_user_message),
            latest_content=Subquery(latest_message),
        )
        .order_by("-latest_at")
    )


def upsert_conversation_thread(
    thread_id: str,
    *,
    user_id: Optional[int] = None,
    name: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
) -> ConversationThread:
    normalized_name = normalize_thread_name(name)
    thread = ConversationThread.objects.filter(thread_id=thread_id).first()

    if not thread:
        thread = ConversationThread(thread_id=thread_id)

    if user_id is not None:
        thread.user_id = user_id

    if name is not None:
        thread.name = normalized_name

    if metadata is not None:
        merged_metadata = dict(thread.metadata or {})
        for key, value in metadata.items():
            if value is None:
                merged_metadata.pop(key, None)
            else:
                merged_metadata[key] = value
        thread.metadata = merged_metadata

    if tags is not None:
        thread.tags = [tag for tag in tags if tag]

    thread.save()
    return thread


def get_thread_author_identifier(thread_id: str) -> str:
    thread = ConversationThread.objects.select_related("user").filter(
        thread_id=thread_id
    ).first()
    if thread and thread.user:
        return thread.user.get_username()

    message = (
        ChatMessage.objects.select_related("user")
        .filter(conversation_id=thread_id, user__isnull=False)
        .order_by("created_at")
        .first()
    )
    if message and message.user:
        return message.user.get_username()

    return ""


def get_thread_owner_user_id(thread_id: str) -> int | None:
    thread = ConversationThread.objects.filter(thread_id=thread_id).first()
    if thread and thread.user_id:
        return thread.user_id

    return (
        ChatMessage.objects.filter(conversation_id=thread_id, user__isnull=False)
        .order_by("created_at")
        .values_list("user_id", flat=True)
        .first()
    )


def list_user_conversations(
    *,
    user_id: Optional[int] = None,
    limit: int = 10,
    offset: int = 0,
) -> Dict[str, Any]:
    limit = max(1, int(limit))
    offset = max(0, int(offset))

    summary_queryset = get_conversation_summary_rows(user_id=user_id)
    total = summary_queryset.count()

    if total == 0:
        return {
            "total": 0,
            "limit": limit,
            "offset": 0,
            "results": [],
            "has_prev": False,
            "has_next": False,
        }

    if offset >= total:
        offset = ((total - 1) // limit) * limit

    rows = list(summary_queryset[offset : offset + limit])

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [
            {
                "conversation_id": row["conversation_id"],
                "title": build_conversation_title(
                    explicit_name=row.get("thread_name"),
                    first_user_content=row.get("first_user_content"),
                    latest_content=row.get("latest_content"),
                ),
                "preview": truncate_text(row.get("latest_content"), length=100),
                "message_count": row["message_count"],
                "latest_at": row["latest_at"].strftime("%Y-%m-%d %H:%M")
                if row.get("latest_at")
                else "-",
            }
            for row in rows
        ],
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
    }


def get_conversation_messages(
    conversation_id: str,
    *,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    queryset = get_chat_queryset(user_id=user_id).filter(
        conversation_id=conversation_id
    )
    messages = list(queryset.order_by("created_at"))
    thread = get_thread_queryset(user_id=user_id).filter(thread_id=conversation_id).first()

    if not messages and not thread:
        raise ChatMessage.DoesNotExist()

    first_user_message = next(
        (msg.content for msg in messages if msg.role == "user" and msg.content),
        messages[0].content if messages else None,
    )

    return {
        "conversation_id": conversation_id,
        "title": build_conversation_title(
            explicit_name=thread.name if thread else None,
            first_user_content=first_user_message,
        ),
        "message_count": len(messages),
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
    }


def delete_conversation(
    conversation_id: str,
    *,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    queryset = get_chat_queryset(user_id=user_id).filter(
        conversation_id=conversation_id
    )
    messages = list(queryset.order_by("created_at"))
    thread = get_thread_queryset(user_id=user_id).filter(thread_id=conversation_id).first()

    if not messages and not thread:
        raise ChatMessage.DoesNotExist()

    first_user_message = next(
        (msg.content for msg in messages if msg.role == "user" and msg.content),
        messages[0].content if messages else None,
    )
    deleted_count = len(messages)
    queryset.delete()

    if thread:
        thread.delete()

    return {
        "conversation_id": conversation_id,
        "title": build_conversation_title(
            explicit_name=thread.name if thread else None,
            first_user_content=first_user_message,
        ),
        "deleted_count": deleted_count,
    }


def get_native_thread(thread_id: str, *, user_id: int) -> Optional[ThreadDict]:
    thread = get_thread_queryset(user_id=user_id).filter(thread_id=thread_id).first()
    messages = list(
        get_chat_queryset(user_id=user_id)
        .filter(conversation_id=thread_id)
        .order_by("created_at")
    )

    if not thread and not messages:
        return None

    user = None
    if thread and thread.user:
        user = thread.user
    elif messages and messages[0].user:
        user = messages[0].user

    first_user_message = next(
        (msg.content for msg in messages if msg.role == "user" and msg.content),
        messages[0].content if messages else None,
    )
    latest_content = messages[-1].content if messages else None
    created_at = thread.created_at if thread else (messages[0].created_at if messages else None)

    return ThreadDict(
        id=thread_id,
        createdAt=serialize_datetime(created_at),
        name=build_conversation_title(
            explicit_name=thread.name if thread else None,
            first_user_content=first_user_message,
            latest_content=latest_content,
        ),
        userId=str(user.id) if user else None,
        userIdentifier=user.get_username() if user else None,
        tags=list(thread.tags) if thread else [],
        metadata=dict(thread.metadata or {}) if thread else {},
        steps=[build_step_from_chat_message(message) for message in messages],
        elements=[],
    )


def get_native_thread_by_thread_id(thread_id: str) -> Optional[ThreadDict]:
    user_id = get_thread_owner_user_id(thread_id)
    if user_id is None:
        return None

    return get_native_thread(thread_id, user_id=user_id)


def list_native_threads(
    *,
    user_id: int,
    limit: int,
    cursor: str | None = None,
    search: str | None = None,
) -> PaginatedResponse[ThreadDict]:
    limit = max(1, int(limit))
    summary_rows = list(get_conversation_summary_rows(user_id=user_id))

    if search:
        lowered = search.lower()
        matching_message_thread_ids = set(
            ChatMessage.objects.filter(user_id=user_id, content__icontains=search)
            .values_list("conversation_id", flat=True)
            .distinct()
        )
        matching_named_thread_ids = set(
            ConversationThread.objects.filter(user_id=user_id, name__icontains=search)
            .values_list("thread_id", flat=True)
        )
        matching_ids = matching_message_thread_ids | matching_named_thread_ids
        filtered_rows = []
        for row in summary_rows:
            title = build_conversation_title(
                explicit_name=row.get("thread_name"),
                first_user_content=row.get("first_user_content"),
                latest_content=row.get("latest_content"),
            )
            if (
                row["conversation_id"] in matching_ids
                or lowered in title.lower()
                or lowered in (row.get("latest_content") or "").lower()
            ):
                filtered_rows.append(row)
        summary_rows = filtered_rows

    start_index = 0
    if cursor:
        for index, row in enumerate(summary_rows):
            if row["conversation_id"] == cursor:
                start_index = index + 1
                break

    page_rows = summary_rows[start_index : start_index + limit]
    page_ids = [row["conversation_id"] for row in page_rows]

    thread_map = {
        thread.thread_id: thread
        for thread in get_thread_queryset(user_id=user_id).filter(thread_id__in=page_ids)
    }
    messages_by_thread_id: dict[str, list[ChatMessage]] = defaultdict(list)
    for message in (
        get_chat_queryset(user_id=user_id)
        .filter(conversation_id__in=page_ids)
        .order_by("created_at")
    ):
        messages_by_thread_id[message.conversation_id].append(message)

    threads: list[ThreadDict] = []
    for row in page_rows:
        thread_id = row["conversation_id"]
        thread = thread_map.get(thread_id)
        messages = messages_by_thread_id.get(thread_id, [])
        user = None
        if thread and thread.user:
            user = thread.user
        elif messages and messages[0].user:
            user = messages[0].user

        title = build_conversation_title(
            explicit_name=row.get("thread_name") or (thread.name if thread else None),
            first_user_content=row.get("first_user_content"),
            latest_content=row.get("latest_content"),
        )
        created_at = thread.created_at if thread else (messages[0].created_at if messages else row.get("latest_at"))
        threads.append(
            ThreadDict(
                id=thread_id,
                createdAt=serialize_datetime(created_at),
                name=title,
                userId=str(user.id) if user else None,
                userIdentifier=user.get_username() if user else None,
                tags=list(thread.tags) if thread else [],
                metadata=dict(thread.metadata or {}) if thread else {},
                steps=[build_step_from_chat_message(message) for message in messages],
                elements=[],
            )
        )

    has_next_page = len(summary_rows) > start_index + limit
    start_cursor = threads[0]["id"] if threads else None
    end_cursor = threads[-1]["id"] if threads else None

    return PaginatedResponse(
        pageInfo=PageInfo(
            hasNextPage=has_next_page,
            startCursor=start_cursor,
            endCursor=end_cursor,
        ),
        data=threads,
    )


def delete_native_thread_by_thread_id(thread_id: str) -> bool:
    user_id = get_thread_owner_user_id(thread_id)
    if user_id is not None:
        delete_conversation(thread_id, user_id=user_id)
        return True

    thread = ConversationThread.objects.filter(thread_id=thread_id).first()
    if not thread:
        return False

    thread.delete()
    return True
