from datetime import datetime
from unittest.mock import patch

from asgiref.sync import async_to_sync
from chainlit.types import Feedback
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from .models import ChatMessage, ChatMessageFeedback
from .services.chainlit_data_layer import DjangoChainlitDataLayer
from .services.feedback_service import build_feedback_summary
from .services.ollama_service import (
    extract_problem_analytics_query,
    get_generation_num_predict,
    rerank_knowledge_items,
    should_use_structured_answer_mode,
)
from .services.langgraph_chat_service import plan_reply_with_langgraph
from .services.sqlserver_job_card_analytics_service import (
    _build_query_terms,
    classify_frequency,
    classify_trend,
)
from .services.sqlserver_job_card_ingestion_service import build_sqlserver_job_card_content
from .services.sqlserver_job_card_ingestion_service import import_sqlserver_job_cards
from .services.system_health_service import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    get_system_health_report,
)
from .services.term_grouping_service import (
    build_semantic_search_groups,
    build_semantic_search_text,
)


class ProblemAnalyticsHelpersTests(SimpleTestCase):
    def test_build_query_terms_splits_query(self):
        self.assertEqual(
            _build_query_terms("Sensor ชำรุด"),
            ["Sensor", "ชำรุด"],
        )

    def test_classify_frequency_high(self):
        self.assertEqual(
            classify_frequency(total_count=120, active_months=12, last_90_days=30),
            "often_very_high",
        )

    def test_classify_trend_up(self):
        self.assertEqual(
            classify_trend(last_90_days=12, previous_90_days=6),
            "trend_up",
        )

    def test_extract_problem_analytics_query_from_followup(self):
        history = [
            {"role": "user", "content": "Sensor ชำรุด แก้ยังไง"},
            {"role": "assistant", "content": "ลองเปลี่ยน sensor"},
        ]
        self.assertEqual(
            extract_problem_analytics_query(history, "ปัญหานี้เกิดกี่ครั้ง"),
            "Sensor ชำรุด",
        )

    def test_extract_problem_analytics_query_from_short_count_followup(self):
        history = [
            {"role": "user", "content": "วิธีแก้ปัญหา Sensor ชำรุด"},
            {"role": "assistant", "content": "ลองเปลี่ยน proximity sensor"},
        ]
        self.assertEqual(
            extract_problem_analytics_query(history, "เกิดขึ้นกี่ครั้งหรอครับ"),
            "Sensor ชำรุด",
        )

    def test_extract_problem_analytics_query_from_monthly_followup(self):
        history = [
            {"role": "user", "content": "วิธีแก้ปัญหา Sensor ชำรุด"},
            {"role": "assistant", "content": "ลองเปลี่ยน proximity sensor"},
        ]
        self.assertEqual(
            extract_problem_analytics_query(history, "ต่อเดือนเป็นยังไง"),
            "Sensor ชำรุด",
        )

    def test_extract_problem_analytics_query_from_count_phrase_followup(self):
        history = [
            {"role": "user", "content": "วิธีแก้ปัญหา Sensor ชำรุด"},
            {"role": "assistant", "content": "ลองเปลี่ยน proximity sensor"},
        ]
        self.assertEqual(
            extract_problem_analytics_query(history, "เป็นจำนวนครั้งเท่าไหร่หรอครับ"),
            "Sensor ชำรุด",
        )

    def test_extract_problem_analytics_query_from_top_problem_followup(self):
        history = [
            {"role": "user", "content": "วิธีแก้ปัญหา Sensor"},
            {"role": "assistant", "content": "ลองตรวจ sensor"},
        ]
        self.assertEqual(
            extract_problem_analytics_query(history, "ปัญหายอดฮิตคือ"),
            "Sensor",
        )

    def test_build_semantic_search_groups_expands_sensor_aliases(self):
        self.assertEqual(
            build_semantic_search_groups("PS Sensor ชำรุด"),
            [
                [
                    "Sensor",
                    "ps sensor",
                    "proximity sensor",
                    "prox sensor",
                    "photo sensor",
                    "photoelectric sensor",
                    "เซ็นเซอร์",
                ],
                ["ชำรุด"],
            ],
        )

    def test_build_semantic_search_text_adds_aliases(self):
        expanded = build_semantic_search_text("Sensor ชำรุด")
        self.assertIn("คำค้นใกล้เคียง:", expanded)
        self.assertIn("proximity sensor", expanded.lower())

    def test_build_semantic_keyword_lines_for_job_card_content(self):
        content = build_sqlserver_job_card_content(
            {
                "ID": "MT-1",
                "MC_NO": "G-01",
                "Description": "PS Sensor ทำงานผิดปกติ",
                "impact_quality": 0,
                "Problem": "Proximity Sensor เสีย",
                "Problem_Cause": "",
                "Problem_detail": "",
                "Position_name": "",
                "REPAIR_DETAIL": "",
            }
        )
        self.assertIn("คำค้นใกล้เคียงกลุ่ม Sensor", content)
        self.assertIn("proximity sensor", content.lower())

    def test_get_generation_num_predict_expands_for_broad_many_case_query(self):
        budget = get_generation_num_predict(
            "Box auto",
            {"knowledge_items": [{} for _ in range(20)]},
        )
        self.assertGreaterEqual(budget, 3072)

    def test_rerank_knowledge_items_prioritizes_alias_and_title_matches(self):
        knowledge_items = [
            {
                "content": "ตรวจดู spring และ stopper",
                "metadata": {"title": "MT0001 | Box auto spring ขาด", "source": "sql"},
                "distance": 0.9,
            },
            {
                "content": "เปลี่ยน proximity sensor ใหม่",
                "metadata": {"title": "MT0002 | PS Sensor ทำงานผิดปกติ", "source": "sql"},
                "distance": 1.1,
            },
        ]

        reranked = rerank_knowledge_items("Sensor", knowledge_items, limit=2)

        self.assertEqual(reranked[0]["metadata"]["title"], "MT0002 | PS Sensor ทำงานผิดปกติ")

    def test_should_use_structured_answer_mode_for_broad_query(self):
        self.assertTrue(
            should_use_structured_answer_mode(
                "วิธีแก้ปัญหา Box auto",
                {"knowledge_items": [{} for _ in range(10)]},
            )
        )


