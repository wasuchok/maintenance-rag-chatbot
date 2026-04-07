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
from .sqlserver_job_card_analytics_service import (
    analyze_mt_job_card_problem,
    build_problem_analytics_source,
    build_problem_analytics_summary,
)

logger = logging.getLogger(__name__)

OLLAMA_URL = settings.OLLAMA_CHAT_URL
OLLAMA_MODEL = settings.OLLAMA_MODEL
OLLAMA_TEMPERATURE = settings.OLLAMA_TEMPERATURE
OLLAMA_THINK = settings.OLLAMA_THINK
OLLAMA_KEEP_ALIVE = settings.OLLAMA_KEEP_ALIVE
OLLAMA_NUM_PREDICT = settings.OLLAMA_NUM_PREDICT
RAG_ONLY_MODE = settings.RAG_ONLY_MODE
RAG_INCLUDE_CHAT_HISTORY = settings.RAG_INCLUDE_CHAT_HISTORY
RAG_SEARCH_TOP_K = settings.RAG_SEARCH_TOP_K

DEFAULT_RESPONSE_LANGUAGE = "th"
LANGUAGE_LABELS = {
    "th": "ภาษาไทย",
    "en": "ภาษาอังกฤษ",
    "ja": "ภาษาญี่ปุ่น",
}
RAG_ONLY_NO_CONTEXT_REPLIES = {
    "th": "ไม่พบข้อมูลที่เกี่ยวข้องในฐานความรู้ จึงตอบได้เพียงว่าไม่มีข้อมูลเพียงพอครับ",
    "en": "I could not find relevant information in the knowledge base, so I do not have enough information to answer.",
    "ja": "知識ベースに関連する情報が見つからなかったため、回答に必要な情報が不足しています。",
}
RAG_ONLY_GENERATION_ERROR_REPLIES = {
    "th": (
        "ขออภัยครับ ระบบถูกตั้งให้ตอบจากฐานความรู้เท่านั้น "
        "แต่ยังสรุปคำตอบจากข้อมูลอ้างอิงที่พบไม่ได้ กรุณาลองถามใหม่หรือเพิ่มข้อมูลในฐานความรู้ครับ"
    ),
    "en": (
        "Sorry, this system is configured to answer from the knowledge base first, "
        "but I could not produce a reliable answer from the retrieved references. "
        "Please try again or add more information to the knowledge base."
    ),
    "ja": (
        "申し訳ありません。このシステムはまず知識ベースに基づいて回答するよう設定されていますが、"
        "取得した参照情報から信頼できる回答を生成できませんでした。"
        "質問を変えるか、知識ベースに情報を追加してください。"
    ),
}
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
    r"ได้ไหม|ได้มั้ย|ยังไง|อย่างไร|เป็นอะไรไหม|ต้องยื่น|ต้องทำ|ต้องใช้|"
    r"กี่ครั้ง|จำนวนครั้ง|จำนวนเท่าไหร่|จำนวนเท่าไร|บ่อยไหม|บ่อยแค่ไหน|ถี่ไหม|ถี่แค่ไหน|ต่อเดือน|ต่อปี|รายเดือน|รายปี|แนวโน้ม|สถิติ|ยอดฮิต|ฮิตสุด|พบบ่อยสุด|ยอดนิยม)"
)
THAI_CHAR_PATTERN = re.compile(r"[\u0E00-\u0E7F]")
JAPANESE_CHAR_PATTERN = re.compile(r"[\u3040-\u30FF]")
LATIN_CHAR_PATTERN = re.compile(r"[A-Za-z]")

