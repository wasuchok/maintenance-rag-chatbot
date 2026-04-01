import os
import sys
import uuid
from pathlib import Path

import django

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import chainlit as cl
import requests
from asgiref.sync import sync_to_async
from chainlit import make_async
from chainlit.context import context as chainlit_context
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q

from chatbot.services.chat_service import (
    EditableMessageNotAllowedError,
    EditableMessageNotFoundError,
    regenerate_reply_for_edited_message,
    stream_and_store_reply,
)
from chatbot.services.chainlit_data_layer import DjangoChainlitDataLayer
from chatbot.services.conversation_management_service import (
    build_chainlit_step_id,
    delete_conversation,
    get_editable_user_message_status,
    get_conversation_messages,
    list_user_conversations,
    truncate_text,
    upsert_auto_conversation_title,
)
from chatbot.services.knowledge_access_service import get_knowledge_visibility_label
from chatbot.services.knowledge_ingestion_service import ingest_knowledge_files
from chatbot.services.knowledge_management_service import (
    delete_all_knowledge_documents,
    delete_knowledge_document,
    get_knowledge_document_summary,
    list_knowledge_documents,
)
from chatbot.services.ollama_service import looks_like_followup_question

list_user_conversations_async = make_async(list_user_conversations)
get_conversation_messages_async = make_async(get_conversation_messages)
delete_conversation_async = make_async(delete_conversation)
get_editable_user_message_status_async = make_async(get_editable_user_message_status)
upsert_auto_conversation_title_async = make_async(upsert_auto_conversation_title)
ingest_knowledge_files_async = make_async(ingest_knowledge_files)
list_knowledge_documents_async = make_async(list_knowledge_documents)
get_knowledge_document_summary_async = make_async(get_knowledge_document_summary)
delete_knowledge_document_async = make_async(delete_knowledge_document)
delete_all_knowledge_documents_async = make_async(delete_all_knowledge_documents)

DOCUMENTS_PER_PAGE = 5
CONVERSATIONS_PER_PAGE = 8
KNOWLEDGE_DASHBOARD_SESSION_KEY = "knowledge_dashboard_message"
CONVERSATION_DASHBOARD_SESSION_KEY = "conversation_dashboard_message"
CHAT_MENU_MESSAGE_SESSION_KEY = "chat_menu_message"
CURRENT_CONVERSATION_TITLE_SESSION_KEY = "current_conversation_title"
UserModel = get_user_model()


@cl.data_layer
def get_data_layer():
    return DjangoChainlitDataLayer()


def get_chainlit_user():
    return cl.user_session.get("user")


def get_current_django_user_id() -> int | None:
    app_user = get_chainlit_user()
    metadata = getattr(app_user, "metadata", {}) or {}
    user_id = metadata.get("django_user_id")

    if user_id is None:
        return None

    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def get_current_user_can_manage_all() -> bool:
    app_user = get_chainlit_user()
    metadata = getattr(app_user, "metadata", {}) or {}
    return bool(metadata.get("is_staff") or metadata.get("is_superuser"))


def get_user_display_name() -> str:
    app_user = get_chainlit_user()
    return (
        getattr(app_user, "display_name", None)
        or getattr(app_user, "identifier", None)
        or "คุณ"
    )


def get_current_conversation_id() -> str | None:
    return cl.user_session.get("conversation_id")


def get_current_thread_id() -> str:
    return chainlit_context.session.thread_id


def activate_conversation(conversation_id: str, title: str | None = None) -> None:
    chainlit_context.session.thread_id = conversation_id
    chainlit_context.session.thread_id_to_resume = conversation_id
    set_current_conversation(conversation_id, title)


def set_current_conversation(conversation_id: str, title: str | None = None) -> None:
    cl.user_session.set("conversation_id", conversation_id)
    if title is not None:
        cl.user_session.set(CURRENT_CONVERSATION_TITLE_SESSION_KEY, title)


def get_current_conversation_title() -> str:
    return cl.user_session.get(CURRENT_CONVERSATION_TITLE_SESSION_KEY) or "ห้องสนทนาใหม่"


def get_default_upload_visibility() -> str:
    return "shared"


def set_current_upload_visibility(visibility: str) -> str:
    return "shared"


def get_current_upload_visibility() -> str:
    return "shared"


