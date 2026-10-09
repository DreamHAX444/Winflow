"""Notification sources package for WinFlow.

Provides abstract and concrete notification listening sources.
"""

import os
from typing import Optional

from winflow.triggers.sources.base import BaseNotificationSource
from winflow.triggers.sources.mock import MockNotificationSource
from winflow.triggers.sources.windows_db import WindowsDatabaseNotificationSource


def create_notification_source(
    source_type: str = "auto",
    db_path: str | None = None,
    poll_interval: float = 0.25,
    start_from_current: bool = True,
) -> BaseNotificationSource:
    """Factory creating an appropriate notification source.

    Args:
        source_type: "auto", "windows_db", or "mock".
        db_path: Custom database path for windows_db.
        poll_interval: Poll frequency for windows_db.
        start_from_current: Whether to ignore prior notifications on start.

    Returns:
        Instance of BaseNotificationSource.
    """
    mode = source_type.lower().strip()
    if mode == "mock":
        return MockNotificationSource()

    if mode == "windows_db":
        return WindowsDatabaseNotificationSource(
            db_path=db_path,
            poll_interval=poll_interval,
            start_from_current=start_from_current,
        )

    # "auto" detection
    default_db = WindowsDatabaseNotificationSource.DEFAULT_DB_PATH
    if os.name == "nt" and os.path.exists(db_path or default_db):
        return WindowsDatabaseNotificationSource(
            db_path=db_path,
            poll_interval=poll_interval,
            start_from_current=start_from_current,
        )

    # Fallback to Mock in environments without wpndatabase
    return MockNotificationSource()


__all__ = [
    "BaseNotificationSource",
    "MockNotificationSource",
    "WindowsDatabaseNotificationSource",
    "create_notification_source",
]
