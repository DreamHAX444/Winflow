"""Abstract interfaces for WinFlow desktop automation backends.

Decouples higher-level actions from platform-specific APIs and third-party libraries.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class BaseMouseBackend(ABC):
    """Abstract interface for mouse automation operations."""

    @abstractmethod
    def move(self, x: int, y: int, duration_ms: int = 0) -> None:
        """Move cursor to absolute screen coordinates (x, y)."""

    @abstractmethod
    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
        interval_ms: int = 50,
    ) -> None:
        """Perform mouse clicks at coordinates (or current position if omitted)."""

    @abstractmethod
    def double_click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
    ) -> None:
        """Perform a double click."""

    @abstractmethod
    def right_click(
        self,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Perform a right click."""

    @abstractmethod
    def scroll(
        self,
        amount: int,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Scroll vertical wheel. Positive amount scrolls UP, negative scrolls DOWN."""

    @abstractmethod
    def get_position(self) -> tuple[int, int]:
        """Retrieve current cursor coordinates (x, y)."""


class BaseKeyboardBackend(ABC):
    """Abstract interface for keyboard automation operations."""

    @abstractmethod
    def press_key(self, key: str) -> None:
        """Press and release a single key (e.g., 'ENTER', 'ESC', 'TAB')."""

    @abstractmethod
    def hotkey(self, keys: list[str]) -> None:
        """Press a sequence of keys simultaneously (e.g., ['CTRL', 'V'])."""

    @abstractmethod
    def type_text(self, text: str, interval_ms: int = 0) -> None:
        """Type arbitrary text string with optional interval delay between characters."""


class BaseClipboardBackend(ABC):
    """Abstract interface for clipboard operations."""

    @abstractmethod
    def read_text(self) -> str:
        """Read and return plain text from system clipboard."""

    @abstractmethod
    def write_text(self, text: str) -> None:
        """Write plain text to system clipboard."""

    @abstractmethod
    def clear(self) -> None:
        """Clear system clipboard content."""


class BaseWindowBackend(ABC):
    """Abstract interface for window and application control."""

    @abstractmethod
    def get_active_window(self) -> dict[str, Any]:
        """Retrieve information on current foreground window."""

    @abstractmethod
    def find_window(self, target: dict[str, Any]) -> dict[str, Any] | None:
        """Find a window matching criteria (title_contains, process_name, etc.)."""

    @abstractmethod
    def focus_window(self, target: dict[str, Any], state: str = "restore") -> bool:
        """Bring a window matching criteria to the foreground."""

    @abstractmethod
    def launch_application(
        self,
        path: str,
        arguments: list[str] | None = None,
        wait_for_start: bool = False,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Launch an executable application directly."""


@dataclass
class AutomationBackend:
    """Unified container for all desktop automation backend capabilities."""
    mouse: BaseMouseBackend
    keyboard: BaseKeyboardBackend
    clipboard: BaseClipboardBackend
    window: BaseWindowBackend
