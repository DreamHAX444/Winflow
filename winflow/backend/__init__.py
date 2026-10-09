"""WinFlow Automation Backend Subsystem.

Provides pluggable OS/automation backends (Windows native, Mock, etc.).
"""

import sys
import threading
from typing import Optional

from winflow.backend.base import (
    AutomationBackend,
    BaseClipboardBackend,
    BaseKeyboardBackend,
    BaseMouseBackend,
    BaseWindowBackend,
)

_GLOBAL_BACKEND: AutomationBackend | None = None
_BACKEND_LOCK = threading.Lock()


def get_backend() -> AutomationBackend:
    """Retrieve active automation backend, defaulting to Windows native on Windows or Mock."""
    global _GLOBAL_BACKEND
    with _BACKEND_LOCK:
        if _GLOBAL_BACKEND is None:
            if sys.platform == "win32":
                from winflow.backend.windows import create_windows_backend
                _GLOBAL_BACKEND = create_windows_backend()
            else:
                raise RuntimeError("WinFlow requires Windows OS.")
        return _GLOBAL_BACKEND


def set_backend(backend: AutomationBackend) -> None:
    """Explicitly set or override active automation backend (e.g., for testing)."""
    global _GLOBAL_BACKEND
    with _BACKEND_LOCK:
        _GLOBAL_BACKEND = backend


def reset_backend() -> None:
    """Reset global backend instance to default."""
    global _GLOBAL_BACKEND
    with _BACKEND_LOCK:
        _GLOBAL_BACKEND = None


__all__ = [
    "AutomationBackend",
    "BaseClipboardBackend",
    "BaseKeyboardBackend",
    "BaseMouseBackend",
    "BaseWindowBackend",
    "get_backend",
    "reset_backend",
    "set_backend",
]
