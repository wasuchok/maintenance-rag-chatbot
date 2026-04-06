from django.urls import path
from .views import (
    chat_with_local_model,
    health_check,
    import_mt_job_card_view,
    sync_mt_job_card_view,
    knowledge_list_create,
    knowledge_detail,
    get_chat_history,
)

urlpatterns = [
    path("chat/", chat_with_local_model, name="chat-with-local-model"),
    path("chat/<str:conversation_id>/history/", get_chat_history, name="chat-history"),

    path("knowledge/", knowledge_list_create, name="knowledge-list-create"),
    path("knowledge/<int:document_id>/", knowledge_detail, name="knowledge-detail"),
    path(
        "knowledge/import/mt-job-cards/",
        import_mt_job_card_view,
        name="knowledge-import-mt-job-cards",
    ),
    path(
        "knowledge/sync/mt-job-cards/",
        sync_mt_job_card_view,
        name="knowledge-sync-mt-job-cards",
    ),

    path("health/", health_check, name="health-check"),
]
