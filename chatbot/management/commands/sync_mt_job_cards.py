from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...services.sqlserver_job_card_sync_service import (
    sync_sqlserver_job_cards_with_checkpoint,
)
from ...services.sqlserver_service import (
    SQLServerConfigurationError,
    SQLServerDependencyError,
)


class Command(BaseCommand):
    help = "sync ข้อมูลจาก SQL Server view v_MT_JOB_CARD แบบใช้ checkpoint เข้า knowledge base"

    def add_arguments(self, parser):
        parser.add_argument("--schema", dest="schema", default=None, help="ชื่อ schema")
        parser.add_argument("--view", dest="view_name", default=None, help="ชื่อ view")
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=None,
            help="จำกัดจำนวนแถวที่ต้องการ sync",
        )
        parser.add_argument(
            "--full",
            dest="full",
            action="store_true",
            help="บังคับดึงข้อมูลทั้งหมดโดยไม่ใช้ checkpoint รอบก่อน",
        )
        parser.add_argument(
            "--bootstrap-days",
            dest="bootstrap_days",
            type=int,
            default=None,
            help="ถ้ายังไม่มี checkpoint ให้ดึงเฉพาะข้อมูลย้อนหลัง N วันก่อน",
        )
        parser.add_argument(
            "--overlap-minutes",
            dest="overlap_minutes",
            type=int,
            default=None,
            help="เผื่อช่วงทับซ้อนย้อนหลัง N นาทีจาก checkpoint เพื่อกันข้อมูลตกหล่น",
        )

    def handle(self, *args, **options):
        schema = (
            options.get("schema") or settings.SQLSERVER_JOB_CARD_SCHEMA or "dbo"
        ).strip()
        view_name = (
            options.get("view_name") or settings.SQLSERVER_JOB_CARD_VIEW or "v_MT_JOB_CARD"
        ).strip()

        if not view_name:
            raise CommandError(
                "ยังไม่ได้กำหนด view ที่ต้องการ sync กรุณาระบุ --view หรือ SQLSERVER_JOB_CARD_VIEW"
            )

        try:
            result = sync_sqlserver_job_cards_with_checkpoint(
                schema=schema,
                view_name=view_name,
                limit=options.get("limit"),
                full=bool(options.get("full")),
                bootstrap_days=options.get("bootstrap_days"),
                overlap_minutes=options.get("overlap_minutes"),
            )
        except (SQLServerConfigurationError, SQLServerDependencyError) as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(f"sync mt job cards ไม่สำเร็จ: {exc}") from exc

        summary = result["summary"]
        checkpoint = result["checkpoint"]

        self.stdout.write(
            self.style.SUCCESS(
                f"sync เสร็จแล้วจาก {schema}.{view_name}"
            )
        )
        self.stdout.write(f"sync_mode: {result.get('sync_mode')}")
        self.stdout.write(f"used_since: {result.get('used_since') or '-'}")
        self.stdout.write(f"total rows: {summary.total_rows}")
        self.stdout.write(f"created: {summary.created_count}")
        self.stdout.write(f"updated: {summary.updated_count}")
        self.stdout.write(f"skipped: {summary.skipped_count}")
        self.stdout.write(f"errors: {summary.error_count}")
        self.stdout.write(f"checkpoint_before: {checkpoint.get('cursor_value_before') or '-'}")
        self.stdout.write(f"checkpoint_after: {checkpoint.get('cursor_value_after') or '-'}")
