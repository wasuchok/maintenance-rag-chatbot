import asyncio
import json
import logging
import re
import threading
import time
import requests
from typing import Any, Awaitable, Callable, Dict, List, Optional
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Q
from ..models import ChatMessage

from .rag_service import search_knowledge

logger = logging.getLogger(__name__)

OLLAMA_URL = settings.OLLAMA_CHAT_URL
OLLAMA_MODEL = settings.OLLAMA_MODEL
OLLAMA_TEMPERATURE = settings.OLLAMA_TEMPERATURE
RAG_ONLY_MODE = settings.RAG_ONLY_MODE
RAG_INCLUDE_CHAT_HISTORY = settings.RAG_INCLUDE_CHAT_HISTORY
RAG_SEARCH_TOP_K = settings.RAG_SEARCH_TOP_K

RAG_ONLY_NO_CONTEXT_REPLY = (
    "ไม่พบข้อมูลที่เกี่ยวข้องในฐานความรู้ จึงตอบได้เพียงว่าไม่มีข้อมูลเพียงพอครับ"
)
RAG_ONLY_GENERATION_ERROR_REPLY = (
    "ขออภัยครับ ระบบถูกตั้งให้ตอบจากฐานความรู้เท่านั้น "
    "แต่ยังสรุปคำตอบจากข้อมูลอ้างอิงที่พบไม่ได้ กรุณาลองถามใหม่หรือเพิ่มข้อมูลในฐานความรู้ครับ"
)
FOLLOW_UP_PREFIXES = (
    "แล้ว",
    "งั้น",
    "ถ้างั้น",
    "ถ้าอย่างนั้น",
    "อย่างนั้น",
    "ในกรณีนี้",
    "กรณีนี้",
    "แบบนี้",
    "เรื่องนี้",
    "อันนี้",
    "ข้อนี้",
    "กรณีดังกล่าว",
)
TOPIC_TOKEN_PATTERN = re.compile(r"ลา[^\s,.;:!?()\[\]{}\"'“”‘’]{2,}")
CONTEXT_DEPENDENT_HINT_PATTERN = re.compile(
    r"(กี่วัน|กี่ปี|กี่บาท|เท่าไหร่|เท่าไร|เกินมากี่|เกินกี่|เหลือกี่|ได้กี่|"
    r"ได้ไหม|ได้มั้ย|ยังไง|อย่างไร|เป็นอะไรไหม|ต้องยื่น|ต้องทำ|ต้องใช้)"
)


def build_ollama_payload(
    messages: List[Dict[str, str]],
    *,
    stream: bool = False,
) -> Dict[str, Any]:
    return {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": stream,
        "options": {
            "temperature": OLLAMA_TEMPERATURE
        }
    }


def has_cjk(text: str) -> bool:
    # จีน ญี่ปุ่น เกาหลี
    return bool(re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))


def too_much_latin(text: str) -> bool:
    latin_chars = re.findall(r"[A-Za-z]", text)
    thai_chars = re.findall(r"[\u0E00-\u0E7F]", text)

    return len(latin_chars) > 40 and len(thai_chars) < 10


def is_bad_reply(text: str) -> bool:
    if not text or not text.strip():
        return True
    if has_cjk(text):
        return True
    if too_much_latin(text):
        return True
    return False


