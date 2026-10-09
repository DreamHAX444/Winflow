"""Global emergency-stop hotkey support for Windows.

The parser is platform-independent so workflow files can be validated anywhere.
The listener imports pywin32 only when started, keeping non-Windows test runs
and configuration validation free of Win32 imports.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any

from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import ConfigurationError, UnsupportedEnvironmentError
from winflow.core.logger import get_logger


_MODIFIERS = {
    "ALT": 0x0001,
    "CTRL": 0x0002,
    "CONTROL": 0x0002,
    "SHIFT": 0x0004,
    "WIN": 0x0008,
    "WINDOWS": 0x0008,
}
_NAMED_KEYS = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "PRINTSCREEN": 0x2C,
}

# RegisterHotKey modifiers and message IDs.
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
_HOTKEY_ID = 0x5746


@dataclass(frozen=True)
class HotkeySpec:
    """Parsed RegisterHotKey modifier/key pair."""

    modifiers: int
    virtual_key: int
    canonical: str


def parse_emergency_hotkey(value: Any) -> HotkeySpec:
    """Parse a hotkey such as ``F8`` or ``CTRL+ALT+F8`` without Win32 imports."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Emergency-stop hotkey must be a non-empty string.")

    parts = [part.strip().upper() for part in value.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"Invalid emergency-stop hotkey {value!r}.")

    modifiers = 0
    main_keys: list[str] = []
    for part in parts:
        if part in _MODIFIERS:
            bit = _MODIFIERS[part]
            if modifiers & bit:
                raise ValueError(f"Duplicate modifier {part!r} in emergency-stop hotkey.")
            modifiers |= bit
        else:
            main_keys.append(part)

    if len(main_keys) != 1:
        raise ValueError("Emergency-stop hotkey must contain exactly one non-modifier key.")

    key = main_keys[0]
    if key in _NAMED_KEYS:
        virtual_key = _NAMED_KEYS[key]
    elif len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        virtual_key = ord(key)
    else:
        function_match = re.fullmatch(r"F(\d{1,2})", key)
        if not function_match or not 1 <= int(function_match.group(1)) <= 24:
            raise ValueError(
                f"Unsupported emergency-stop key {key!r}; use a letter, digit, F1-F24, "
                "or a supported named key."
            )
        virtual_key = 0x6F + int(function_match.group(1))

    canonical_modifiers = []
    for name, bit in (("CTRL", 0x0002), ("ALT", 0x0001), ("SHIFT", 0x0004), ("WIN", 0x0008)):
        if modifiers & bit:
            canonical_modifiers.append(name)
    canonical_modifiers.append(key)
    return HotkeySpec(modifiers, virtual_key, "+".join(canonical_modifiers))


class EmergencyStopHotkey:
    """Register a system-wide Windows hotkey that requests an emergency stop.

    ``win32api``/``win32gui`` can be injected in tests. The normal runtime uses
    pywin32 (already listed as a project dependency) and a dedicated message
    thread. The listener is explicitly unregistered by :meth:`stop`.
    """

    def __init__(
        self,
        hotkey: str,
        emergency_stop: EmergencyStop,
        logger: logging.Logger | None = None,
        *,
        win32api: Any | None = None,
        win32gui: Any | None = None,
    ) -> None:
        self.spec = parse_emergency_hotkey(hotkey)
        self.emergency_stop = emergency_stop
        self.logger = logger or get_logger()
        self._api = win32api
        self._gui = win32gui
        self._injected_api = win32api is not None and win32gui is not None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._startup_error: BaseException | None = None
        self._registered = False

    def start(self) -> None:
        """Start the message thread and fail explicitly if registration fails."""
        if self._thread is not None and self._thread.is_alive():
            return
        if sys.platform != "win32" and not self._injected_api:
            raise UnsupportedEnvironmentError(
                "The configured emergency-stop hotkey requires Windows and pywin32."
            )

        self._ready.clear()
        self._stop_event.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="WinFlowEmergencyStopHotkey",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            self.stop()
            raise ConfigurationError("Timed out while registering the emergency-stop hotkey.")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise ConfigurationError(
                f"Could not register emergency-stop hotkey '{self.spec.canonical}': {error}"
            ) from error

    def stop(self, timeout: float = 5.0) -> None:
        """Unregister the hotkey and join the listener thread."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        if self._thread_id is not None and self._api is not None:
            try:
                self._api.PostThreadMessage(self._thread_id, WM_QUIT, 0, 0)
            except Exception as exc:
                self.logger.debug("Could not post hotkey listener shutdown message: %s", exc)
        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                raise ConfigurationError("Emergency-stop hotkey listener did not shut down cleanly.")
        self._thread = None
        self._thread_id = None

    def _load_win32(self) -> None:
        if self._api is not None and self._gui is not None:
            return
        try:
            import win32api
            import win32gui
        except ImportError as exc:
            raise UnsupportedEnvironmentError(
                "Registering the emergency-stop hotkey requires pywin32."
            ) from exc
        self._api = win32api
        self._gui = win32gui

    def _message_loop(self) -> None:
        try:
            self._load_win32()
            self._thread_id = int(self._api.GetCurrentThreadId())
            # Ensure the thread message queue exists before it is registered or
            # another thread can post WM_QUIT to it.
            self._gui.PeekMessage(None, 0, 0, PM_NOREMOVE)
            self._api.RegisterHotKey(
                None,
                _HOTKEY_ID,
                self.spec.modifiers | MOD_NOREPEAT,
                self.spec.virtual_key,
            )
            self._registered = True
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            while not self._stop_event.is_set():
                message = self._gui.GetMessage(None, 0, 0)
                if message is None:
                    break
                message_id = message[1]
                if message_id == WM_QUIT:
                    break
                if message_id == WM_HOTKEY and int(message[2]) == _HOTKEY_ID:
                    self.emergency_stop.request_stop(
                        f"Emergency hotkey '{self.spec.canonical}' pressed"
                    )
                    continue
                self._gui.TranslateMessage(message)
                self._gui.DispatchMessage(message)
        except BaseException:
            self.logger.exception("Emergency-stop hotkey message loop failed.")
        finally:
            if self._registered:
                try:
                    self._api.UnregisterHotKey(None, _HOTKEY_ID)
                except Exception:
                    self.logger.exception("Failed to unregister the emergency-stop hotkey.")
                self._registered = False
            self._ready.set()


__all__ = ["EmergencyStopHotkey", "HotkeySpec", "parse_emergency_hotkey"]
