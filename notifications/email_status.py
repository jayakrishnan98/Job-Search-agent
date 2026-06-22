"""Tracks the last email delivery attempt for API/UI diagnostics."""

_last_status: dict = {
    "ok": None,
    "transport": None,
    "error": None,
    "checked_at": None,
}


def record_success(transport: str) -> None:
    from datetime import datetime, timezone

    _last_status.update(
        {
            "ok": True,
            "transport": transport,
            "error": None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def record_failure(transport: str, error: str) -> None:
    from datetime import datetime, timezone

    _last_status.update(
        {
            "ok": False,
            "transport": transport,
            "error": error,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def get_status() -> dict:
    return dict(_last_status)