ENGLISH_RESPONSE_HINTS = (
    "ตอบเป็นภาษาอังกฤษ",
    "ตอบภาษาอังกฤษ",
    "ตอบอังกฤษ",
    "เป็นภาษาอังกฤษ",
    "in english",
    "answer in english",
    "reply in english",
    "respond in english",
    "english please",
    "英語で",
)
JAPANESE_RESPONSE_HINTS = (
    "ตอบเป็นภาษาญี่ปุ่น",
    "ตอบภาษาญี่ปุ่น",
    "ตอบญี่ปุ่น",
    "เป็นภาษาญี่ปุ่น",
    "in japanese",
    "answer in japanese",
    "reply in japanese",
    "respond in japanese",
    "japanese please",
    "日本語で",
)
THAI_RESPONSE_HINTS = (
    "ตอบเป็นภาษาไทย",
    "ตอบภาษาไทย",
    "ตอบไทย",
    "เป็นภาษาไทย",
    "in thai",
    "answer in thai",
    "reply in thai",
    "respond in thai",
    "thai please",
    "タイ語で",
)
ANALYTICS_HINT_PATTERN = re.compile(
    r"(กี่ครั้ง|จำนวนครั้ง|จำนวนเท่าไหร่|จำนวนเท่าไร|บ่อยไหม|บ่อยแค่ไหน|ถี่ไหม|ถี่แค่ไหน|ต่อเดือน|ต่อปี|รายเดือน|รายปี|"
    r"แนวโน้ม|สถิติ|ยอดฮิต|ฮิตสุด|พบบ่อยสุด|ยอดนิยม|อันดับ|top problem|top issue|most common|most frequent|"
    r"frequency|how many times|often|per month|per year|monthly|yearly|trend|"
    r"何回|頻度|月別|年別)",
    re.IGNORECASE,
)
ANALYTICS_GENERIC_REFERENCE_PATTERN = re.compile(
    r"^(?:ปัญหานี้|อาการนี้|เคสนี้|กรณีนี้|เรื่องนี้|อันนี้|รายการนี้|เรื่องดังกล่าว|"
    r"this issue|this problem|this case|it)",
    re.IGNORECASE,
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
        "think": OLLAMA_THINK,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_predict": OLLAMA_NUM_PREDICT,
        }
    }


def contains_thai(text: str) -> bool:
    return bool(THAI_CHAR_PATTERN.search(text or ""))


def contains_japanese(text: str) -> bool:
    raw_text = text or ""
    return bool(JAPANESE_CHAR_PATTERN.search(raw_text) or "日本語" in raw_text)


def contains_latin(text: str) -> bool:
    return bool(LATIN_CHAR_PATTERN.search(text or ""))


def detect_explicit_response_language(text: str) -> str | None:
    normalized = normalize_query_text(text)
    raw_text = text or ""

    if any(hint in normalized for hint in THAI_RESPONSE_HINTS) or "タイ語で" in raw_text:
        return "th"
    if any(hint in normalized for hint in ENGLISH_RESPONSE_HINTS):
        return "en"
    if any(hint in normalized for hint in JAPANESE_RESPONSE_HINTS):
        return "ja"

    return None


def detect_response_language_from_text(text: str) -> str:
    explicit_language = detect_explicit_response_language(text)
    if explicit_language:
        return explicit_language

    if contains_thai(text):
        return "th"
    if contains_japanese(text):
        return "ja"
    if contains_latin(text):
        return "en"
    return DEFAULT_RESPONSE_LANGUAGE


def detect_response_language(
    user_message: str,
    history: List[Dict[str, str]] | None = None,
) -> str:
    detected_language = detect_response_language_from_text(user_message)
    if detected_language != DEFAULT_RESPONSE_LANGUAGE:
        return detected_language

    if contains_thai(user_message):
        return "th"

    if history and looks_like_followup_question(user_message):
        anchor_message = get_followup_anchor_message(history, user_message)
        if anchor_message:
            return detect_response_language_from_text(anchor_message)

    return detected_language


def strip_response_language_directives(text: str) -> str:
    raw_text = (text or "").strip()
    stripped = raw_text

    replacements = [
        r"(?:ตอบ|สรุป|อธิบาย)\s*(?:เป็น)?\s*ภาษาอังกฤษ",
        r"(?:ตอบ|สรุป|อธิบาย)\s*(?:เป็น)?\s*อังกฤษ",
        r"(?:ตอบ|สรุป|อธิบาย)\s*(?:เป็น)?\s*ภาษาญี่ปุ่น",
        r"(?:ตอบ|สรุป|อธิบาย)\s*(?:เป็น)?\s*ญี่ปุ่น",
        r"(?:ตอบ|สรุป|อธิบาย)\s*(?:เป็น)?\s*ภาษาไทย",
        r"(?:ตอบ|สรุป|อธิบาย)\s*(?:เป็น)?\s*ไทย",
        r"\b(?:answer|reply|respond)\s+in\s+english\b",
        r"\b(?:answer|reply|respond)\s+in\s+japanese\b",
        r"\b(?:answer|reply|respond)\s+in\s+thai\b",
        r"\bin\s+english\b",
        r"\bin\s+japanese\b",
        r"\bin\s+thai\b",
        r"英語で",
        r"日本語で",
        r"タイ語で",
    ]

    for pattern in replacements:
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE)

    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" ,.;:-")
    return stripped or raw_text


