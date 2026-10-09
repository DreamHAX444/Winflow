"""Reusable desktop location picker for mouse-action properties."""

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from winflow.core.inspector import DesktopInspector
from winflow.ui.design_tokens import SPACING


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


_ULONG_PTR = ctypes.c_size_t


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("pt", _Point),
        ("mouse_data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", _ULONG_PTR),
    ]


class _KeyboardHookData(ctypes.Structure):
    _fields_ = [
        ("virtual_key", wintypes.DWORD),
        ("scan_code", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", _ULONG_PTR),
    ]


class LocationPicker(QDialog):
    location_picked = Signal(int, int)
    cancelled = Signal()
    failed = Signal(str)

    _WH_KEYBOARD_LL = 13
    _WH_MOUSE_LL = 14
    _WM_LBUTTONDOWN = 0x0201
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _VK_ESCAPE = 0x1B

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("locationPicker")
        self.setWindowTitle("Pick a location")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAccessibleName("Pick a location")
        self._active = False
        self._user32: Any = None
        self._mouse_hook: Any = None
        self._keyboard_hook: Any = None
        self._mouse_callback: Any = None
        self._keyboard_callback: Any = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"]
        )
        layout.setSpacing(SPACING["sm"])

        title = QLabel("Pick a location")
        title.setObjectName("locationPickerTitle")
        description = QLabel("Click anywhere on the screen to select it.")
        description.setObjectName("helperText")
        self.status_label = QLabel("")
        self.status_label.setObjectName("locationPickerStatus")
        self.status_label.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.setAccessibleName("Cancel location selection")
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)
        self.setFixedWidth(320)

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        if sys.platform != "win32":
            message = "Location picking requires Windows."
            self.status_label.setText(message)
            self._position_prompt()
            self.show()
            self.raise_()
            self.failed.emit(message)
            return

        self.status_label.setText("Waiting for a location. Press Escape to cancel.")
        self._active = True
        self._position_prompt()
        self.show()
        self.raise_()
        self.activateWindow()
        try:
            self._install_hooks()
        except OSError as exc:
            self._stop_hooks()
            self._active = False
            self.status_label.setText(f"Unable to start location picking: {exc}")
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        if not self._active:
            self.hide()
            return
        self._active = False
        self._stop_hooks()
        self.hide()
        self.cancelled.emit()

    def _position_prompt(self) -> None:
        screen = self.screen()
        if screen is None:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.adjustSize()
        self.move(available.left() + 20, available.top() + 20)

    def _install_hooks(self) -> None:
        handle_type = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(
            handle_type, ctypes.c_int, ctypes.c_size_t, handle_type
        )
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p

        self._user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            callback_type,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self._user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
            handle_type,
        ]
        self._user32.CallNextHookEx.restype = handle_type
        self._user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._user32.WindowFromPoint.argtypes = [_Point]
        self._user32.WindowFromPoint.restype = ctypes.c_void_p
        self._user32.IsChild.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._user32.IsChild.restype = wintypes.BOOL

        module = kernel32.GetModuleHandleW(None)
        self._mouse_callback = callback_type(self._on_mouse_event)
        self._keyboard_callback = callback_type(self._on_keyboard_event)
        self._mouse_hook = self._user32.SetWindowsHookExW(
            self._WH_MOUSE_LL, self._mouse_callback, module, 0
        )
        if not self._mouse_hook:
            raise ctypes.WinError(ctypes.get_last_error())

        self._keyboard_hook = self._user32.SetWindowsHookExW(
            self._WH_KEYBOARD_LL, self._keyboard_callback, module, 0
        )
        if not self._keyboard_hook:
            raise ctypes.WinError(ctypes.get_last_error())

    def _on_mouse_event(self, code: int, message: int, data: int) -> int:
        if code >= 0 and message == self._WM_LBUTTONDOWN:
            try:
                if self._is_prompt_click(data):
                    return self._call_next(self._mouse_hook, code, message, data)
                position = DesktopInspector.inspect_cursor()
                QTimer.singleShot(0, lambda result=position: self._capture(result))
            except Exception as exc:
                QTimer.singleShot(0, lambda error=str(exc): self._fail(error))
            return 1
        return self._call_next(self._mouse_hook, code, message, data)

    def _on_keyboard_event(self, code: int, message: int, data: int) -> int:
        if code >= 0 and message in (self._WM_KEYDOWN, self._WM_SYSKEYDOWN):
            try:
                key_data = ctypes.cast(
                    ctypes.c_void_p(data),
                    ctypes.POINTER(_KeyboardHookData),
                ).contents
                if key_data.virtual_key == self._VK_ESCAPE:
                    QTimer.singleShot(0, self.cancel)
                    return 1
            except Exception as exc:
                QTimer.singleShot(0, lambda error=str(exc): self._fail(error))
        return self._call_next(self._keyboard_hook, code, message, data)

    def _is_prompt_click(self, data: int) -> bool:
        try:
            mouse_data = ctypes.cast(
                ctypes.c_void_p(data),
                ctypes.POINTER(_MouseHookData),
            ).contents
            point = _Point(mouse_data.pt.x, mouse_data.pt.y)
            target = self._user32.WindowFromPoint(point)
            prompt = ctypes.c_void_p(int(self.winId()))
            return bool(
                target
                and (
                    target == prompt.value
                    or self._user32.IsChild(prompt, target)
                )
            )
        except (OSError, ValueError, TypeError):
            return False

    def _capture(self, position: dict[str, Any]) -> None:
        if not self._active:
            return
        x = position.get("x")
        y = position.get("y")
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            self._fail(str(position.get("error", "Unable to read cursor position.")))
            return
        self._active = False
        self._stop_hooks()
        self.hide()
        self.location_picked.emit(x, y)

    def _fail(self, message: str) -> None:
        if not self._active:
            return
        self._active = False
        self._stop_hooks()
        self.status_label.setText(f"Unable to capture location: {message}")
        self.failed.emit(message)

    def _call_next(self, hook: Any, code: int, message: int, data: int) -> int:
        if self._user32 is None:
            return 0
        return int(self._user32.CallNextHookEx(hook, code, message, data))

    def _stop_hooks(self) -> None:
        if self._user32 is not None:
            for hook in (self._mouse_hook, self._keyboard_hook):
                if hook:
                    self._user32.UnhookWindowsHookEx(hook)
        self._mouse_hook = None
        self._keyboard_hook = None
        self._mouse_callback = None
        self._keyboard_callback = None

    def closeEvent(self, event: Any) -> None:
        was_active = self._active
        self._active = False
        self._stop_hooks()
        if was_active:
            self.cancelled.emit()
        super().closeEvent(event)