def authenticate_django_user(identifier: str, password: str):
    user = authenticate(username=identifier, password=password)

    if not user and identifier:
        candidates = list(
            UserModel.objects.filter(
                Q(username__iexact=identifier) | Q(email__iexact=identifier)
            )
            .order_by("id")
        )

        preferred_candidates = [
            candidate
            for candidate in candidates
            if (candidate.username or "").lower() == identifier.lower()
        ]
        fallback_candidates = [
            candidate
            for candidate in candidates
            if candidate not in preferred_candidates
        ]

        for candidate in preferred_candidates + fallback_candidates:
            if candidate.check_password(password):
                user = candidate
                break

    if not user or not user.is_active:
        return None

    return user


def get_uploaded_files(message: cl.Message) -> list[dict[str, str]]:
    files = []

    for element in message.elements or []:
        path = getattr(element, "path", None)
        if not path:
            continue

        files.append(
            {
                "name": getattr(element, "name", None) or Path(path).name,
                "path": str(path),
            }
        )

    return files


def build_upload_summary(upload_result: dict, visibility: str) -> str:
    successes = upload_result["successes"]
    errors = upload_result["errors"]
    visibility_label = get_knowledge_visibility_label(visibility)

    lines = []

    if successes:
        lines.append(
            f"เพิ่มเข้าฐานความรู้แบบ{visibility_label}แล้ว {len(successes)} ไฟล์"
        )
        for item in successes:
            lines.append(
                f"- {item['title']} (เอกสาร #{item['document_id']}, {item['characters']} ตัวอักษร, {item['visibility_label']})"
            )

    if errors:
        if lines:
            lines.append("")
        lines.append(f"มีไฟล์ที่เพิ่มไม่สำเร็จ {len(errors)} ไฟล์")
        for item in errors:
            lines.append(f"- {item['name']}: {item['error']}")

    return "\n".join(lines) if lines else "ไม่ได้รับไฟล์ที่ใช้เพิ่มฐานความรู้"


async def clear_visible_chat() -> None:
    for message in cl.chat_context.get():
        try:
            await message.remove()
        except Exception:
            continue

    cl.chat_context.clear()
    cl.user_session.set(KNOWLEDGE_DASHBOARD_SESSION_KEY, None)
    cl.user_session.set(CONVERSATION_DASHBOARD_SESSION_KEY, None)
    cl.user_session.set(CHAT_MENU_MESSAGE_SESSION_KEY, None)


def build_intro_actions(*, can_manage_knowledge: bool) -> list[cl.Action]:
    actions = []

    if can_manage_knowledge:
        actions.append(
            cl.Action(
                name="knowledge_list",
                payload={"offset": 0},
                label="ดูรายการเอกสาร",
            )
        )

    return actions


async def replay_conversation_messages(conversation_data: dict) -> None:
    user_author = get_user_display_name()

    for item in conversation_data["messages"]:
        role = item["role"]
        content = item["content"]
        message_id = build_chainlit_step_id(item["id"])

        if role == "user":
            await cl.Message(
                id=message_id,
                content=content,
                author=user_author,
                type="user_message",
                created_at=item.get("created_at"),
            ).send()
        elif role == "assistant":
            await cl.Message(
                id=message_id,
                content=content,
                created_at=item.get("created_at"),
            ).send()


async def refresh_conversation_title_from_user_text(
    conversation_id: str,
    user_text: str,
    *,
    user_id: int | None,
) -> str:
    if looks_like_followup_question(user_text):
        return get_current_conversation_title()

    title = await upsert_auto_conversation_title_async(
        conversation_id,
        user_id=user_id,
        title=truncate_text(user_text),
    )
    return title


async def start_new_chat(*, announce: bool = True) -> None:
    new_conversation_id = str(uuid.uuid4())
    await clear_visible_chat()
    activate_conversation(new_conversation_id, "ห้องสนทนาใหม่")

    if announce:
        await cl.Message(content="เริ่มห้องใหม่แล้วครับ").send()


async def open_conversation(conversation_id: str) -> None:
    conversation_data = await get_conversation_messages_async(
        conversation_id,
        user_id=get_current_django_user_id(),
    )
    await clear_visible_chat()
    activate_conversation(
        conversation_data["conversation_id"],
        conversation_data["title"],
    )
    await cl.Message(
        content=(
            f"เปิดห้องเก่าแล้ว: {conversation_data['title']}\n"
            f"ข้อความทั้งหมด {conversation_data['message_count']} รายการ"
        )
    ).send()
    await replay_conversation_messages(conversation_data)


