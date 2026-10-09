"""Native Windows desktop automation backend using Win32 APIs via ctypes.

Provides direct OS-level control without requiring heavy external binary packages.
"""

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from winflow.backend.base import (
    AutomationBackend,
    BaseClipboardBackend,
    BaseKeyboardBackend,
    BaseMouseBackend,
    BaseWindowBackend,
)
from winflow.core.errors import ActionExecutionError

# Win32 Constants
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Mouse flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

# Keyboard flags
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# Clipboard flags
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# Window flags
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Virtual Key Mapping
VK_MAP: dict[str, int] = {
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "TAB": 0x09,
    "SPACE": 0x20,
    "BACKSPACE": 0x08,
    "BS": 0x08,
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "ALT": 0x12,
    "SHIFT": 0x10,
    "WIN": 0x5B,
    "WINDOWS": 0x5B,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "HOME": 0x24,
    "END": 0x23,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "CAPSLOCK": 0x14,
    "NUMLOCK": 0x90,
    "PRINTSCREEN": 0x2C,
}

# Add standard letters and numbers to VK_MAP
for _char_code in range(ord("A"), ord("Z") + 1):
    VK_MAP[chr(_char_code)] = _char_code
for _num_code in range(ord("0"), ord("9") + 1):
    VK_MAP[chr(_num_code)] = _num_code
for _fn in range(1, 13):
    VK_MAP[f"F{_fn}"] = 0x6F + _fn