def looks_like_problem_analytics_question(user_message: str) -> bool:
    normalized = normalize_query_text(strip_response_language_directives(user_message))
    if not normalized:
        return False
    return bool(ANALYTICS_HINT_PATTERN.search(normalized))


def strip_problem_subject_noise(text: str) -> str:
    stripped = strip_response_language_directives(text or "")
    replacements = [
        r"ปัญหายอดฮิต(?:คืออะไร|คือ|อะไร)?",
        r"อาการยอดฮิต(?:คืออะไร|คือ|อะไร)?",
        r"พบบ่อยสุด(?:คืออะไร|คือ|อะไร)?",
        r"ยอดนิยม(?:คืออะไร|คือ|อะไร)?",
        r"ฮิตสุด(?:คืออะไร|คือ|อะไร)?",
        r"เป็นจำนวนครั้งเท่าไหร่(?:หรอ)?",
        r"เป็นจำนวนเท่าไหร่(?:หรอ)?",
        r"จำนวนครั้งเท่าไหร่(?:หรอ)?",
        r"จำนวนเท่าไหร่(?:หรอ)?",
        r"จำนวนเท่าไร(?:หรอ)?",
        r"เกิดขึ้นกี่ครั้ง(?:หรอ)?",
        r"(?:เกิด)?กี่ครั้ง(?:แล้ว)?",
        r"จำนวนครั้ง",
        r"ยอดฮิต",
        r"ฮิตสุด",
        r"พบบ่อยสุด",
        r"ยอดนิยม",
        r"บ่อยไหม",
        r"บ่อยแค่ไหน",
        r"ถี่ไหม",
        r"ถี่แค่ไหน",
        r"ต่อเดือน(?:เป็นยังไง)?",
        r"ต่อปี(?:เป็นยังไง)?",
        r"รายเดือน",
        r"รายปี",
        r"แนวโน้ม(?:เป็นยังไง)?",
        r"สถิติ",
        r"\bhow many times\b",
        r"\bfrequency\b",
        r"\boften\b",
        r"\bper month\b",
        r"\bper year\b",
        r"\bmonthly\b",
        r"\byearly\b",
        r"\btrend\b",
        r"何回",
        r"頻度",
        r"月別",
        r"年別",
        r"(?:ช่วย)?สรุป(?:ให้)?",
        r"(?:ขอ)?ข้อมูล",
        r"หน่อย",
        r"หรอ",
        r"เหรอ",
        r"ครับ",
        r"ค่ะ",
        r"คะ",
        r"เกิดขึ้น",
        r"(?:แก้|แก้ไข)(?:ยังไง|อย่างไร|ยังไงบ้าง)?",
        r"วิธีแก้(?:คืออะไร|ยังไง|อย่างไร)?",
        r"เกิดจากอะไร",
        r"เป็นเพราะอะไร",
        r"มีเคสคล้ายไหม",
        r"คืออะไร",
        r"คือ",
        r"เป็นยังไง",
        r"อย่างไร",
        r"\?",
    ]

    for pattern in replacements:
        stripped = re.sub(pattern, " ", stripped, flags=re.IGNORECASE)

    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" ,.;:-")
    stripped = re.sub(r"^(?:วิธี|การ)\s+", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"^(?:ปัญหา|อาการ|เคส|กรณี)\s+", "", stripped, flags=re.IGNORECASE)
    return stripped


def extract_problem_analytics_query(
    history: List[Dict[str, str]],
    user_message: str,
) -> str:
    current_message = strip_response_language_directives((user_message or "").strip())
    has_generic_reference = bool(
        ANALYTICS_GENERIC_REFERENCE_PATTERN.search(normalize_query_text(current_message))
    )
    anchor_message = ""
    if looks_like_followup_question(current_message) or has_generic_reference:
        anchor_message = get_followup_anchor_message(history, current_message)

    cleaned_current = strip_problem_subject_noise(current_message)
    cleaned_anchor = strip_problem_subject_noise(anchor_message) if anchor_message else ""

    if has_generic_reference:
        return cleaned_anchor or cleaned_current

    if len(normalize_query_text(cleaned_current)) < 3:
        return cleaned_anchor or cleaned_current

    return cleaned_current


