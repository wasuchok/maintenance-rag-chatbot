from django.urls import path
from .views import chat_with_local_model, get_chat_history, health_check, add_knowledge

urlpatterns = [
    path("chat/", chat_with_local_model, name="chat-with-local-model"),
    path("health/", health_check, name="health-check"),
    path("knowledge/", add_knowledge, name="add-knowledge"),
    path("chat/<str:conversation_id>/history/", get_chat_history, name="chat-history"),
]