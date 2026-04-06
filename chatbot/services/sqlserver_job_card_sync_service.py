from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from ..models import SyncCheckpoint
from .sqlserver_job_card_ingestion_service import import_sqlserver_job_cards

SYNC_SOURCE_TYPE = "sqlserver_job_cards"
SYNC_CURSOR_FIELD = "J_CREATE_DATE"
CURSOR_VALUE_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_job_card_checkpoint_key(*, schema: str, view_name: str) -> str:
    return f"{SYNC_SOURCE_TYPE}:{schema}.{view_name}"


def parse_checkpoint_cursor_value(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    try:
        return datetime.strptime(normalized, CURSOR_VALUE_FORMAT)
    except ValueError:
        return datetime.fromisoformat(normalized)


def format_checkpoint_cursor_value(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime(CURSOR_VALUE_FORMAT)


def get_or_create_job_card_checkpoint(
    *,
    schema: str,
    view_name: str,
    checkpoint_key: str | None = None,
) -> SyncCheckpoint:
    key = checkpoint_key or build_job_card_checkpoint_key(
        schema=schema,
        view_name=view_name,
    )
    checkpoint, _ = SyncCheckpoint.objects.get_or_create(
        key=key,
        defaults={
            "source_type": SYNC_SOURCE_TYPE,
            "source_name": f"{schema}.{view_name}",
            "cursor_field": SYNC_CURSOR_FIELD,
        },
    )
    return checkpoint


def sync_sqlserver_job_cards_with_checkpoint(
    *,
    schema: str,
    view_name: str,
    limit: int | None = None,
    full: bool = False,
    bootstrap_days: int | None = None,
    overlap_minutes: int | None = None,
    checkpoint_key: str | None = None,
) -> dict[str, object]:
    checkpoint = get_or_create_job_card_checkpoint(
        schema=schema,
        view_name=view_name,
        checkpoint_key=checkpoint_key,
    )
    now = timezone.now()
    overlap_minutes = (
        settings.SQLSERVER_JOB_CARD_SYNC_OVERLAP_MINUTES
        if overlap_minutes is None
        else max(0, int(overlap_minutes))
    )

    since: datetime | None = None
    mode = "full"
    checkpoint_cursor_before = checkpoint.cursor_value
    parsed_checkpoint_cursor = parse_checkpoint_cursor_value(checkpoint.cursor_value)

    if not full and parsed_checkpoint_cursor is not None:
        mode = "checkpoint"
        since = parsed_checkpoint_cursor - timedelta(minutes=overlap_minutes)
    elif not full and bootstrap_days is not None:
        mode = "bootstrap_days"

    checkpoint.last_status = SyncCheckpoint.STATUS_RUNNING
    checkpoint.last_run_started_at = now
    checkpoint.last_error = ""
    checkpoint.metadata = {
        **(checkpoint.metadata or {}),
        "last_requested_mode": mode,
        "overlap_minutes": overlap_minutes,
        "limit": limit,
        "bootstrap_days": bootstrap_days,
    }
    checkpoint.save(
        update_fields=[
            "last_status",
            "last_run_started_at",
            "last_error",
            "metadata",
            "updated_at",
        ]
    )

    try:
        result = import_sqlserver_job_cards(
            schema=schema,
            view_name=view_name,
            limit=limit,
            days=(bootstrap_days if mode == "bootstrap_days" else None),
            since=since,
        )
        latest_cursor = result.get("latest_job_create_date")

        if isinstance(latest_cursor, str) and latest_cursor:
            parsed_latest_cursor = parse_checkpoint_cursor_value(latest_cursor)
        else:
            parsed_latest_cursor = None

        checkpoint.last_status = SyncCheckpoint.STATUS_SUCCESS
        checkpoint.last_run_finished_at = timezone.now()
        checkpoint.last_error = ""
        if parsed_latest_cursor and (
            parsed_checkpoint_cursor is None or parsed_latest_cursor > parsed_checkpoint_cursor
        ):
            checkpoint.cursor_value = format_checkpoint_cursor_value(parsed_latest_cursor)

        checkpoint.metadata = {
            **(checkpoint.metadata or {}),
            "last_completed_mode": mode,
            "last_result": {
                "total_rows": result["summary"].total_rows,
                "created": result["summary"].created_count,
                "updated": result["summary"].updated_count,
                "skipped": result["summary"].skipped_count,
                "errors": result["summary"].error_count,
                "latest_job_create_date": latest_cursor,
            },
        }
        checkpoint.save(
            update_fields=[
                "cursor_value",
                "last_status",
                "last_run_finished_at",
                "last_error",
                "metadata",
                "updated_at",
            ]
        )
    except Exception as exc:
        checkpoint.last_status = SyncCheckpoint.STATUS_FAILED
        checkpoint.last_run_finished_at = timezone.now()
        checkpoint.last_error = str(exc)
        checkpoint.save(
            update_fields=[
                "last_status",
                "last_run_finished_at",
                "last_error",
                "updated_at",
            ]
        )
        raise

    return {
        **result,
        "sync_mode": mode,
        "used_since": format_checkpoint_cursor_value(since),
        "checkpoint": {
            "key": checkpoint.key,
            "cursor_field": checkpoint.cursor_field,
            "cursor_value_before": checkpoint_cursor_before,
            "cursor_value_after": checkpoint.cursor_value,
            "last_status": checkpoint.last_status,
            "last_run_started_at": checkpoint.last_run_started_at.isoformat()
            if checkpoint.last_run_started_at
            else None,
            "last_run_finished_at": checkpoint.last_run_finished_at.isoformat()
            if checkpoint.last_run_finished_at
            else None,
            "overlap_minutes": overlap_minutes,
        },
    }
