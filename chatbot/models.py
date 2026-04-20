from django.conf import settings
from django.db import models


class ConversationThread(models.Model): ## เก็บข้อมูลการสนทนาแต่ละรอบไว้ในฐานข้อมูล
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


class ChatMessage(models.Model): ## เก็บข้อความ user และ assistant ที่ส่งไปมาระหว่างการสนทนาไว้ในฐานข้อมูล
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


class ChatMessageFeedback(models.Model): ## เก็บข้อมูลการให้ feedback แก่ข้อความใน chat ไว้ในฐานข้อมูล
    VALUE_INCORRECT = 0
    VALUE_CORRECT = 1
    VALUE_CHOICES = [
        (VALUE_INCORRECT, "Incorrect"),
        (VALUE_CORRECT, "Correct"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_message_feedback",
        blank=True,
        null=True,
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="feedback_items",
    )
    conversation_id = models.CharField(max_length=100, db_index=True)
    chainlit_step_id = models.CharField(max_length=100, db_index=True)
    chainlit_feedback_id = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        db_index=True,
    )
    value = models.SmallIntegerField(choices=VALUE_CHOICES)
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "message"],
                name="unique_chat_feedback_per_user_message",
            )
        ]

    def __str__(self):
        value_label = "correct" if self.value == self.VALUE_CORRECT else "incorrect"
        return f"{self.conversation_id}:{value_label}"


class KnowledgeDocument(models.Model): ## เก็บข้อมูลเอกสารความรู้ที่ใช้ในการสนทนาไว้ในฐานข้อมูล
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


class SyncCheckpoint(models.Model):
    STATUS_NEVER = "never"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_NEVER, "Never"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]

    key = models.CharField(max_length=255, unique=True, db_index=True)
    source_type = models.CharField(max_length=100)
    source_name = models.CharField(max_length=255)
    cursor_field = models.CharField(max_length=100, blank=True, null=True)
    cursor_value = models.CharField(max_length=32, blank=True, null=True)
    last_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NEVER,
        db_index=True,
    )
    last_run_started_at = models.DateTimeField(blank=True, null=True)
    last_run_finished_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key