class LangGraphPlanningTests(SimpleTestCase):
    @patch("chatbot.services.langgraph_chat_service.prepare_reply_generation")
    def test_plan_reply_with_langgraph_routes_to_analytics(self, mock_prepare):
        mock_prepare.return_value = {
            "history": [],
            "knowledge_text": "",
            "sources": [{"source": "sql"}],
            "response_language": "th",
            "analytics_reply": "พบทั้งหมด 12 ครั้ง",
        }

        planned = plan_reply_with_langgraph("room-1", "Sensor ชำรุด เกิดกี่ครั้ง")

        self.assertEqual(planned["route"], "analytics")
        self.assertEqual(planned["reply"], "พบทั้งหมด 12 ครั้ง")
        self.assertEqual(planned["sources"], [{"source": "sql"}])

    @patch("chatbot.services.langgraph_chat_service.prepare_reply_generation")
    def test_plan_reply_with_langgraph_builds_langchain_messages(self, mock_prepare):
        mock_prepare.return_value = {
            "history": [{"role": "user", "content": "Sensor ชำรุด"}],
            "knowledge_text": "[แหล่งข้อมูล : MT2406-006]\nเปลี่ยน Proximity Sensor ใหม่",
            "sources": [{"source": "rag"}],
            "response_language": "th",
            "knowledge_items": [{"content": "เปลี่ยน Proximity Sensor ใหม่"}],
            "analytics_reply": "",
        }

        planned = plan_reply_with_langgraph("room-1", "วิธีแก้คืออะไร")

        self.assertEqual(planned["route"], "llm_generate")
        self.assertTrue(planned["langchain_messages"])
        self.assertEqual(planned["sources"], [{"source": "rag"}])