async def reload_current_conversation(
    conversation_id: str,
    *,
    notice: str | None = None,
) -> None:
    conversation_data = await get_conversation_messages_async(
        conversation_id,
        user_id=get_current_django_user_id(),
    )
    await clear_visible_chat()
    activate_conversation(
        conversation_data["conversation_id"],
        conversation_data["title"],
    )
    if notice:
        await cl.Message(content=notice).send()
    await replay_conversation_messages(conversation_data)


def build_conversation_dashboard_actions(
    *,
    offset: int = 0,
    total: int = 0,
    results: list[dict] | None = None,
) -> list[cl.Action]:
    actions = [
        cl.Action(
            name="conversation_list",
            payload={"offset": offset},
            label="รีเฟรชรายการ",
        ),
        cl.Action(
            name="conversation_new",
            payload={},
            label="เริ่มแชตใหม่",
        ),
    ]

    if offset > 0:
        prev_offset = max(0, offset - CONVERSATIONS_PER_PAGE)
        actions.append(
            cl.Action(
                name="conversation_list",
                payload={"offset": prev_offset},
                label="ก่อนหน้า",
            )
        )

    if offset + CONVERSATIONS_PER_PAGE < total:
        actions.append(
            cl.Action(
                name="conversation_list",
                payload={"offset": offset + CONVERSATIONS_PER_PAGE},
                label="ถัดไป",
            )
        )

    for item in results or []:
        actions.append(
            cl.Action(
                name="conversation_open",
                payload={"conversation_id": item["conversation_id"]},
                label=f"เปิด {item['title']}",
            )
        )
        actions.append(
            cl.Action(
                name="conversation_delete_request",
                payload={"conversation_id": item["conversation_id"], "offset": offset},
                label=f"ลบ {item['title']}",
            )
        )

    return actions


