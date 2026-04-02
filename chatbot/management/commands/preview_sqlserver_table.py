import json

from django.core.management.base import BaseCommand, CommandError

from ...services.sqlserver_service import (
    SQLServerConfigurationError,
    SQLServerDependencyError,
    fetch_table_preview,
)


class Command(BaseCommand):
    help = "ดูตัวอย่างข้อมูลจาก SQL Server table ที่ต้องการ"

    def add_arguments(self, parser):
        parser.add_argument("--table", dest="table", default=None, help="ชื่อ table")
        parser.add_argument("--schema", dest="schema", default=None, help="ชื่อ schema")
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=10,
            help="จำนวนแถวที่ต้องการ preview",
        )

    def handle(self, *args, **options):
        try:
            rows = fetch_table_preview(
                table=options.get("table"),
                schema=options.get("schema"),
                limit=options.get("limit") or 10,
            )
        except (SQLServerConfigurationError, SQLServerDependencyError) as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(f"ดึงข้อมูลจาก SQL Server ไม่สำเร็จ: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(f"preview ได้ {len(rows)} แถว")
        )
        for index, row in enumerate(rows, start=1):
            self.stdout.write(f"[row {index}]")
            self.stdout.write(
                json.dumps(row, ensure_ascii=False, default=str, indent=2)
            )
