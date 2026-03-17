import requests

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import ChatMessage, KnowledgeDocument
from .services.ollama_service import generate_reply_with_history
from .services.rag_service import index_document

@api_view(["POST"])
def chat_with_local_model(request):
    conversation_id = request.data.get("conversation_id", "").strip()
    message = request.data.get("message", "").strip()

    if not conversation_id:
        return Response(
            {"error": "conversation_id is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not message:
        return Response(
            {"error": "message is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        reply = generate_reply_with_history(conversation_id, message)

        user_msg = ChatMessage.objects.create(
            conversation_id=conversation_id,
            role="user",
            content=message,
            model_name="qwen2.5:14b"
        )

        assistant_msg = None

        if reply != "ขออภัย ตอบใหม่อีกครั้งเป็นภาษาไทยสั้น ๆ ได้ไหม":
            assistant_msg = ChatMessage.objects.create(
            conversation_id=conversation_id,
            role="assistant",
            content=reply,
            model_name="qwen2.5:14b"
            )

        return Response({
            "conversation_id": conversation_id,
            "reply": reply,
            "saved": {
                "user_message_id": user_msg.id,
                "assistant_message_id": assistant_msg.id if assistant_msg else None
            }
        })

    except requests.exceptions.RequestException as e:
        return Response(
            {"error": f"cannot connect to local model: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    except Exception as e:
        return Response(
            {"error": f"unexpected error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["GET"])
def health_check(request):
    return Response({
        "status": "ok",
        "service": "django-chatbot-api"
    })

@api_view(["POST"])
def add_knowledge(request):
    title = request.data.get("title", "").strip()
    content = request.data.get("content", "").strip()
    source = request.data.get("source", "").strip()

    if not title:
        return Response({"error": "title is required"}, status=status.HTTP_400_BAD_REQUEST)

    if not content:
        return Response({"error": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        doc = KnowledgeDocument.objects.create(
            title=title,
            content=content,
            source=source or None
        )

        index_document(doc)

        return Response({
            "message": "knowledge added successfully",
            "document_id": doc.id,
            "title": doc.title
        })

    except Exception as e:
        return Response(
            {"error": f"unexpected error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
@api_view(["GET"])
def get_chat_history(request, conversation_id):
    messages = (
        ChatMessage.objects
        .filter(conversation_id=conversation_id)
        .order_by("created_at")
    )

    data = [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "model_name": msg.model_name,
            "created_at": msg.created_at,
        }
        for msg in messages
    ]

    return Response({
        "conversation_id": conversation_id,
        "messages": data
    })