class SQLServerJobCardIngestionTests(TestCase):
    @patch("chatbot.services.sqlserver_job_card_ingestion_service.index_document")
    @patch("chatbot.services.sqlserver_job_card_ingestion_service.fetch_rows")
    def test_latest_job_create_date_includes_created_rows(
        self,
        mock_fetch_rows,
        mock_index_document,
    ):
        mock_fetch_rows.return_value = [
            {
                "ID": "MT-NEW",
                "MC_NO": "MC-01",
                "Description": "Newest row",
                "J_CREATE_DATE": datetime(2026, 5, 5, 10, 25),
            },
            {
                "ID": "MT-OLD",
                "MC_NO": "MC-02",
                "Description": "Older row",
                "J_CREATE_DATE": datetime(2026, 4, 8, 10, 21),
            },
        ]

        result = import_sqlserver_job_cards(
            schema="dbo",
            view_name="v_MT_JOB_CARD",
        )

        self.assertEqual(result["summary"].created_count, 2)
        self.assertEqual(result["latest_job_create_date"], "2026-05-05 10:25:00")
        self.assertEqual(mock_index_document.call_count, 2)


class SystemHealthReportTests(SimpleTestCase):
    @patch("chatbot.services.system_health_service.check_sync_checkpoint_health")
    @patch("chatbot.services.system_health_service.check_sqlserver_health")
    @patch("chatbot.services.system_health_service.check_ollama_health")
    def test_get_system_health_report_aggregates_warning(
        self,
        mock_ollama,
        mock_sqlserver,
        mock_checkpoint,
    ):
        mock_ollama.return_value = {
            "name": "ollama",
            "label": "Ollama",
            "status": STATUS_OK,
            "status_label": "ปกติ",
            "message": "ok",
            "details": {},
            "alerts": [],
        }
        mock_sqlserver.return_value = {
            "name": "sqlserver",
            "label": "SQL Server",
            "status": STATUS_WARNING,
            "status_label": "เตือน",
            "message": "warning",
            "details": {},
            "alerts": ["sql warning"],
        }
        mock_checkpoint.return_value = {
            "name": "sync_checkpoints",
            "label": "Sync Checkpoint",
            "status": STATUS_OK,
            "status_label": "ปกติ",
            "message": "ok",
            "details": {},
            "alerts": [],
        }

        report = get_system_health_report(include_live_checks=True)

        self.assertEqual(report["status"], STATUS_WARNING)
        self.assertIn("sql warning", report["alerts"])

    @patch("chatbot.services.system_health_service.check_sync_checkpoint_health")
    @patch("chatbot.services.system_health_service.check_sqlserver_health")
    @patch("chatbot.services.system_health_service.check_ollama_health")
    def test_get_system_health_report_aggregates_error(
        self,
        mock_ollama,
        mock_sqlserver,
        mock_checkpoint,
    ):
        mock_ollama.return_value = {
            "name": "ollama",
            "label": "Ollama",
            "status": STATUS_ERROR,
            "status_label": "ผิดปกติ",
            "message": "down",
            "details": {},
            "alerts": ["ollama down"],
        }
        mock_sqlserver.return_value = {
            "name": "sqlserver",
            "label": "SQL Server",
            "status": STATUS_OK,
            "status_label": "ปกติ",
            "message": "ok",
            "details": {},
            "alerts": [],
        }
        mock_checkpoint.return_value = {
            "name": "sync_checkpoints",
            "label": "Sync Checkpoint",
            "status": STATUS_OK,
            "status_label": "ปกติ",
            "message": "ok",
            "details": {},
            "alerts": [],
        }

        report = get_system_health_report(include_live_checks=True)

        self.assertEqual(report["status"], STATUS_ERROR)
        self.assertIn("ollama down", report["alerts"])


class FeedbackPersistenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="feedback-user",
            password="secret12345",
        )
        self.message = ChatMessage.objects.create(
            user=self.user,
            conversation_id="room-1",
            chainlit_step_id="assistant-step-1",
            role="assistant",
            content="คำตอบทดสอบ",
            model_name="qwen3:14b",
        )

    async def _save_feedback(self, feedback: Feedback) -> str:
        data_layer = DjangoChainlitDataLayer()
        return await data_layer.upsert_feedback(feedback)

    async def _delete_feedback(self, feedback_id: str) -> bool:
        data_layer = DjangoChainlitDataLayer()
        return await data_layer.delete_feedback(feedback_id)

    def test_upsert_feedback_persists_record(self):
        feedback_id = async_to_sync(self._save_feedback)(
            Feedback(
                forId="assistant-step-1",
                threadId="room-1",
                value=1,
                comment="ตรงครับ",
            )
        )

        stored = ChatMessageFeedback.objects.get(chainlit_feedback_id=feedback_id)
        self.assertEqual(stored.message_id, self.message.id)
        self.assertEqual(stored.user_id, self.user.id)
        self.assertEqual(stored.value, ChatMessageFeedback.VALUE_CORRECT)
        self.assertEqual(stored.comment, "ตรงครับ")

    def test_upsert_feedback_updates_same_user_message(self):
        initial_feedback_id = async_to_sync(self._save_feedback)(
            Feedback(forId="assistant-step-1", threadId="room-1", value=1)
        )

        updated_feedback_id = async_to_sync(self._save_feedback)(
            Feedback(
                forId="assistant-step-1",
                threadId="room-1",
                id=initial_feedback_id,
                value=0,
                comment="ยังไม่ตรง",
            )
        )

        self.assertEqual(initial_feedback_id, updated_feedback_id)
        self.assertEqual(ChatMessageFeedback.objects.count(), 1)
        stored = ChatMessageFeedback.objects.get(chainlit_feedback_id=updated_feedback_id)
        self.assertEqual(stored.value, ChatMessageFeedback.VALUE_INCORRECT)
        self.assertEqual(stored.comment, "ยังไม่ตรง")

    def test_delete_feedback_removes_record(self):
        feedback_id = async_to_sync(self._save_feedback)(
            Feedback(forId="assistant-step-1", threadId="room-1", value=1)
        )

        deleted = async_to_sync(self._delete_feedback)(feedback_id)

        self.assertTrue(deleted)
        self.assertFalse(
            ChatMessageFeedback.objects.filter(chainlit_feedback_id=feedback_id).exists()
        )

    def test_feedback_summary_counts_records(self):
        ChatMessageFeedback.objects.create(
            user=self.user,
            message=self.message,
            conversation_id=self.message.conversation_id,
            chainlit_step_id=self.message.chainlit_step_id or "",
            chainlit_feedback_id="fb-1",
            value=ChatMessageFeedback.VALUE_CORRECT,
        )
        message_2 = ChatMessage.objects.create(
            user=self.user,
            conversation_id="room-2",
            chainlit_step_id="assistant-step-2",
            role="assistant",
            content="คำตอบทดสอบ 2",
            model_name="qwen3:14b",
        )
        ChatMessageFeedback.objects.create(
            user=self.user,
            message=message_2,
            conversation_id=message_2.conversation_id,
            chainlit_step_id=message_2.chainlit_step_id or "",
            chainlit_feedback_id="fb-2",
            value=ChatMessageFeedback.VALUE_INCORRECT,
        )

        summary = build_feedback_summary(limit=10)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["positive"], 1)
        self.assertEqual(summary["negative"], 1)
        self.assertEqual(len(summary["recent"]), 2)
