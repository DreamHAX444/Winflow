"""WinFlow Desktop Actions Module.

Exports all Phase 2 desktop automation action classes and auto-registration functions.
"""

from typing import TYPE_CHECKING, Dict, Optional, Type

from winflow.actions.application import LaunchApplicationAction
from winflow.actions.base import BaseAction
from winflow.actions.clipboard import (
    ClipboardClearAction,
    ClipboardReadAction,
    ClipboardWriteAction,
)
from winflow.actions.keyboard import HotkeyAction, KeyAction, TypeAction
from winflow.actions.mouse import (
    ClickAction,
    DoubleClickAction,
    MoveAction,
    RightClickAction,
    ScrollAction,
)
from winflow.actions.variables import SetVariableAction
from winflow.actions.wait import WaitAction
from winflow.actions.window import FocusWindowAction, GetActiveWindowAction

if TYPE_CHECKING:
    from winflow.engine.action_registry import ActionRegistry

# Mapping of standard action names to concrete action classes
PHASE2_ACTIONS: dict[str, type[BaseAction]] = {
    "move": MoveAction,
    "click": ClickAction,
    "double_click": DoubleClickAction,
    "right_click": RightClickAction,
    "scroll": ScrollAction,
    "key": KeyAction,
    "hotkey": HotkeyAction,
    "type": TypeAction,
    "clipboard_read": ClipboardReadAction,
    "clipboard_write": ClipboardWriteAction,
    "clipboard_clear": ClipboardClearAction,
    "wait": WaitAction,
    "focus_window": FocusWindowAction,
    "launch_application": LaunchApplicationAction,
    "get_active_window": GetActiveWindowAction,
    "set_variable": SetVariableAction,
}


def register_desktop_actions(registry: Optional["ActionRegistry"] = None) -> None:
    """Register all Phase 2 desktop automation actions into target ActionRegistry."""
    from winflow.engine.action_registry import get_action_registry

    target_registry = registry if registry is not None else get_action_registry()
    for name, action_cls in PHASE2_ACTIONS.items():
        target_registry.register(name, action_cls, allow_override=True)


__all__ = [
    "PHASE2_ACTIONS",
    "BaseAction",
    "ClickAction",
    "ClipboardClearAction",
    "ClipboardReadAction",
    "ClipboardWriteAction",
    "DoubleClickAction",
    "FocusWindowAction",
    "GetActiveWindowAction",
    "HotkeyAction",
    "KeyAction",
    "LaunchApplicationAction",
    "MoveAction",
    "RightClickAction",
    "ScrollAction",
    "SetVariableAction",
    "TypeAction",
    "WaitAction",
    "register_desktop_actions",
]
