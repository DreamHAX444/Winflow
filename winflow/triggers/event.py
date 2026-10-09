"""Notification event data model for WinFlow.

Defines a structured, strongly typed representation of incoming Windows notifications.
"""

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NotificationEvent:
    """Structured representation of a desktop notification event."""

    event_id: str
    application_id: str
    title: str = ""
    body: str = ""
    application_name: str = ""
    timestamp: float = field(default_factory=time.time)
    source: str = "windows_notification"
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Derive application_name from application_id if omitted."""
        if not self.application_name and self.application_id:
            # Fall back to base name or ID
            self.application_name = self.application_id.split(".")[-1]

    def to_dict(self) -> dict[str, Any]:
        """Convert the notification event to a dictionary."""
        return asdict(self)

    def safe_summary(self) -> dict[str, Any]:
        """Return a sanitized dictionary suitable for structured logging.

        Masks or truncates sensitive body text to protect user privacy.
        """
        body_length = len(self.body)
        body_snippet = self.body[:30] + "..." if body_length > 30 else self.body
        return {
            "event_id": self.event_id,
            "application_id": self.application_id,
            "application_name": self.application_name,
            "title": self.title,
            "body_snippet": body_snippet,
            "body_length": body_length,
            "timestamp": self.timestamp,
            "source": self.source,
        }

    def __repr__(self) -> str:
        """Safe string representation without exposing full body text."""
        return (
            f"NotificationEvent(id={self.event_id!r}, "
            f"app={self.application_id!r}, "
            f"title={self.title!r}, "
            f"body_length={len(self.body)})"
        )
