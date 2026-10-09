"""Keyboard automation actions for WinFlow.

Implements key, hotkey, and type.
"""

import re
from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.backend import AutomationBackend, get_backend
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


def _focus_target_if_requested(backend: AutomationBackend, opts: dict[str, Any]) -> None:
    if "target" not in opts or opts["target"] is None:
        return
    target = opts["target"]
    if not isinstance(target, dict) or not target:
        raise ActionExecutionError(
            "Keyboard target must contain a window title, application, PID, or HWND."
        )
    window = backend.window.find_window(target)
    if not window or not window.get("hwnd"):
        raise ActionExecutionError(
            "Keyboard target window could not be found confidently; no input was sent."
        )
    hwnd = window["hwnd"]
    if not backend.window.focus_window({"hwnd": hwnd}):
        raise ActionExecutionError(
            "Keyboard target window could not be focused; no input was sent."
        )
    active = backend.window.get_active_window()
    if active.get("hwnd") != hwnd:
        raise ActionExecutionError(
            "Keyboard target is not the foreground window; no input was sent."
        )


def _register_keyboard_cleanup(context: Any, keyboard: Any) -> None:
    register_cleanup = getattr(context, "register_cleanup", None)
    release_all = getattr(keyboard, "release_all", None)
    if callable(register_cleanup) and callable(release_all):
        register_cleanup(release_all)


class KeyAction(BaseAction):
    """Press and release a single key."""

    def __init__(
        self,
        name: str = "key",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        key = opts.get("key")

        if not key or not isinstance(key, str) or not key.strip():
            raise ActionExecutionError("KeyAction requires a non-empty string 'key'.")

        key_str = key.strip()
        _focus_target_if_requested(self.backend, opts)
        _register_keyboard_cleanup(context, self.backend.keyboard)
        key_state = str(opts.get("key_state", "press")).strip().lower()
        if key_state == "press":
            self.backend.keyboard.press_key(key_str)
        elif key_state == "down":
            key_down = getattr(self.backend.keyboard, "key_down", None)
            if not callable(key_down):
                raise ActionExecutionError("The active keyboard backend does not support Key Down.")
            key_down(key_str)
        elif key_state == "up":
            key_up = getattr(self.backend.keyboard, "key_up", None)
            if not callable(key_up):
                raise ActionExecutionError("The active keyboard backend does not support Key Up.")
            key_up(key_str)
        else:
            raise ActionExecutionError(
                f"Unsupported key_state '{key_state}'. Use 'press', 'down', or 'up'."
            )
        context.check_cancellation()
        result = {"action": "key", "key": key_str}
        if key_state != "press":
            result["key_state"] = key_state
        return result


class HotkeyAction(BaseAction):
    """Press a sequence or combination of keys simultaneously."""

    def __init__(
        self,
        name: str = "hotkey",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def _parse_keys(self, keys_input: list[str] | str) -> list[str]:
        if isinstance(keys_input, list):
            if any(not isinstance(key, str) for key in keys_input):
                raise ActionExecutionError("Hotkey list entries must all be strings.")
            parsed = [key.strip() for key in keys_input]
            if any(not key for key in parsed):
                raise ActionExecutionError("Hotkey keys cannot be empty.")
        elif isinstance(keys_input, str):
            # Support delimiters like 'CTRL,V' or 'CTRL+V'
            parsed = [k.strip() for k in re.split(r"[,+]", keys_input) if k.strip()]
        else:
            raise ActionExecutionError(
                f"Hotkey keys must be a list of strings or delimited string, got: {type(keys_input).__name__}"
            )
        if not parsed:
            raise ActionExecutionError("Hotkey requires at least one valid key.")
        return parsed

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        raw_keys = opts.get("keys")

        if raw_keys is None:
            raise ActionExecutionError("HotkeyAction requires 'keys' parameter.")

        keys = self._parse_keys(raw_keys)
        _focus_target_if_requested(self.backend, opts)
        _register_keyboard_cleanup(context, self.backend.keyboard)
        self.backend.keyboard.hotkey(keys)
        context.check_cancellation()
        return {"action": "hotkey", "keys": keys}


class TypeAction(BaseAction):
    """Type text string."""

    def __init__(
        self,
        name: str = "type",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        if "text" not in opts:
            raise ActionExecutionError("TypeAction requires 'text' parameter.")

        text = str(opts["text"])
        try:
            interval_ms = int(opts.get("interval_ms", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ActionExecutionError("Typing interval must be a non-negative integer.") from exc
        if isinstance(opts.get("interval_ms", 0), bool) or interval_ms < 0:
            raise ActionExecutionError("Typing interval must be a non-negative integer.")

        _focus_target_if_requested(self.backend, opts)
        _register_keyboard_cleanup(context, self.backend.keyboard)
        self.backend.keyboard.type_text(text, interval_ms=interval_ms)
        context.check_cancellation()
        # Avoid exposing raw text in logs/return if sensitive
        return {"action": "type", "length": len(text)}
