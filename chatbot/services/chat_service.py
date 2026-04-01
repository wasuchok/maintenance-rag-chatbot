from typing import Any, Awaitable, Callable, Dict, Optional

from asgiref.sync import sync_to_async

from django.db import transaction

from ..models import ChatMessage
from .conversation_management_service import (
    delete_messages_after,
    get_chat_message_for_step,
    get_conversation_messages,
    has_later_user_messages,
)
from .ollama_service import (
    OLLAMA_MODEL,
    generate_reply_with_history,
    stream_reply_with_history,
)


class EditableMessageNotFoundError(LookupError):
    pass


class EditableMessageNotAllowedError(ValueError):
    pass


def save_chat_exchange(
    conversation_id: str,
    message: str,
    reply: str,
    user_id: Optional[int] = None,
    user_step_id: Optional[str] = None,
    assistant_step_id: Optional[str] = None,
) -> Dict[str, int]:
    with transaction.atomic():
        user_msg = ChatMessage.objects.create(
            user_id=user_id,
            conversation_id=conversation_id,
            chainlit_step_id=user_step_id,
            role="user",
            content=message,
            model_name=OLLAMA_MODEL,
        )

        assistant_msg = ChatMessage.objects.create(
            user_id=user_id,
            conversation_id=conversation_id,
            chainlit_step_id=assistant_step_id,
            role="assistant",
            content=reply,
            model_name=OLLAMA_MODEL,
        )

    return {
        "user_message_id": user_msg.id,
        "assistant_message_id": assistant_msg.id,
    }


def generate_and_store_reply(
    conversation_id: str,
    message: str,
    user_id: Optional[int] = None,
    user_step_id: Optional[str] = None,
    assistant_step_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = generate_reply_with_history(
        conversation_id,
        message,
        user_id=user_id,
    )
    reply = result["reply"]
    sources = result["sources"]
    saved = save_chat_exchange(
        conversation_id,
        message,
        reply,
        user_id=user_id,
        user_step_id=user_step_id,
        assistant_step_id=assistant_step_id,
    )


    return {
        "conversation_id": conversation_id,
        "reply": reply,
        "sources": sources,
        "saved": saved,
    }


async def stream_and_store_reply(
    conversation_id: str,
    message: str,
    on_token: Callable[[str], Awaitable[None]],
    user_id: Optional[int] = None,
    user_step_id: Optional[str] = None,
    assistant_step_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = await stream_reply_with_history(
        conversation_id,
        message,
        on_token,
        user_id=user_id,
    )
    reply = result["reply"]
    sources = result["sources"]
    saved = await sync_to_async(save_chat_exchange, thread_sensitive=True)(
        conversation_id,
        message,
        reply,
        user_id,
        user_step_id,
        assistant_step_id,
    )

    return {
        "conversation_id": conversation_id,
        "reply": reply,
        "sources": sources,
        "saved": saved,
    }


def _get_editable_user_message(
    conversation_id: str,
    step_id: str,
    *,
    user_id: Optional[int] = None,
) -> ChatMessage:
    user_message = get_chat_message_for_step(
        step_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if not user_message:
        raise EditableMessageNotFoundError("ไม่พบข้อความที่ต้องการแก้ไขในห้องนี้")

    if user_message.role != "user":
        raise EditableMessageNotAllowedError("แก้ไขได้เฉพาะข้อความคำถามของผู้ใช้เท่านั้น")

    if has_later_user_messages(user_message):
        raise EditableMessageNotAllowedError(
            "ตอนนี้รองรับการแก้ไขเฉพาะคำถามล่าสุดของคุณในห้องนี้เท่านั้น"
        )

    return user_message


def _apply_edited_user_message_and_save_reply(
    conversation_id: str,
    user_message_id: int,
    step_id: str,
    message: str,
    reply: str,
    *,
    user_id: Optional[int] = None,
    assistant_step_id: Optional[str] = None,
) -> Dict[str, int]:
    with transaction.atomic():
        queryset = ChatMessage.objects.select_for_update().filter(
            id=user_message_id,
            conversation_id=conversation_id,
        )

        if user_id is None:
            queryset = queryset.filter(user__isnull=True)
        else:
            queryset = queryset.filter(user_id=user_id)

        user_message = queryset.first()
        if not user_message:
            raise EditableMessageNotFoundError("ไม่พบข้อความที่ต้องการแก้ไขในห้องนี้")

        if user_message.role != "user":
            raise EditableMessageNotAllowedError(
                "แก้ไขได้เฉพาะข้อความคำถามของผู้ใช้เท่านั้น"
            )

        if has_later_user_messages(user_message):
            raise EditableMessageNotAllowedError(
                "ตอนนี้รองรับการแก้ไขเฉพาะคำถามล่าสุดของคุณในห้องนี้เท่านั้น"
            )

        delete_messages_after(user_message)
        user_message.content = message
        user_message.chainlit_step_id = step_id
        user_message.save(update_fields=["content", "chainlit_step_id"])

        assistant_msg = ChatMessage.objects.create(
            user_id=user_id,
            conversation_id=conversation_id,
            chainlit_step_id=assistant_step_id,
            role="assistant",
            content=reply,
            model_name=OLLAMA_MODEL,
        )

    return {
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_msg.id,
    }


async def regenerate_reply_for_edited_message(
    conversation_id: str,
    step_id: str,
    message: str,
    on_token: Callable[[str], Awaitable[None]],
    *,
    user_id: Optional[int] = None,
    assistant_step_id: Optional[str] = None,
) -> Dict[str, Any]:
    user_message = await sync_to_async(_get_editable_user_message, thread_sensitive=True)(
        conversation_id,
        step_id,
        user_id=user_id,
    )

    result = await stream_reply_with_history(
        conversation_id,
        message,
        on_token,
        user_id=user_id,
        before_message_id=user_message.id,
    )
    reply = result["reply"]
    sources = result["sources"]
    saved = await sync_to_async(
        _apply_edited_user_message_and_save_reply,
        thread_sensitive=True,
    )(
        conversation_id,
        user_message.id,
        step_id,
        message,
        reply,
        user_id=user_id,
        assistant_step_id=assistant_step_id,
    )
    conversation = await sync_to_async(get_conversation_messages, thread_sensitive=True)(
        conversation_id,
        user_id=user_id,
    )

    return {
        "conversation_id": conversation_id,
        "reply": reply,
        "sources": sources,
        "saved": saved,
        "conversation_title": conversation["title"],
        "edited": True,
    }
