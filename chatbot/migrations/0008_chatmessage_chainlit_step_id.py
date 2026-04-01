from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0007_conversationthread"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="chainlit_step_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=100,
                null=True,
            ),
        ),
    ]
