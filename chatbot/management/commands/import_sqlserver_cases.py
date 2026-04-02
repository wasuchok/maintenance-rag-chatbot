from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...services.sqlserver_case_ingestion_service import import_sqlserver_cases
from ...services.sqlserver_service import (
    SQLServerConfigurationError,
    SQLServerDependencyError,
)


class Command(BaseCommand):
    help = "ดึงเคสจาก SQL Server เข้า knowledge base และ index เข้า RAG"

    def add_arguments(self, parser):
        parser.add_argument("--table", dest="table", default=None, help="ชื่อ table")
        parser.add_argument("--schema", dest="schema", default=None, help="ชื่อ schema")
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=None,
            help="จำกัดจำนวนแถวที่ต้องการ import",
        )

    def handle(self, *args, **options):
        schema = (options.get("schema") or settings.SQLSERVER_CASES_SCHEMA or "dbo").strip()
        table = (options.get("table") or settings.SQLSERVER_CASES_TABLE or "").strip()
        limit = options.get("limit")

        if not table:
            raise CommandError(
                "ยังไม่ได้กำหนด table ที่ต้องการ import กรุณาระบุ --table หรือ SQLSERVER_CASES_TABLE"
            )

        try:
            result = import_sqlserver_cases(
                schema=schema,
                table=table,
                limit=limit,
            )
        except (SQLServerConfigurationError, SQLServerDependencyError) as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(f"import จาก SQL Server ไม่สำเร็จ: {exc}") from exc

        summary = result["summary"]
        self.stdout.write(
            self.style.SUCCESS(
                f"import เสร็จแล้วจาก {schema}.{table}"
            )
        )
        self.stdout.write(f"total rows: {summary.total_rows}")
        self.stdout.write(f"created: {summary.created_count}")
        self.stdout.write(f"updated: {summary.updated_count}")
        self.stdout.write(f"skipped: {summary.skipped_count}")
        self.stdout.write(f"errors: {summary.error_count}")

        errors = result["errors"]
        if errors:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("ตัวอย่าง error สูงสุด 10 รายการ"))
            for item in errors[:10]:
                self.stdout.write(
                    f"- {item.get('card_id', '-')} : {item.get('error', 'unknown error')}"
                )
