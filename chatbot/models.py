from django.conf import settings
from django.db import models


class ConversationThread(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_threads",
        blank=True,
        null=True,
    )
    thread_id = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or self.thread_id


class ChatMessage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        blank=True,
        null=True,
    )
    conversation_id = models.CharField(max_length=100, db_index=True)
    chainlit_step_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
    )
    role = models.CharField(max_length=20)
    content = models.TextField()
    model_name = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class KnowledgeDocument(models.Model):
    VISIBILITY_PRIVATE = "private"
    VISIBILITY_SHARED = "shared"
    VISIBILITY_CHOICES = [
        (VISIBILITY_PRIVATE, "Private"),
        (VISIBILITY_SHARED, "Shared"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="knowledge_documents",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    source = models.CharField(max_length=255, blank=True, null=True)
    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_SHARED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
