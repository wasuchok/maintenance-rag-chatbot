import requests

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ChatMessage, KnowledgeDocument
from .services.chat_service import generate_and_store_reply
from .services.knowledge_access_service import (
    get_accessible_knowledge_queryset,
    get_knowledge_visibility_label,
    get_manageable_knowledge_queryset,
    normalize_knowledge_visibility,
)
from .services.knowledge_management_service import list_knowledge_documents
from .services.rag_service import index_document, delete_document_from_index


def get_request_user_id(request):
    if request.user.is_authenticated:
        return request.user.id
    return None


def can_manage_all_documents(request) -> bool:
    return bool(
        request.user.is_authenticated
        and (request.user.is_staff or request.user.is_superuser)
    )


@api_view(["POST"])
def chat_with_local_model(request):
    user_id = get_request_user_id(request)
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
        result = generate_and_store_reply(
            conversation_id,
            message,
            user_id=user_id,
        )
        return Response(result)
    

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
    
@api_view(["POST", "GET"])
def knowledge_list_create(request):
    user_id = get_request_user_id(request)
    manage_all = can_manage_all_documents(request)

    if request.method == "POST":
        if not manage_all:
            return Response(
                {"error": "permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        title = request.data.get("title", "").strip()
        content = request.data.get("content", "").strip()
        source = request.data.get("source", "").strip()
        visibility = normalize_knowledge_visibility(request.data.get("visibility"))

        if not title:
            return Response(
                {"error" : "title is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not content:
            return Response(
                {"error" : "content is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            doc = KnowledgeDocument.objects.create(
                owner=request.user if request.user.is_authenticated else None,
                title=title,
                content=content,
                source=source or None,
                visibility=visibility,
            )

            index_document(doc)

            return Response({
                "message" : "knowledge added successfully",
                "document_id" : doc.id,
                "title" : doc.title,
                "visibility" : doc.visibility,
                "visibility_label" : get_knowledge_visibility_label(doc.visibility),
            }, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            return Response(
                {"error" : f"unexpected error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
    limit = request.query_params.get("limit", 100)
    offset = request.query_params.get("offset", 0)
    page = list_knowledge_documents(
        limit=limit,
        offset=offset,
        user_id=user_id,
        can_manage_all=manage_all,
    )

    return Response({
        "count" : page["total"],
        "manageable_count" : page["manageable_total"],
        "results" : page["results"],
    })

@api_view(["GET", "PUT", "DELETE"])
def knowledge_detail(request, document_id):
    user_id = get_request_user_id(request)
    manage_all = can_manage_all_documents(request)

    accessible_queryset = get_accessible_knowledge_queryset(
        user_id=user_id,
        can_manage_all=manage_all,
    )
    manageable_queryset = get_manageable_knowledge_queryset(
        user_id=user_id,
        can_manage_all=manage_all,
    )

    if request.method == "GET":
        try:
            doc = accessible_queryset.select_related("owner").get(id=document_id)
        except KnowledgeDocument.DoesNotExist:
            return Response(
                {"error" : "Knowledge document not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            "id" : doc.id,
            "title" : doc.title,
            "content" : doc.content,
            "source" : doc.source,
            "created_at" : doc.created_at,
            "visibility" : doc.visibility,
            "visibility_label" : get_knowledge_visibility_label(doc.visibility),
            "owner_username" : (
                doc.owner.get_username() if doc.owner_id is not None else None
            ),
        })
    
    if request.method == "PUT":
        if not manage_all:
            return Response(
                {"error": "permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            doc = manageable_queryset.get(id=document_id)
        except KnowledgeDocument.DoesNotExist:
            return Response(
                {"error" : "Knowledge document not found or permission denied"},
                status=status.HTTP_404_NOT_FOUND
            )

        title = request.data.get("title", "").strip()
        content = request.data.get("content", "").strip()
        source = request.data.get("source", "").strip()
        visibility = normalize_knowledge_visibility(request.data.get("visibility"))

        if not title:
            return Response(
                {"error" : "title is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not content:
            return Response(
                {"error" : "content is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            delete_document_from_index(doc.id)

            doc.title = title
            doc.content = content
            doc.source = source or None
            doc.visibility = visibility
            if doc.owner_id is None and request.user.is_authenticated:
                doc.owner = request.user
            doc.save()

            index_document(doc)

            return Response({
                "message" : "knowledge document updated successfully",
                "document_id" : doc.id,
                "title" : doc.title,
                "source" : doc.source,
                "visibility" : doc.visibility,
                "visibility_label" : get_knowledge_visibility_label(doc.visibility),
            })
        
        except Exception as e:
            return Response(
                {"error" : f"unexpected error while updating knowledge: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
    if not manage_all:
        return Response(
            {"error": "permission denied"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        doc = manageable_queryset.get(id=document_id)
    except KnowledgeDocument.DoesNotExist:
        return Response(
            {"error" : "Knowledge document not found or permission denied"},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        delete_document_from_index(doc.id)
        doc.delete()

        return Response({
            "message" : "knowledge document deleted successfully",
            "document_id" : document_id
        })
    
    except Exception as e:
        return Response(
            {"error" : f"unexpected error while deleting knowledge: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_chat_history(request, conversation_id):
    queryset = ChatMessage.objects.filter(conversation_id=conversation_id)
    if request.user.is_authenticated:
        queryset = queryset.filter(user=request.user)
    else:
        queryset = queryset.filter(user__isnull=True)

    messages = (
        queryset.order_by("created_at")
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
        "conversation_id" : conversation_id,
        "messages" : data
    })

@api_view(["GET"])
def health_check(request):
    return Response({
        "status": "ok",
        "service": "django-chatbot-api"
    })

@api_view(["POST"])
def add_knowledge(request):
    user_id = get_request_user_id(request)
    if not can_manage_all_documents(request):
        return Response(
            {"error": "permission denied"},
            status=status.HTTP_403_FORBIDDEN,
        )

    title = request.data.get("title", "").strip()
    content = request.data.get("content", "").strip()
    source = request.data.get("source", "").strip()
    visibility = normalize_knowledge_visibility(request.data.get("visibility"))

    if not title:
        return Response({"error": "title is required"}, status=status.HTTP_400_BAD_REQUEST)

    if not content:
        return Response({"error": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        doc = KnowledgeDocument.objects.create(
            owner=request.user if request.user.is_authenticated else None,
            title=title, 
            content=content,
            source=source or None,
            visibility=visibility,
        )

        index_document(doc)

        return Response({
            "message": "knowledge added successfully",
            "document_id": doc.id,
            "title": doc.title,
            "visibility": doc.visibility,
            "visibility_label": get_knowledge_visibility_label(doc.visibility),
        })

    except Exception as e:
        return Response(
            {"error": f"unexpected error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
@api_view(["GET"])
def get_chat_history(request, conversation_id):
    queryset = ChatMessage.objects.filter(conversation_id=conversation_id)
    if request.user.is_authenticated:
        queryset = queryset.filter(user=request.user)
    else:
        queryset = queryset.filter(user__isnull=True)

    messages = (
        queryset.order_by("created_at")
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