def get_conversation_history(
    conversation_id: str,
    limit: int = 6,
    user_id: Optional[int] = None,
    exclude_message_id: Optional[int] = None,
    before_message_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    queryset = ChatMessage.objects.filter(conversation_id=conversation_id)

    if user_id is None:
        queryset = queryset.filter(user__isnull=True)
    else:
        queryset = queryset.filter(user_id=user_id)

    if before_message_id is not None:
        reference_row = queryset.filter(id=before_message_id).values("created_at", "id").first()
        if not reference_row:
            return []

        queryset = queryset.filter(
            Q(created_at__lt=reference_row["created_at"])
            | Q(created_at=reference_row["created_at"], id__lt=reference_row["id"])
        )

    if exclude_message_id is not None:
        queryset = queryset.exclude(id=exclude_message_id)

    rows = queryset.order_by("-created_at")[:limit]

    rows = list(rows)[::-1]

    history = []
    for row in rows:
        if row.role not in ["user", "assistant", "system"]:
            continue

        content = (row.content or "").strip()
        if not content:
            continue

        # ข้ามข้อความ debug / error
        if content.startswith("ไม่พบข้อความตอบกลับ"):
            continue
        if content.startswith("Ollama error:"):
            continue

        # ข้าม assistant reply ที่ปนจีน/เพี้ยน
        if row.role == "assistant" and is_bad_reply(content):
            continue

        history.append({
            "role": row.role,
            "content": content
        })

    return history


def get_generation_history(
    history: List[Dict[str, str]],
    user_message: str,
) -> List[Dict[str, str]]:
    if not RAG_ONLY_MODE:
        return history

    if not RAG_INCLUDE_CHAT_HISTORY:
        return []

    if not looks_like_followup_question(user_message):
        return []

    anchor_message = get_followup_anchor_message(history, user_message)
    if not anchor_message:
        return []

    return [
        {
            "role": "user",
            "content": anchor_message,
        }
    ]


def normalize_query_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def looks_like_followup_question(user_message: str) -> bool:
    normalized = normalize_query_text(user_message)
    if not normalized:
        return False

    if normalized.startswith(FOLLOW_UP_PREFIXES):
        return True

    if normalized.startswith(("และ", "ส่วน", "อีก")) and len(normalized) <= 80:
        return True

    if (
        len(normalized) <= 60
        and CONTEXT_DEPENDENT_HINT_PATTERN.search(normalized)
        and not TOPIC_TOKEN_PATTERN.search(user_message or "")
    ):
        return True

    return False


def get_followup_anchor_message(
    history: List[Dict[str, str]],
    user_message: str,
) -> str:
    current_normalized = normalize_query_text(user_message)
    latest_previous_user_message = ""

    for item in reversed(history):
        if item.get("role") != "user":
            continue

        content = (item.get("content") or "").strip()
        if not content:
            continue

        if normalize_query_text(content) == current_normalized:
            continue

        if not latest_previous_user_message:
            latest_previous_user_message = content

        if not looks_like_followup_question(content):
            return content

    return latest_previous_user_message


def extract_topic_tokens(text: str) -> List[str]:
    seen = set()
    tokens: List[str] = []

    for raw_token in TOPIC_TOKEN_PATTERN.findall(text or ""):
        token = raw_token.strip()
        normalized = normalize_query_text(token)
        if len(normalized) < 3 or normalized in seen:
            continue

        seen.add(normalized)
        tokens.append(token)

    return tokens


def prioritize_knowledge_items_by_topic(
    knowledge_items: List[Dict],
    topic_tokens: List[str],
) -> List[Dict]:
    if not topic_tokens:
        return knowledge_items

    normalized_tokens = [normalize_query_text(token) for token in topic_tokens]
    matched_items: List[tuple[int, int, Dict]] = []
    unmatched_items: List[tuple[int, Dict]] = []

    for index, item in enumerate(knowledge_items):
        metadata = item.get("metadata", {}) or {}
        searchable_text = normalize_query_text(
            " ".join(
                [
                    item.get("content", "") or "",
                    metadata.get("title", "") or "",
                    metadata.get("source", "") or "",
                ]
            )
        )

        match_score = sum(1 for token in normalized_tokens if token in searchable_text)
        if match_score > 0:
            matched_items.append((match_score, index, item))
        else:
            unmatched_items.append((index, item))

    matched_items.sort(key=lambda row: (-row[0], row[1]))
    prioritized_items = [item for _, _, item in matched_items]
    prioritized_items.extend(item for _, item in unmatched_items)
    return prioritized_items


def build_retrieval_query(history: List[Dict[str, str]], user_message: str) -> str:
    current_message = (user_message or "").strip()
    if not current_message:
        return ""

    if not looks_like_followup_question(current_message):
        return current_message

    anchor_message = get_followup_anchor_message(history, current_message)
    if not anchor_message:
        return current_message

    return "\n".join([anchor_message, current_message])


def has_grounded_knowledge(prepared: Dict[str, object]) -> bool:
    knowledge_items = prepared.get("knowledge_items") or []
    knowledge_text = (prepared.get("knowledge_text") or "").strip()
    return bool(knowledge_items and knowledge_text)


def build_messages(
    history: List[Dict[str, str]],
    user_message: str,
    strict: bool = False,
    knowledge_text: str = "",
) -> List[Dict[str, str]]:
    if knowledge_text:
        system_prompt = """
    คุณคือผู้ช่วย AI ภาษาไทยที่ต้องตอบโดยยึดข้อมูลจากฐานความรู้เป็นหลัก

    กฎที่ต้องทำตาม:
    1. ใช้เฉพาะข้อมูลที่อยู่ในส่วน "ข้อมูลอ้างอิง" เท่านั้นเป็นหลักฐานในการตอบ
    2. ห้ามใช้ความรู้ทั่วไป ความจำเดิมของโมเดล หรือข้อมูลจากภายนอก แม้ว่าคุณจะรู้คำตอบ
    3. ถ้ามีประวัติสนทนาถูกส่งมา ให้ใช้เพื่อช่วยเข้าใจคำถามปัจจุบันเท่านั้น ไม่ใช่แหล่งข้อมูลอ้างอิง
    4. ถ้าข้อมูลอ้างอิงไม่มีคำตอบชัดเจน หรือไม่ครอบคลุมคำถาม ให้ตอบว่าไม่มีข้อมูลเพียงพอในฐานความรู้
    5. ห้ามเดา ห้ามแต่ง ห้ามสรุปเกินกว่าที่ข้อมูลอ้างอิงระบุไว้
    6. ตอบสั้น กระชับ ชัดเจน และเป็นภาษาไทยเท่านั้น
    7. ตอบเฉพาะสิ่งที่ผู้ใช้ถาม ห้ามดึงรายละเอียดเรื่องอื่นที่ไม่ได้ถามมาเอง
    8. ถ้าคำถามเกี่ยวกับจำนวน ส่วนเกิน หรือการคำนวณ ให้คำนวณจากข้อมูลอ้างอิงโดยตรงและสรุปผลลัพธ์ให้ชัดเจน
    """
    else:
        system_prompt = """
    คุณคือผู้ช่วย AI ภาษาไทย

    กฎที่ต้องทำตาม:
    1. ถ้ามีประวัติสนทนาถูกส่งมา ให้ใช้เพื่อช่วยเข้าใจคำถามปัจจุบันเท่านั้น
    2. ตอบสั้น กระชับ ชัดเจน และเป็นภาษาไทยเท่านั้น
    3. ตอบเฉพาะสิ่งที่ผู้ใช้ถาม ห้ามดึงรายละเอียดเรื่องอื่นที่ไม่ได้ถามมาเอง
    4. ถ้าคำถามเกี่ยวกับจำนวน ส่วนเกิน หรือการคำนวณ ให้สรุปผลลัพธ์ที่คำนวณได้ให้ชัดเจน
    5. หากไม่มั่นใจจริง ๆ ให้บอกตามตรงว่าไม่แน่ใจ แทนการแต่งข้อมูล
    """

    if strict and knowledge_text:
        system_prompt += """
ข้อบังคับเพิ่มเติม:
- ห้ามตอบเป็นภาษาจีนหรือภาษาอังกฤษ เว้นแต่เป็นชื่อเฉพาะที่อยู่ในข้อมูลอ้างอิง
- คำตอบสุดท้ายต้องเป็นภาษาไทย
- ถ้าไม่มีข้อมูลพอ ให้ปฏิเสธอย่างสุภาพแทนการคาดเดา
"""

    messages = [{"role": "system", "content": system_prompt}]

    if knowledge_text:
        messages.append({
            "role": "system",
            "content": f"ข้อมูลอ้างอิง:\n{knowledge_text}"
        })

    messages.extend(get_generation_history(history, user_message))
    messages.append({"role": "user", "content": user_message})
    return messages


def call_ollama(messages: List[Dict[str, str]]) -> dict:
    payload = build_ollama_payload(messages, stream=False)

    logger.debug("Sending chat request to Ollama with %s messages", len(messages))

    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    logger.debug("Ollama chat response status=%s", response.status_code)

    response.raise_for_status()
    return response.json()


def extract_reply(data: dict) -> str:
    if data.get("error"):
        return f"Ollama error: {data['error']}"

    message_obj = data.get("message")
    if isinstance(message_obj, dict):
        content = message_obj.get("content", "")
        if content and content.strip():
            return content.strip()

    if data.get("response"):
        return data["response"].strip()

    return ""


def extract_stream_token(data: dict) -> str:
    message_obj = data.get("message")
    if isinstance(message_obj, dict):
        content = message_obj.get("content", "")
        if content:
            return content

    if data.get("response"):
        return data["response"]

    return ""


def prepare_reply_generation(
    conversation_id: str,
    user_message: str,
    user_id: Optional[int] = None,
    exclude_message_id: Optional[int] = None,
    before_message_id: Optional[int] = None,
) -> Dict[str, object]:
    history = get_conversation_history(
        conversation_id,
        limit=6,
        user_id=user_id,
        exclude_message_id=exclude_message_id,
        before_message_id=before_message_id,
    )
    retrieval_query = build_retrieval_query(history, user_message)
    followup_anchor_message = ""
    followup_topic_tokens: List[str] = []

    if looks_like_followup_question(user_message):
        followup_anchor_message = get_followup_anchor_message(history, user_message)
        followup_topic_tokens = extract_topic_tokens(followup_anchor_message)

    knowledge_items = search_knowledge(
        retrieval_query,
        top_k=RAG_SEARCH_TOP_K,
        max_distance=1.2,
        user_id=user_id,
    )

    if followup_topic_tokens:
        knowledge_items = prioritize_knowledge_items_by_topic(
            knowledge_items,
            followup_topic_tokens,
        )

    if (
        not knowledge_items
        and not followup_topic_tokens
        and normalize_query_text(retrieval_query) != normalize_query_text(user_message)
    ):
        knowledge_items = search_knowledge(
            user_message,
            top_k=RAG_SEARCH_TOP_K,
            max_distance=1.2,
            user_id=user_id,
        )
        retrieval_query = user_message

    knowledge_text = build_knowledge_context(knowledge_items)
    source_items = clean_sources(knowledge_items)

    return {
        "history": history,
        "retrieval_query": retrieval_query,
        "followup_anchor_message": followup_anchor_message,
        "followup_topic_tokens": followup_topic_tokens,
        "knowledge_items": knowledge_items,
        "knowledge_text": knowledge_text,
        "sources": source_items,
    }


def should_block_for_missing_knowledge(prepared: Dict[str, object]) -> bool:
    if not RAG_ONLY_MODE:
        return False

    return not has_grounded_knowledge(prepared)


def build_missing_knowledge_result(prepared: Dict[str, object]) -> Dict[str, object]:
    return {
        "reply": RAG_ONLY_NO_CONTEXT_REPLY,
        "sources": prepared.get("sources", []),
    }


async def stream_ollama_response(
    messages: List[Dict[str, str]],
    on_token: Callable[[str], Awaitable[None]],
) -> dict:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def publish(item: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        payload = build_ollama_payload(messages, stream=True)
        final_data: dict = {}

        try:
            logger.debug(
                "Sending streaming chat request to Ollama with %s messages",
                len(messages),
            )
            with requests.post(
                OLLAMA_URL,
                json=payload,
                timeout=120,
                stream=True,
            ) as response:
                logger.debug(
                    "Ollama streaming chat response status=%s",
                    response.status_code,
                )
                response.raise_for_status()

                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue

                    data = json.loads(raw_line)
                    final_data = data

                    token = extract_stream_token(data)
                    if token:
                        publish({"type": "token", "token": token})

                    if data.get("done"):
                        break

        except Exception as exc:
            publish({"type": "error", "error": exc})
        else:
            publish({"type": "done", "data": final_data})
        finally:
            publish({"type": "end"})

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    final_data: dict = {}

    while True:
        item = await queue.get()
        item_type = item["type"]

        if item_type == "token":
            await on_token(item["token"])
            continue

        if item_type == "done":
            final_data = item["data"]
            continue

        if item_type == "error":
            raise item["error"]

        if item_type == "end":
            return final_data


async def stream_reply_with_history(
    conversation_id: str,
    user_message: str,
    on_token: Callable[[str], Awaitable[None]],
    user_id: Optional[int] = None,
    exclude_message_id: Optional[int] = None,
    before_message_id: Optional[int] = None,
) -> Dict[str, object]:
    prepared = await sync_to_async(
        prepare_reply_generation,
        thread_sensitive=True,
    )(
        conversation_id,
        user_message,
        user_id=user_id,
        exclude_message_id=exclude_message_id,
        before_message_id=before_message_id,
    )
    history = prepared["history"]
    knowledge_text = prepared["knowledge_text"]
    source_items = prepared["sources"]

    if should_block_for_missing_knowledge(prepared):
        return build_missing_knowledge_result(prepared)

    reply_parts: list[str] = []

    async def collect_and_forward(token: str) -> None:
        reply_parts.append(token)
        await on_token(token)

    messages = build_messages(
        history,
        user_message,
        strict=has_grounded_knowledge(prepared),
        knowledge_text=knowledge_text,
    )

    data = await stream_ollama_response(messages, collect_and_forward)
    reply = "".join(reply_parts).strip()

    if not reply and data.get("done_reason") == "load":
        reply_parts.clear()
        data = await stream_ollama_response(messages, collect_and_forward)
        reply = "".join(reply_parts).strip()

    if reply and not is_bad_reply(reply):
        return {
            "reply": reply,
            "sources": source_items,
        }

    return {
        "reply": RAG_ONLY_GENERATION_ERROR_REPLY,
        "sources": source_items,
    }


def generate_reply_with_history(
    conversation_id: str,
    user_message: str,
    user_id: Optional[int] = None,
    exclude_message_id: Optional[int] = None,
    before_message_id: Optional[int] = None,
) -> Dict[str, object]:
    prepared = prepare_reply_generation(
        conversation_id,
        user_message,
        user_id=user_id,
        exclude_message_id=exclude_message_id,
        before_message_id=before_message_id,
    )
    history = prepared["history"]
    knowledge_text = prepared["knowledge_text"]
    source_items = prepared["sources"]

    if should_block_for_missing_knowledge(prepared):
        return build_missing_knowledge_result(prepared)

    messages = build_messages(
        history,
        user_message,
        strict=has_grounded_knowledge(prepared),
        knowledge_text=knowledge_text,
    )

    data = call_ollama(messages)
    reply = extract_reply(data)

    if not reply and data.get("done_reason") == "load":
        time.sleep(1)
        data = call_ollama(messages)
        reply = extract_reply(data)

    if is_bad_reply(reply):
        time.sleep(1)
        data = call_ollama(messages)
        retry_reply = extract_reply(data)

        if retry_reply and not is_bad_reply(retry_reply):
            return {
                "reply": retry_reply,
                "sources": source_items,
            }

    if reply and not is_bad_reply(reply):
        return {
            "reply": reply,
            "sources": source_items,
        }

    return {
        "reply": RAG_ONLY_GENERATION_ERROR_REPLY,
        "sources": source_items,
    }


def build_knowledge_context(knowledge_items : List[Dict]) -> str:
    if not knowledge_items:
        return ""
    
    parts = []
    for item in knowledge_items:
        content = item.get("content", "").strip()
        metadata = item.get("metadata", {})
        title = metadata.get("title", "ไม่ระบุชื่อ")

        parts.append(f"[แหล่งข้อมูล : {title}]\n{content}")

    return "\n\n".join(parts)

def clean_sources(knowledge_items : List[Dict]) -> List[Dict]:
    cleaned = []

    for item in knowledge_items:
        metadata = item.get("metadata", {}) or {}

        cleaned.append({
            "title" : metadata.get("title"),
            "source" : metadata.get("source"),
            "chunk_index" : metadata.get("chunk_index"),
            "document_id" : metadata.get("document_id"),
            "distance" : item.get("distance")
        })

    return cleaned
