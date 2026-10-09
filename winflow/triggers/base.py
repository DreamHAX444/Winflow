"""Base trigger interface for WinFlow.

All trigger implementations (notification listener, hotkey, schedule, etc.)
must inherit from BaseTrigger.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class BaseTrigger(ABC):
    """Abstract base class for event triggers."""

    def __init__(self, name: str, config: dict[str, Any] | None = None) -> None:
        """Initialize base trigger.

        Args:
            name: Identifier for the trigger instance.
            config: Trigger configuration dictionary.
        """
        self.name: str = name
        self.config: dict[str, Any] = config or {}
        self._callback: Callable[[dict[str, Any]], None] | None = None
        self._is_running: bool = False

    @abstractmethod
    def start(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Start listening for events and invoke callback when an event triggers.

        Args:
            callback: Function invoked with event payload dictionary when triggered.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop listening for events and clean up any resources."""

    @property
    def is_running(self) -> bool:
        """Return True if trigger listener is currently active."""
        return self._is_running

    def emit_event(self, event_data: dict[str, Any]) -> None:
        """Emit an event payload to the registered callback.

        Args:
            event_data: Event payload data.
        """
        if self._callback is not None:
            self._callback(event_data)