def build_problem_analytics_result(
    history: List[Dict[str, str]],
    user_message: str,
    response_language: str,
) -> Dict[str, object] | None:
    if not looks_like_problem_analytics_question(user_message):
        return None

    analytics_query = extract_problem_analytics_query(history, user_message)
    if not analytics_query:
        return None

    try:
        analytics = analyze_mt_job_card_problem(
            query=analytics_query,
            schema=settings.SQLSERVER_JOB_CARD_SCHEMA,
            view_name=settings.SQLSERVER_JOB_CARD_VIEW,
        )
    except Exception as exc:
        logger.warning("Problem analytics failed for query=%r error=%s", analytics_query, exc)
        return None

    return {
        "analytics_query": analytics_query,
        "analytics": analytics,
        "reply": build_problem_analytics_summary(
            analytics,
            language=response_language,
        ),
        "sources": [
            build_problem_analytics_source(
                query=analytics_query,
                schema=settings.SQLSERVER_JOB_CARD_SCHEMA,
                view_name=settings.SQLSERVER_JOB_CARD_VIEW,
            )
        ],
    }


def is_bad_reply(text: str) -> bool:
    if not text or not text.strip():
        return True
    if text.strip().startswith("Ollama error:"):
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

        # ข้าม assistant reply ที่ว่างหรือเป็นข้อความ error
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
    current_message = strip_response_language_directives((user_message or "").strip())
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


def get_response_language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language, LANGUAGE_LABELS[DEFAULT_RESPONSE_LANGUAGE])


def build_no_context_reply(language: str) -> str:
    return RAG_ONLY_NO_CONTEXT_REPLIES.get(
        language,
        RAG_ONLY_NO_CONTEXT_REPLIES[DEFAULT_RESPONSE_LANGUAGE],
    )


def build_generation_error_reply(language: str) -> str:
    return RAG_ONLY_GENERATION_ERROR_REPLIES.get(
        language,
        RAG_ONLY_GENERATION_ERROR_REPLIES[DEFAULT_RESPONSE_LANGUAGE],
    )


