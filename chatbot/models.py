from django.db import models

class ChatMessage(models.Model):
    conversation_id = models.CharField(max_length=100, db_index=True)
    role = models.CharField(max_length=20)
    content = models.TextField()
    model_name = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class KnowledgeDocument(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    source = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.role}: {self.content[:30]}"