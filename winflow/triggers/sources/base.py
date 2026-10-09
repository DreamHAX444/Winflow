"""Base notification source interface for WinFlow.

Abstracts the low-level mechanism used to observe desktop notifications.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable

from winflow.triggers.event import NotificationEvent


class BaseNotificationSource(ABC):
    """Abstract interface for desktop notification listeners and event producers."""

    def __init__(self) -> None:
        """Initialize the base notification source."""
        self._is_running: bool = False
        self._callback: Callable[[NotificationEvent], None] | None = None

    @abstractmethod
    def start(self, on_event: Callable[[NotificationEvent], None]) -> None:
        """Start listening for notifications and dispatch to on_event callback.

        Args:
            on_event: Function called with each incoming NotificationEvent.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop listening for notifications and release system resources."""

    @property
    def is_running(self) -> bool:
        """Return True if the listener source is currently active."""
        return self._is_running