def build_messages(
    history: List[Dict[str, str]],
    user_message: str,
    strict: bool = False,
    knowledge_text: str = "",
    response_language: str = DEFAULT_RESPONSE_LANGUAGE,
) -> List[Dict[str, str]]:
    language_label = get_response_language_label(response_language)

    if knowledge_text:
        system_prompt = f"""
    คุณคือผู้ช่วย AI หลายภาษาที่ต้องตอบโดยยึดข้อมูลจากฐานความรู้เป็นหลัก

    กฎที่ต้องทำตาม:
    1. ใช้เฉพาะข้อมูลที่อยู่ในส่วน "ข้อมูลอ้างอิง" เท่านั้นเป็นหลักฐานในการตอบ
    2. ห้ามใช้ความรู้ทั่วไป ความจำเดิมของโมเดล หรือข้อมูลจากภายนอก แม้ว่าคุณจะรู้คำตอบ
    3. ถ้ามีประวัติสนทนาถูกส่งมา ให้ใช้เพื่อช่วยเข้าใจคำถามปัจจุบันเท่านั้น ไม่ใช่แหล่งข้อมูลอ้างอิง
    4. ถ้าข้อมูลอ้างอิงไม่มีคำตอบชัดเจน หรือไม่ครอบคลุมคำถาม ให้ตอบว่าไม่มีข้อมูลเพียงพอในฐานความรู้
    5. ห้ามเดา ห้ามแต่ง ห้ามสรุปเกินกว่าที่ข้อมูลอ้างอิงระบุไว้
    6. ตอบให้กระชับแต่ครอบคลุม ชัดเจน และใช้{language_label}เท่านั้น
    7. ตอบเฉพาะสิ่งที่ผู้ใช้ถาม ห้ามดึงรายละเอียดเรื่องอื่นที่ไม่ได้ถามมาเอง
    8. ถ้าคำถามเกี่ยวกับจำนวน ส่วนเกิน หรือการคำนวณ ให้คำนวณจากข้อมูลอ้างอิงโดยตรงและสรุปผลลัพธ์ให้ชัดเจน
    9. ถ้าข้อมูลอ้างอิงมีข้อมูล "ผู้ปฏิบัติงาน" หรือ "Worker" ให้ระบุในคำตอบด้วยเสมอ โดยใช้ป้ายกำกับที่เหมาะกับ{language_label}
    10. ถ้าข้อมูลอ้างอิงมีหลายเคสที่เกี่ยวข้อง ให้สรุปรวมจากหลายเคส ไม่ใช่ยึดแค่เคสเดียว
    11. ถ้ามีรูปแบบหรือแนวโน้มที่พบซ้ำ ให้สรุปเป็นภาพรวม เช่น สาเหตุที่พบบ่อย วิธีแก้ที่พบบ่อย หรือทีม/ผู้ปฏิบัติงานที่เกี่ยวข้อง
    12. ถ้าข้อมูลเพียงพอ ให้ตอบแบบละเอียดขึ้นและจัดเป็นหัวข้อที่อ่านง่าย เช่น ภาพรวม สาเหตุที่พบบ่อย วิธีแก้ที่พบบ่อย และตัวอย่างเคสที่เกี่ยวข้อง
    13. ถ้ามีหลายเคสที่ใกล้เคียงกัน ให้ยกตัวอย่างเคสสำคัญ 3-5 เคสพร้อมรายละเอียดที่ช่วยตัดสินใจได้
    """
    else:
        system_prompt = f"""
    คุณคือผู้ช่วย AI หลายภาษา

    กฎที่ต้องทำตาม:
    1. ถ้ามีประวัติสนทนาถูกส่งมา ให้ใช้เพื่อช่วยเข้าใจคำถามปัจจุบันเท่านั้น
    2. ตอบให้กระชับแต่ครอบคลุม ชัดเจน และใช้{language_label}เท่านั้น
    3. ตอบเฉพาะสิ่งที่ผู้ใช้ถาม ห้ามดึงรายละเอียดเรื่องอื่นที่ไม่ได้ถามมาเอง
    4. ถ้าคำถามเกี่ยวกับจำนวน ส่วนเกิน หรือการคำนวณ ให้สรุปผลลัพธ์ที่คำนวณได้ให้ชัดเจน
    5. หากไม่มั่นใจจริง ๆ ให้บอกตามตรงว่าไม่แน่ใจ แทนการแต่งข้อมูล
    6. ถ้าคำถามเป็นเชิงสรุปภาพรวมหรือมีหลายกรณีเกี่ยวข้อง ให้ตอบแบบละเอียดขึ้นและจัดเป็นหัวข้อที่อ่านง่าย
    """

    if strict and knowledge_text:
        system_prompt += f"""
ข้อบังคับเพิ่มเติม:
- คำตอบสุดท้ายต้องเป็น{language_label}
- อย่าสลับไปใช้ภาษาอื่น เว้นแต่เป็นชื่อเฉพาะ รหัสชิ้นส่วน หรือข้อความที่อยู่ในข้อมูลอ้างอิง
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
    response_language = detect_response_language(user_message, history)
    retrieval_query = build_retrieval_query(history, user_message)
    followup_anchor_message = ""
    followup_topic_tokens: List[str] = []
    analytics_result = build_problem_analytics_result(
        history,
        user_message,
        response_language,
    )

    if analytics_result is not None:
        return {
            "history": history,
            "response_language": response_language,
            "retrieval_query": analytics_result.get("analytics_query", ""),
            "followup_anchor_message": "",
            "followup_topic_tokens": [],
            "knowledge_items": [],
            "knowledge_text": "",
            "sources": analytics_result.get("sources", []),
            "analytics_reply": analytics_result.get("reply", ""),
            "analytics": analytics_result.get("analytics"),
        }

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
        "response_language": response_language,
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
    response_language = str(
        prepared.get("response_language") or DEFAULT_RESPONSE_LANGUAGE
    )
    return {
        "reply": build_no_context_reply(response_language),
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
    response_language = str(
        prepared.get("response_language") or DEFAULT_RESPONSE_LANGUAGE
    )
    analytics_reply = str(prepared.get("analytics_reply") or "").strip()

    if analytics_reply:
        await on_token(analytics_reply)
        return {
            "reply": analytics_reply,
            "sources": prepared.get("sources", []),
        }

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
        response_language=response_language,
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
        "reply": build_generation_error_reply(response_language),
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
    response_language = str(
        prepared.get("response_language") or DEFAULT_RESPONSE_LANGUAGE
    )
    analytics_reply = str(prepared.get("analytics_reply") or "").strip()

    if analytics_reply:
        return {
            "reply": analytics_reply,
            "sources": prepared.get("sources", []),
        }

    if should_block_for_missing_knowledge(prepared):
        return build_missing_knowledge_result(prepared)

    messages = build_messages(
        history,
        user_message,
        strict=has_grounded_knowledge(prepared),
        knowledge_text=knowledge_text,
        response_language=response_language,
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
        "reply": build_generation_error_reply(response_language),
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