def render_conversation_dashboard(page_data: dict) -> str:
    total = page_data["total"]
    offset = page_data["offset"]
    results = page_data["results"]
    current_conversation_id = get_current_conversation_id()

    if total == 0:
        return "ตอนนี้ยังไม่มีประวัติห้องสนทนาของคุณ"

    page_number = (offset // CONVERSATIONS_PER_PAGE) + 1
    page_count = ((total - 1) // CONVERSATIONS_PER_PAGE) + 1

    lines = [
        f"ห้องสนทนาของคุณทั้งหมด {total} ห้อง",
        f"หน้า {page_number}/{page_count}",
        "",
    ]

    for item in results:
        is_current = item["conversation_id"] == current_conversation_id
        current_marker = " (กำลังเปิดอยู่)" if is_current else ""
        lines.extend(
            [
                f"{item['title']}{current_marker}",
                f"id: {item['conversation_id']}",
                f"ล่าสุด: {item['latest_at']}",
                f"จำนวนข้อความ: {item['message_count']}",
                f"preview: {item['preview']}",
                "",
            ]
        )

    lines.append("กดปุ่มด้านล่างเพื่อเปิดห้องเก่า ลบห้อง หรือเริ่มห้องใหม่")
    return "\n".join(lines).strip()


async def send_conversation_dashboard(offset: int = 0) -> None:
    page_data = await list_user_conversations_async(
        user_id=get_current_django_user_id(),
        limit=CONVERSATIONS_PER_PAGE,
        offset=offset,
    )
    actions = build_conversation_dashboard_actions(
        offset=page_data["offset"],
        total=page_data["total"],
        results=page_data["results"],
    )
    dashboard_message = cl.user_session.get(CONVERSATION_DASHBOARD_SESSION_KEY)

    if dashboard_message:
        try:
            await dashboard_message.remove_actions()
        except Exception:
            dashboard_message = None

    if dashboard_message:
        dashboard_message.content = render_conversation_dashboard(page_data)
        dashboard_message.actions = actions
        await dashboard_message.update()
        return

    dashboard_message = await cl.Message(
        content=render_conversation_dashboard(page_data),
        actions=actions,
    ).send()
    cl.user_session.set(CONVERSATION_DASHBOARD_SESSION_KEY, dashboard_message)


def build_visibility_actions(current_visibility: str) -> list[cl.Action]:
    return []


def build_management_actions(
    *,
    offset: int = 0,
    total: int = 0,
    manageable_total: int = 0,
    results: list[dict] | None = None,
    current_visibility: str = "shared",
) -> list[cl.Action]:
    actions = [
        cl.Action(
            name="knowledge_list",
            payload={"offset": offset},
            label="รีเฟรชรายการ",
        ),
    ]

    if manageable_total > 0:
        actions.append(
            cl.Action(
                name="knowledge_delete_all_request",
                payload={"offset": offset},
                label="ลบเอกสารแชร์ทั้งหมด",
            )
        )

    if offset > 0:
        prev_offset = max(0, offset - DOCUMENTS_PER_PAGE)
        actions.append(
            cl.Action(
                name="knowledge_list",
                payload={"offset": prev_offset},
                label="ก่อนหน้า",
            )
        )

    if offset + DOCUMENTS_PER_PAGE < total:
        actions.append(
            cl.Action(
                name="knowledge_list",
                payload={"offset": offset + DOCUMENTS_PER_PAGE},
                label="ถัดไป",
            )
        )

    for item in results or []:
        if not item.get("can_delete"):
            continue
        actions.append(
            cl.Action(
                name="knowledge_delete_request",
                payload={"document_id": item["id"], "offset": offset},
                label=f"ลบ #{item['id']}",
            )
        )

    return actions


def render_knowledge_dashboard(page_data: dict, current_visibility: str) -> str:
    total = page_data["total"]
    manageable_total = page_data["manageable_total"]
    offset = page_data["offset"]
    results = page_data["results"]

    if total == 0:
        return "ตอนนี้ยังไม่มีเอกสาร shared ในฐานความรู้"

    page_number = (offset // DOCUMENTS_PER_PAGE) + 1
    page_count = ((total - 1) // DOCUMENTS_PER_PAGE) + 1

    lines = [
        f"เอกสาร shared ทั้งหมด {total} รายการ",
        f"เอกสารที่ admin ลบได้ {manageable_total} รายการ",
        f"หน้า {page_number}/{page_count}",
        "",
    ]

    for item in results:
        source = item["source"] or "-"
        preview = item["content_preview"] or "-"
        owner_username = item["owner_username"] or "-"
        lines.extend(
            [
                f"#{item['id']} {item['title']}",
                f"scope: {item['visibility_label']}",
                f"owner: {owner_username}",
                f"source: {source}",
                f"created: {item['created_at']}",
                f"preview: {preview}",
                "",
            ]
        )

    lines.append("รายการนี้ใช้สำหรับ admin ในการจัดการเอกสาร shared")
    return "\n".join(lines).strip()


async def send_knowledge_dashboard(offset: int = 0) -> None:
    if not get_current_user_can_manage_all():
        await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
        return

    current_visibility = get_current_upload_visibility()
    page_data = await list_knowledge_documents_async(
        limit=DOCUMENTS_PER_PAGE,
        offset=offset,
        user_id=get_current_django_user_id(),
        can_manage_all=get_current_user_can_manage_all(),
    )
    actions = build_management_actions(
        offset=page_data["offset"],
        total=page_data["total"],
        manageable_total=page_data["manageable_total"],
        results=page_data["results"],
        current_visibility=current_visibility,
    )
    dashboard_message = cl.user_session.get(KNOWLEDGE_DASHBOARD_SESSION_KEY)

    if dashboard_message:
        try:
            await dashboard_message.remove_actions()
        except Exception:
            dashboard_message = None

    if dashboard_message:
        dashboard_message.content = render_knowledge_dashboard(
            page_data,
            current_visibility,
        )
        dashboard_message.actions = actions
        await dashboard_message.update()
        return

    dashboard_message = await cl.Message(
        content=render_knowledge_dashboard(page_data, current_visibility),
        actions=actions,
    ).send()
    cl.user_session.set(KNOWLEDGE_DASHBOARD_SESSION_KEY, dashboard_message)


async def send_management_menu() -> None:
    if not get_current_user_can_manage_all():
        return

    current_visibility = get_current_upload_visibility()
    current_visibility_label = get_knowledge_visibility_label(current_visibility)
    actions = [
        cl.Action(
            name="knowledge_list",
            payload={"offset": 0},
            label="ดูรายการเอกสาร",
        ),
        cl.Action(
            name="knowledge_delete_all_request",
            payload={"offset": 0},
            label="ลบเอกสารแชร์ทั้งหมด",
        ),
    ]
    await cl.Message(
        content=f"เมนูจัดการฐานความรู้\nโหมดอัปโหลดตอนนี้: {current_visibility_label}",
        actions=actions,
    ).send()


@cl.password_auth_callback
async def password_auth_callback(username: str, password: str):
    identifier = (username or "").strip()
    user = await sync_to_async(
        authenticate_django_user,
        thread_sensitive=True,
    )(identifier, password)

    if not user or not user.is_active:
        print(f"Chainlit login failed for identifier={identifier!r}")
        return None

    display_name = user.get_full_name() or user.get_username()
    return cl.User(
        identifier=user.get_username(),
        display_name=display_name,
        metadata={
            "django_user_id": user.id,
            "username": user.get_username(),
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "provider": "credentials",
        },
    )


@cl.on_chat_start
async def on_chat_start() -> None:
    conversation_id = get_current_thread_id()
    activate_conversation(conversation_id, "ห้องสนทนาใหม่")
    current_visibility = set_current_upload_visibility(get_default_upload_visibility())
    current_visibility_label = get_knowledge_visibility_label(current_visibility)
    can_manage_knowledge = get_current_user_can_manage_all()
    display_name = get_user_display_name()

    intro_text = (
        f"พร้อมใช้งานครับ {display_name}\n"
        "พิมพ์คำถามได้เลย ระบบนี้จะใช้ Ollama และฐานความรู้ชุดเดียวกับ Django API เดิม\n"
        "ประวัติแชตของแต่ละบัญชีจะแยกจากกัน\n"
        + (
            f"บัญชี admin สามารถเพิ่มข้อมูลเข้าฐานความรู้แบบ{current_visibility_label}ได้ทันที\n"
            if can_manage_knowledge
            else "บัญชี user ใช้สำหรับถามคำถามจากฐานความรู้แบบแชร์เท่านั้น\n"
        )
        + "ประวัติห้องสนทนาอยู่ที่ sidebar ด้านซ้าย และสามารถกลับมาเปิดห้องเก่าได้"
    )

    await cl.Message(
        content=intro_text,
        actions=build_intro_actions(can_manage_knowledge=can_manage_knowledge),
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: dict) -> None:
    set_current_upload_visibility(get_default_upload_visibility())
    activate_conversation(
        thread["id"],
        thread.get("name") or "ห้องสนทนาใหม่",
    )
    cl.user_session.set(KNOWLEDGE_DASHBOARD_SESSION_KEY, None)
    cl.user_session.set(CONVERSATION_DASHBOARD_SESSION_KEY, None)
    cl.user_session.set(CHAT_MENU_MESSAGE_SESSION_KEY, None)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    user_id = get_current_django_user_id()
    can_manage_knowledge = get_current_user_can_manage_all()
    conversation_id = get_current_conversation_id() or get_current_thread_id()
    activate_conversation(conversation_id, get_current_conversation_title())

    uploaded_files = get_uploaded_files(message)
    user_text = (message.content or "").strip()
    upload_denied = False

    if uploaded_files:
        if not can_manage_knowledge:
            await cl.Message(
                content="บัญชีนี้ไม่มีสิทธิ์อัปโหลดเอกสาร ใช้สำหรับถามคำถามอย่างเดียวครับ"
            ).send()
            upload_denied = True
            uploaded_files = []
        else:
            upload_visibility = get_current_upload_visibility()
            status_message = await cl.Message(content="กำลังเพิ่มไฟล์เข้าฐานความรู้...").send()
            upload_result = await ingest_knowledge_files_async(
                uploaded_files,
                user_id=user_id,
                visibility=upload_visibility,
            )
            status_message.content = build_upload_summary(upload_result, upload_visibility)
            await status_message.update()
            await send_management_menu()

    if user_text in {"โหมดส่วนตัว", "/private", "โหมดแชร์", "/shared"}:
        if can_manage_knowledge:
            await cl.Message(content="ตอนนี้ระบบเปิดให้ admin จัดการเฉพาะเอกสาร shared ครับ").send()
        else:
            await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
        return

    if user_text in {"ดูห้องสนทนา", "ดูห้องของฉัน", "/chats"}:
        await cl.Message(
            content="ดูรายการห้องสนทนาได้จาก sidebar ด้านซ้ายของ Chainlit เลยครับ"
        ).send()
        return

    if user_text in {"แชตใหม่", "เริ่มแชตใหม่", "/newchat"}:
        await start_new_chat()
        return

    if not user_text:
        if uploaded_files or upload_denied:
            return
        await cl.Message(content="กรุณาพิมพ์ข้อความก่อนส่งครับ").send()
        return

    edit_status = await get_editable_user_message_status_async(
        message.id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    edit_state = edit_status["status"]

    if edit_state != "new_message":
        if uploaded_files:
            await reload_current_conversation(
                conversation_id,
                notice="ตอนนี้ยังไม่รองรับการแก้ข้อความพร้อมแนบไฟล์ใหม่ กรุณาส่งเป็นข้อความใหม่แทนครับ",
            )
            return

        if edit_state == "not_user_message":
            await reload_current_conversation(
                conversation_id,
                notice="แก้ไขได้เฉพาะข้อความคำถามของผู้ใช้เท่านั้นครับ",
            )
            return

        if edit_state == "not_latest_user_message":
            await reload_current_conversation(
                conversation_id,
                notice="ตอนนี้ระบบรองรับการแก้ไขเฉพาะคำถามล่าสุดของคุณในห้องนี้เท่านั้นครับ",
            )
            return

        streaming_message = cl.Message(content="")

        try:
            result = await regenerate_reply_for_edited_message(
                conversation_id,
                message.id,
                user_text,
                streaming_message.stream_token,
                user_id=user_id,
                assistant_step_id=streaming_message.id,
            )
        except (EditableMessageNotAllowedError, EditableMessageNotFoundError) as exc:
            await reload_current_conversation(conversation_id, notice=str(exc))
            return
        except requests.exceptions.RequestException as exc:
            await reload_current_conversation(
                conversation_id,
                notice=f"เชื่อมต่อ Ollama ไม่สำเร็จ: {exc}",
            )
            return
        except Exception as exc:
            await reload_current_conversation(
                conversation_id,
                notice=f"เกิดข้อผิดพลาดระหว่างแก้ข้อความ: {exc}",
            )
            return

        activate_conversation(
            conversation_id,
            result.get("conversation_title") or get_current_conversation_title(),
        )
        streaming_message.content = result["reply"]
        await streaming_message.send()
        if not looks_like_followup_question(user_text):
            updated_title = await refresh_conversation_title_from_user_text(
                conversation_id,
                user_text,
                user_id=user_id,
            )
            activate_conversation(conversation_id, updated_title)
        return

    if user_text in {"ดูรายการเอกสาร", "ดูเอกสาร", "/docs"}:
        if not can_manage_knowledge:
            await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
            return
        await send_knowledge_dashboard(0)
        return

    streaming_message = cl.Message(content="")

    try:
        result = await stream_and_store_reply(
            conversation_id,
            user_text,
            streaming_message.stream_token,
            user_id=user_id,
            user_step_id=message.id,
            assistant_step_id=streaming_message.id,
        )
    except requests.exceptions.RequestException as exc:
        if streaming_message.content:
            streaming_message.content = streaming_message.content.rstrip()
            await streaming_message.send()
        await cl.Message(content=f"เชื่อมต่อ Ollama ไม่สำเร็จ: {exc}").send()
        return
    except Exception as exc:
        if streaming_message.content:
            streaming_message.content = streaming_message.content.rstrip()
            await streaming_message.send()
        await cl.Message(content=f"เกิดข้อผิดพลาด: {exc}").send()
        return

    updated_title = await refresh_conversation_title_from_user_text(
        conversation_id,
        user_text,
        user_id=user_id,
    )
    activate_conversation(conversation_id, updated_title)

    streaming_message.content = result["reply"]
    await streaming_message.send()


@cl.action_callback("conversation_list")
async def on_conversation_list(action: cl.Action) -> None:
    offset = int(action.payload.get("offset", 0))
    await send_conversation_dashboard(offset)


@cl.action_callback("conversation_new")
async def on_conversation_new(action: cl.Action) -> None:
    await start_new_chat()


@cl.action_callback("conversation_open")
async def on_conversation_open(action: cl.Action) -> None:
    conversation_id = (action.payload.get("conversation_id") or "").strip()

    if not conversation_id:
        await cl.Message(content="ไม่พบรหัสห้องสนทนาที่ต้องการเปิด").send()
        return

    try:
        await open_conversation(conversation_id)
    except ObjectDoesNotExist:
        await cl.Message(content="ไม่พบห้องสนทนานี้แล้ว หรือคุณไม่มีสิทธิ์เข้าถึง").send()
        await send_conversation_dashboard(0)
    except Exception as exc:
        await cl.Message(content=f"เปิดห้องสนทนาไม่สำเร็จ: {exc}").send()


@cl.action_callback("conversation_delete_request")
async def on_conversation_delete_request(action: cl.Action) -> None:
    conversation_id = (action.payload.get("conversation_id") or "").strip()
    offset = int(action.payload.get("offset", 0))

    if not conversation_id:
        await cl.Message(content="ไม่พบรหัสห้องสนทนาที่ต้องการลบ").send()
        return

    try:
        conversation_data = await get_conversation_messages_async(
            conversation_id,
            user_id=get_current_django_user_id(),
        )
    except ObjectDoesNotExist:
        await cl.Message(content="ไม่พบห้องสนทนานี้แล้ว หรือคุณไม่มีสิทธิ์เข้าถึง").send()
        await send_conversation_dashboard(offset)
        return

    await cl.Message(
        content=(
            f"ยืนยันการลบห้องสนทนา\n"
            f"title: {conversation_data['title']}\n"
            f"id: {conversation_data['conversation_id']}\n"
            f"ข้อความทั้งหมด: {conversation_data['message_count']} รายการ"
        ),
        actions=[
            cl.Action(
                name="conversation_delete_confirm",
                payload={"conversation_id": conversation_id, "offset": offset},
                label="ยืนยันลบ",
            ),
            cl.Action(
                name="conversation_list",
                payload={"offset": offset},
                label="ยกเลิก",
            ),
        ],
    ).send()


@cl.action_callback("conversation_delete_current_request")
async def on_conversation_delete_current_request(action: cl.Action) -> None:
    conversation_id = get_current_conversation_id()

    if not conversation_id:
        await cl.Message(content="ยังไม่มีห้องสนทนาปัจจุบันให้ลบ").send()
        return

    try:
        conversation_data = await get_conversation_messages_async(
            conversation_id,
            user_id=get_current_django_user_id(),
        )
    except ObjectDoesNotExist:
        await start_new_chat(announce=False)
        await cl.Message(content="ห้องนี้ยังไม่มีประวัติที่บันทึกไว้ จึงไม่มีอะไรให้ลบ").send()
        return

    await cl.Message(
        content=(
            f"ยืนยันการลบห้องปัจจุบัน\n"
            f"title: {conversation_data['title']}\n"
            f"id: {conversation_data['conversation_id']}\n"
            f"ข้อความทั้งหมด: {conversation_data['message_count']} รายการ"
        ),
        actions=[
            cl.Action(
                name="conversation_delete_confirm",
                payload={"conversation_id": conversation_id, "offset": 0, "current": True},
                label="ยืนยันลบ",
            ),
            cl.Action(
                name="conversation_list",
                payload={"offset": 0},
                label="ดูห้องของฉัน",
            ),
        ],
    ).send()


@cl.action_callback("conversation_delete_confirm")
async def on_conversation_delete_confirm(action: cl.Action) -> None:
    conversation_id = (action.payload.get("conversation_id") or "").strip()
    offset = int(action.payload.get("offset", 0))
    deleting_current = bool(action.payload.get("current"))

    if not conversation_id:
        await cl.Message(content="ไม่พบรหัสห้องสนทนาที่ต้องการลบ").send()
        return

    try:
        result = await delete_conversation_async(
            conversation_id,
            user_id=get_current_django_user_id(),
        )
    except ObjectDoesNotExist:
        await cl.Message(content="ไม่พบห้องสนทนานี้แล้ว หรือคุณไม่มีสิทธิ์เข้าถึง").send()
        await send_conversation_dashboard(offset)
        return
    except Exception as exc:
        await cl.Message(content=f"ลบห้องสนทนาไม่สำเร็จ: {exc}").send()
        return

    is_current_conversation = conversation_id == get_current_conversation_id()
    if deleting_current or is_current_conversation:
        await start_new_chat(announce=False)
        await cl.Message(
            content=(
                f"ลบห้องสนทนา {result['title']} เรียบร้อยแล้ว\n"
                f"ลบข้อความทั้งหมด {result['deleted_count']} รายการ และเริ่มห้องใหม่ให้แล้ว"
            )
        ).send()
        return

    await cl.Message(
        content=(
            f"ลบห้องสนทนา {result['title']} เรียบร้อยแล้ว\n"
            f"ลบข้อความทั้งหมด {result['deleted_count']} รายการ"
        )
    ).send()
    await send_conversation_dashboard(offset)


@cl.action_callback("knowledge_list")
async def on_knowledge_list(action: cl.Action) -> None:
    offset = int(action.payload.get("offset", 0))
    await send_knowledge_dashboard(offset)


@cl.action_callback("knowledge_set_upload_private")
async def on_knowledge_set_upload_private(action: cl.Action) -> None:
    await cl.Message(content="ตอนนี้ระบบเปิดให้ admin จัดการเฉพาะเอกสาร shared ครับ").send()


@cl.action_callback("knowledge_set_upload_shared")
async def on_knowledge_set_upload_shared(action: cl.Action) -> None:
    await cl.Message(content="ตอนนี้ระบบใช้เอกสาร shared อย่างเดียวครับ").send()


@cl.action_callback("knowledge_delete_request")
async def on_knowledge_delete_request(action: cl.Action) -> None:
    if not get_current_user_can_manage_all():
        await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
        return

    document_id = int(action.payload["document_id"])
    offset = int(action.payload.get("offset", 0))

    try:
        document = await get_knowledge_document_summary_async(
            document_id,
            user_id=get_current_django_user_id(),
            can_manage_all=get_current_user_can_manage_all(),
        )
    except ObjectDoesNotExist:
        await cl.Message(content=f"ไม่พบเอกสาร #{document_id} แล้ว").send()
        await send_knowledge_dashboard(offset)
        return

    if not document["can_delete"]:
        await cl.Message(content=f"คุณไม่มีสิทธิ์ลบเอกสาร #{document_id}").send()
        await send_knowledge_dashboard(offset)
        return

    source = document["source"] or "-"
    owner_username = document["owner_username"] or "-"
    await cl.Message(
        content=(
            f"ยืนยันการลบเอกสาร #{document['id']}\n"
            f"title: {document['title']}\n"
            f"scope: {document['visibility_label']}\n"
            f"owner: {owner_username}\n"
            f"source: {source}"
        ),
        actions=[
            cl.Action(
                name="knowledge_delete_confirm",
                payload={"document_id": document_id, "offset": offset},
                label="ยืนยันลบ",
            ),
            cl.Action(
                name="knowledge_list",
                payload={"offset": offset},
                label="ยกเลิก",
            ),
        ],
    ).send()


@cl.action_callback("knowledge_delete_confirm")
async def on_knowledge_delete_confirm(action: cl.Action) -> None:
    if not get_current_user_can_manage_all():
        await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
        return

    document_id = int(action.payload["document_id"])
    offset = int(action.payload.get("offset", 0))

    try:
        result = await delete_knowledge_document_async(
            document_id,
            user_id=get_current_django_user_id(),
            can_manage_all=get_current_user_can_manage_all(),
        )
        await cl.Message(
            content=f"ลบเอกสาร #{result['document_id']} {result['title']} เรียบร้อยแล้ว"
        ).send()
    except ObjectDoesNotExist:
        await cl.Message(content=f"ไม่พบเอกสาร #{document_id} แล้ว").send()
    except Exception as exc:
        await cl.Message(content=f"ลบเอกสารไม่สำเร็จ: {exc}").send()
        return

    await send_knowledge_dashboard(offset)


@cl.action_callback("knowledge_delete_all_request")
async def on_knowledge_delete_all_request(action: cl.Action) -> None:
    if not get_current_user_can_manage_all():
        await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
        return

    offset = int(action.payload.get("offset", 0))
    await cl.Message(
        content="ยืนยันการลบเอกสาร shared ทั้งหมดหรือไม่",
        actions=[
            cl.Action(
                name="knowledge_delete_all_confirm",
                payload={"offset": offset},
                label="ยืนยันลบทั้งหมด",
            ),
            cl.Action(
                name="knowledge_list",
                payload={"offset": offset},
                label="ยกเลิก",
            ),
        ],
    ).send()


@cl.action_callback("knowledge_delete_all_confirm")
async def on_knowledge_delete_all_confirm(action: cl.Action) -> None:
    if not get_current_user_can_manage_all():
        await cl.Message(content="บัญชีนี้ไม่มีสิทธิ์จัดการเอกสาร").send()
        return

    try:
        result = await delete_all_knowledge_documents_async(
            user_id=get_current_django_user_id(),
            can_manage_all=get_current_user_can_manage_all(),
        )
    except Exception as exc:
        await cl.Message(content=f"ลบทั้งหมดไม่สำเร็จ: {exc}").send()
        return

    if result["deleted_count"] == 0:
        await cl.Message(content="ไม่มีเอกสาร shared ให้ลบ").send()
    else:
        await cl.Message(
            content=f"ลบเอกสาร shared เรียบร้อยแล้ว {result['deleted_count']} รายการ"
        ).send()

    await send_knowledge_dashboard(0)