class WindowsMouseBackend(BaseMouseBackend):
    """Windows native mouse automation implementation."""

    def move(self, x: int, y: int, duration_ms: int = 0) -> None:
        if duration_ms <= 0:
            user32.SetCursorPos(int(x), int(y))
            return

        # Smooth interpolation if duration requested
        start_x, start_y = self.get_position()
        steps = max(1, duration_ms // 10)
        delay = (duration_ms / 1000.0) / steps
        for step in range(1, steps + 1):
            curr_x = int(start_x + (x - start_x) * (step / steps))
            curr_y = int(start_y + (y - start_y) * (step / steps))
            user32.SetCursorPos(curr_x, curr_y)
            time.sleep(delay)
        user32.SetCursorPos(int(x), int(y))

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
        interval_ms: int = 50,
    ) -> None:
        if x is not None and y is not None:
            self.move(x, y)

        btn_lower = button.lower()
        if btn_lower == "left":
            down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        elif btn_lower == "right":
            down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        elif btn_lower == "middle":
            down_flag, up_flag = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
        else:
            raise ActionExecutionError(f"Unsupported mouse button: '{button}'")

        for i in range(clicks):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
            if i < clicks - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000.0)

    def double_click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
    ) -> None:
        self.click(x=x, y=y, button=button, clicks=2, interval_ms=50)

    def right_click(
        self,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        self.click(x=x, y=y, button="right", clicks=1)

    def scroll(
        self,
        amount: int,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        # Positive scrolls UP (away from user), negative scrolls DOWN
        wheel_delta = int(amount * WHEEL_DELTA)
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, wheel_delta, 0)

    def get_position(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)


class WindowsKeyboardBackend(BaseKeyboardBackend):
    """Windows native keyboard automation implementation."""

    def __init__(self) -> None:
        import threading

        self._held_keys: list[int] = []
        self._held_unicode: list[int] = []
        self._key_lock = threading.RLock()

    def _resolve_vk(self, key_name: str) -> int:
        if not isinstance(key_name, str) or not key_name.strip():
            raise ActionExecutionError("Key identifier must be a non-empty string.")
        norm = key_name.strip().upper()
        if norm in VK_MAP:
            return VK_MAP[norm]
        if len(norm) == 1 and ord(norm) <= 0x7F:
            return ord(norm)
        raise ActionExecutionError(f"Unsupported key identifier: '{key_name}'")

    def press_key(self, key: str) -> None:
        vk = self._resolve_vk(key)
        with self._key_lock:
            already_held = vk in self._held_keys
            if not already_held:
                self._held_keys.append(vk)
            try:
                user32.keybd_event(vk, 0, 0, 0)
                time.sleep(0.01)
            except Exception as exc:
                if already_held:
                    raise ActionExecutionError(
                        f"Failed to press key '{key}': {exc}"
                    ) from exc
                cleanup_error = self._release_keys([vk])
                if cleanup_error:
                    raise ActionExecutionError(
                        f"Failed to press key '{key}'; key cleanup failed: {cleanup_error}"
                    ) from exc
                raise ActionExecutionError(f"Failed to press key '{key}': {exc}") from exc
            if already_held:
                return
            cleanup_error = self._release_keys([vk])
            if cleanup_error:
                raise ActionExecutionError(
                    f"Failed to release key '{key}' after pressing it: {cleanup_error}"
                )

    def hotkey(self, keys: list[str]) -> None:
        if not isinstance(keys, list) or not keys:
            raise ActionExecutionError("Hotkey requires at least one key.")
        vks = [self._resolve_vk(k) for k in keys]
        if len(set(vks)) != len(vks):
            raise ActionExecutionError("Hotkey cannot contain the same key more than once.")
        with self._key_lock:
            pressed: list[int] = []
            try:
                for vk in vks:
                    if vk in self._held_keys:
                        continue
                    pressed.append(vk)
                    self._held_keys.append(vk)
                    user32.keybd_event(vk, 0, 0, 0)
                time.sleep(0.02)
            except Exception as exc:
                cleanup_error = self._release_keys(pressed)
                message = f"Failed to send hotkey: {exc}"
                if cleanup_error:
                    message += f"; key cleanup failed: {cleanup_error}"
                raise ActionExecutionError(message) from exc
            cleanup_error = self._release_keys(pressed)
            if cleanup_error:
                raise ActionExecutionError(
                    f"Failed to release hotkey keys: {cleanup_error}"
                )

    def key_down(self, key: str) -> None:
        vk = self._resolve_vk(key)
        with self._key_lock:
            if vk in self._held_keys:
                return
            self._held_keys.append(vk)
            try:
                user32.keybd_event(vk, 0, 0, 0)
            except Exception as exc:
                try:
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                except Exception as cleanup_exc:
                    raise ActionExecutionError(
                        f"Failed to press key '{key}' and release it: {cleanup_exc}"
                    ) from exc
                self._discard_held_key(vk)
                raise ActionExecutionError(f"Failed to press key '{key}': {exc}") from exc

    def key_up(self, key: str) -> None:
        vk = self._resolve_vk(key)
        with self._key_lock:
            try:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception as exc:
                raise ActionExecutionError(f"Failed to release key '{key}': {exc}") from exc
            self._discard_held_key(vk)

    def release_all(self) -> None:
        """Release keys intentionally held by key_down, reporting cleanup failures."""
        with self._key_lock:
            cleanup_error = self._release_keys(list(self._held_keys))
            unicode_errors = self._release_unicode(list(self._held_unicode))
            cleanup_error = "; ".join(
                error for error in (cleanup_error, unicode_errors) if error
            ) or None
            if cleanup_error:
                raise ActionExecutionError(
                    f"Failed to release held keyboard keys: {cleanup_error}"
                )

    def _release_keys(self, keys: list[int]) -> str | None:
        errors: list[str] = []
        for vk in reversed(keys):
            try:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception as exc:
                errors.append(f"0x{vk:02X}: {exc}")
            else:
                self._discard_held_key(vk)
        return "; ".join(errors) if errors else None

    def _discard_held_key(self, vk: int) -> None:
        try:
            self._held_keys.remove(vk)
        except ValueError:
            pass

    def _release_unicode(self, code_units: list[int]) -> str | None:
        errors: list[str] = []
        for code_unit in reversed(code_units):
            try:
                user32.keybd_event(
                    0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0
                )
            except Exception as exc:
                errors.append(f"Unicode 0x{code_unit:04X}: {exc}")
            else:
                try:
                    self._held_unicode.remove(code_unit)
                except ValueError:
                    pass
        return "; ".join(errors) if errors else None

    def type_text(self, text: str, interval_ms: int = 0) -> None:
        if not isinstance(text, str):
            raise ActionExecutionError("Text to type must be a string.")
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms < 0:
            raise ActionExecutionError("Typing interval must be a non-negative integer.")

        try:
            # Unicode keyboard events use UTF-16 code units, including surrogate pairs.
            encoded = text.encode("utf-16-le", errors="strict")
        except UnicodeEncodeError as exc:
            raise ActionExecutionError("Text contains invalid Unicode characters.") from exc

        with self._key_lock:
            for index in range(0, len(encoded), 2):
                code_unit = encoded[index] | (encoded[index + 1] << 8)
                self._held_unicode.append(code_unit)
                try:
                    user32.keybd_event(0, code_unit, KEYEVENTF_UNICODE, 0)
                except Exception as exc:
                    cleanup_error = self._release_unicode([code_unit])
                    if cleanup_error:
                        raise ActionExecutionError(
                            f"Failed to type text; character cleanup failed: {cleanup_error}"
                        ) from exc
                    raise ActionExecutionError(f"Failed to type text: {exc}") from exc
                cleanup_error = self._release_unicode([code_unit])
                if cleanup_error:
                    raise ActionExecutionError(
                        f"Failed to release a typed character: {cleanup_error}"
                    )
                if interval_ms > 0:
                    time.sleep(interval_ms / 1000.0)


class WindowsClipboardBackend(BaseClipboardBackend):
    """Windows native clipboard operations using user32/kernel32."""

    def read_text(self) -> str:
        for _ in range(5):
            if user32.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            raise ActionExecutionError("Failed to open Windows clipboard for reading.")

        try:
            h_data = user32.GetClipboardData(CF_UNICODETEXT)
            if not h_data:
                return ""
            p_data = kernel32.GlobalLock(h_data)
            if not p_data:
                return ""
            try:
                return ctypes.wstring_at(p_data)
            finally:
                kernel32.GlobalUnlock(h_data)
        finally:
            user32.CloseClipboard()

    def write_text(self, text: str) -> None:
        buf = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h_mem:
            raise ActionExecutionError("Failed to allocate global memory for clipboard.")

        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            kernel32.GlobalFree(h_mem)
            raise ActionExecutionError("Failed to lock global memory for clipboard.")

        ctypes.memmove(p_mem, buf, size)
        kernel32.GlobalUnlock(h_mem)

        for _ in range(5):
            if user32.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            kernel32.GlobalFree(h_mem)
            raise ActionExecutionError("Failed to open Windows clipboard for writing.")

        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                kernel32.GlobalFree(h_mem)
                raise ActionExecutionError("Failed to set Windows clipboard data.")
        finally:
            user32.CloseClipboard()

    def clear(self) -> None:
        for _ in range(5):
            if user32.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            raise ActionExecutionError("Failed to open Windows clipboard for clearing.")
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()


class WindowsWindowBackend(BaseWindowBackend):
    """Windows native window and process manager implementation."""

    def _get_process_name_for_pid(self, pid: int) -> str:
        if pid <= 0:
            return ""
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_proc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
        except Exception:
            pass
        finally:
            kernel32.CloseHandle(h_proc)
        return ""

    def _get_window_info(self, hwnd: int) -> dict[str, Any]:
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = self._get_process_name_for_pid(pid.value)

        return {
            "hwnd": hwnd,
            "title": title,
            "pid": pid.value,
            "process_name": process_name,
        }

    def get_active_window(self) -> dict[str, Any]:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"hwnd": 0, "title": "", "pid": 0, "process_name": ""}
        return self._get_window_info(hwnd)

    def find_window(self, target: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(target, dict):
            return None

        normalized_target = dict(target)
        hwnd_value = normalized_target.get("hwnd")
        if "hwnd" in normalized_target and hwnd_value is not None:
            try:
                normalized_target["hwnd"] = (
                    int(hwnd_value, 16)
                    if isinstance(hwnd_value, str) and hwnd_value.lower().startswith("0x")
                    else int(hwnd_value)
                )
            except (TypeError, ValueError, OverflowError):
                return None
            if normalized_target["hwnd"] <= 0:
                return None

        for key in ("pid", "process_id"):
            if key in normalized_target and normalized_target[key] is not None:
                try:
                    normalized_target[key] = int(normalized_target[key])
                except (TypeError, ValueError, OverflowError):
                    return None
                if normalized_target[key] <= 0:
                    return None

        string_criteria = (
            "title_contains",
            "title_exact",
            "process_name",
            "executable_name",
        )
        for key in string_criteria:
            value = normalized_target.get(key)
            if value is not None and not isinstance(value, str):
                return None
            if isinstance(value, str):
                normalized_target[key] = value.strip()

        if not any(
            normalized_target.get(key)
            for key in ("hwnd", "pid", "process_id", *string_criteria)
        ):
            return None

        windows: list[dict[str, Any]] = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def enum_windows_callback(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            info = self._get_window_info(int(hwnd))
            windows.append(info)
            return True

        cb = WNDENUMPROC(enum_windows_callback)
        user32.EnumWindows(cb, 0)
        hwnd = normalized_target.get("hwnd")
        hwnd_window = next(
            (window for window in windows if window.get("hwnd") == hwnd),
            None,
        ) if hwnd else None
        criteria = dict(normalized_target)
        if hwnd and hwnd_window is None:
            criteria.pop("hwnd")
            # A stale handle is recoverable only when stored window-specific
            # title information can identify the original window.
            if not (criteria.get("title_exact") or criteria.get("title_contains")):
                return None
            for key in ("pid", "process_id"):
                expected_pid = criteria.get(key)
                if expected_pid and not any(
                    window.get("pid") == expected_pid for window in windows
                ):
                    criteria.pop(key)

        def matches(window: dict[str, Any]) -> bool:
            title = str(window.get("title") or "")
            process_name = str(window.get("process_name") or "")
            expected_exact = criteria.get("title_exact")
            if expected_exact and title != expected_exact:
                return False
            expected_contains = criteria.get("title_contains")
            if expected_contains and expected_contains.casefold() not in title.casefold():
                return False
            for key in ("process_name", "executable_name"):
                expected = criteria.get(key)
                if expected and expected.casefold() != process_name.casefold():
                    return False
            for key in ("pid", "process_id"):
                expected_pid = criteria.get(key)
                if expected_pid and window.get("pid") != expected_pid:
                    return False
            expected_hwnd = criteria.get("hwnd")
            if expected_hwnd and window.get("hwnd") != expected_hwnd:
                return False
            return True

        # A live HWND is the strongest identifier, but still validate every
        # additional stored criterion rather than ignoring contradictions.
        if hwnd_window is not None:
            return hwnd_window if matches(hwnd_window) else None

        matching_windows = [window for window in windows if matches(window)]
        if not matching_windows:
            return None

        is_specific = any(
            criteria.get(key)
            for key in ("title_exact", "title_contains", "hwnd")
        )
        if is_specific:
            return matching_windows[0] if len(matching_windows) == 1 else None

        # Application-wide targets may match multiple windows. Prefer the
        # foreground window if it belongs to that application; otherwise use
        # the first visible match in the native enumeration order.
        foreground = user32.GetForegroundWindow()
        return next(
            (window for window in matching_windows if window.get("hwnd") == foreground),
            matching_windows[0],
        )

    def focus_window(self, target: dict[str, Any], state: str = "restore") -> bool:
        win = self.find_window(target)
        if not win:
            return False
        hwnd = win["hwnd"]
        if not user32.IsWindow(hwnd):
            return False
        
        sw_flag = SW_RESTORE
        if state == "maximize":
            sw_flag = SW_MAXIMIZE
        elif state == "minimize":
            sw_flag = SW_MINIMIZE
            
        user32.ShowWindow(hwnd, sw_flag)
        if state != "minimize":
            user32.SetForegroundWindow(hwnd)
            return user32.GetForegroundWindow() == hwnd
        return True

    def launch_application(
        self,
        path: str,
        arguments: list[str] | None = None,
        wait_for_start: bool = False,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        target_path = Path(path).resolve()
        cmd = [str(target_path)] + (arguments or [])

        try:
            proc = subprocess.Popen(cmd)
        except Exception as exc:
            raise ActionExecutionError(
                f"Failed to launch application '{path}': {exc}"
            ) from exc

        app_info = {
            "pid": proc.pid,
            "path": str(target_path),
            "arguments": arguments or [],
            "status": "RUNNING",
        }

        if wait_for_start:
            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                # Check if process is still running or finished immediately
                poll = proc.poll()
                if poll is not None and poll != 0:
                    raise ActionExecutionError(
                        f"Application '{path}' exited prematurely with code {poll}."
                    )
                # Check if window appeared
                win = self.find_window({"process_name": target_path.name})
                if win:
                    app_info["hwnd"] = win["hwnd"]
                    app_info["title"] = win["title"]
                    break
                time.sleep(0.1)

        return app_info


def create_windows_backend() -> AutomationBackend:
    """Factory helper to construct native Windows AutomationBackend."""
    return AutomationBackend(
        mouse=WindowsMouseBackend(),
        keyboard=WindowsKeyboardBackend(),
        clipboard=WindowsClipboardBackend(),
        window=WindowsWindowBackend(),
    )
