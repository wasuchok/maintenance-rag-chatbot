from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...services.sqlserver_service import (
    SQLServerConfigurationError,
    SQLServerDependencyError,
    test_sqlserver_connection,
)


class Command(BaseCommand):
    help = "ทดสอบการเชื่อมต่อ SQL Server ตามค่าที่ตั้งไว้ใน environment"

    def handle(self, *args, **options):
        try:
            result = test_sqlserver_connection()
        except (SQLServerConfigurationError, SQLServerDependencyError) as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(f"เชื่อมต่อ SQL Server ไม่สำเร็จ: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("เชื่อมต่อ SQL Server สำเร็จ"))
        self.stdout.write(f"client: {settings.SQLSERVER_CLIENT}")
        self.stdout.write(f"driver: {settings.SQLSERVER_DRIVER}")
        self.stdout.write(f"server: {result.get('server_name') or settings.SQLSERVER_HOST}")
        self.stdout.write(f"database: {result.get('database_name') or settings.SQLSERVER_DATABASE}")
        self.stdout.write(
            f"user: {result.get('login_name') or result.get('system_user') or settings.SQLSERVER_USERNAME}"
        )
        self.stdout.write(
            f"time: {result.get('checked_at') or result.get('current_time')}"
        )
