"""Reusable desktop window picker for workflow target properties."""

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from winflow.core.inspector import DesktopInspector
from winflow.ui.design_tokens import SPACING


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("pt", _Point),
        ("mouse_data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


class _KeyboardHookData(ctypes.Structure):
    _fields_ = [
        ("virtual_key", wintypes.DWORD),
        ("scan_code", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


class WindowPicker(QDialog):
    """Capture details for the top-level window under the user's click."""

    window_picked = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)

    _WH_KEYBOARD_LL = 13
    _WH_MOUSE_LL = 14
    _WM_LBUTTONDOWN = 0x0201
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _VK_ESCAPE = 0x1B
    _GA_ROOT = 2

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("windowPicker")
        self.setWindowTitle("Pick a window")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAccessibleName("Pick a window")
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

        title = QLabel("Pick a window")
        title.setObjectName("locationPickerTitle")
        description = QLabel("Click the window you want WinFlow to target.")
        description.setObjectName("helperText")
        self.status_label = QLabel("Press Escape to cancel.")
        self.status_label.setObjectName("locationPickerStatus")
        self.status_label.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.setAccessibleName("Cancel window selection")
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)
        self.setFixedWidth(340)

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        if sys.platform != "win32":
            message = "Window picking requires Windows."
            self.status_label.setText(message)
            self._position_prompt()
            self.show()
            self.raise_()
            self.failed.emit(message)
            return

        self.status_label.setText("Click a window to select it. Press Escape to cancel.")
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
            self.status_label.setText(f"Unable to start window picking: {exc}")
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
        self._user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._user32.GetAncestor.restype = ctypes.c_void_p
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
                mouse_data = ctypes.cast(
                    ctypes.c_void_p(data),
                    ctypes.POINTER(_MouseHookData),
                ).contents
                point = _Point(mouse_data.pt.x, mouse_data.pt.y)
                window = self._user32.WindowFromPoint(point)
                if self._is_prompt_window(window):
                    return self._call_next(self._mouse_hook, code, message, data)
                root_window = self._user32.GetAncestor(window, self._GA_ROOT) if window else 0
                if not root_window:
                    QTimer.singleShot(0, lambda: self._fail("No window was found at that location."))
                else:
                    result = DesktopInspector.inspect_window(int(root_window))
                    QTimer.singleShot(0, lambda details=result: self._capture(details))
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

    def _is_prompt_window(self, window: Any) -> bool:
        if not window:
            return False
        prompt = ctypes.c_void_p(int(self.winId()))
        root_window = self._user32.GetAncestor(window, self._GA_ROOT)
        return bool(
            root_window == prompt.value
            or self._user32.IsChild(prompt, window)
        )

    def _capture(self, details: dict[str, Any]) -> None:
        if not self._active:
            return
        hwnd = details.get("hwnd")
        if not isinstance(hwnd, int) or isinstance(hwnd, bool) or not hwnd:
            self._fail(str(details.get("error", "Unable to inspect the selected window.")))
            return
        self._active = False
        self._stop_hooks()
        self.hide()
        self.window_picked.emit(details)

    def _fail(self, message: str) -> None:
        if not self._active:
            return
        self._active = False
        self._stop_hooks()
        self.status_label.setText(f"Unable to capture window: {message}")
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
