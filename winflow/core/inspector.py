from typing import Any


class DesktopInspector:
    """Safe, read-only diagnostic API for the current desktop."""

    @staticmethod
    def inspect_cursor() -> dict[str, Any]:
        """Expose current mouse position."""
        try:
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return {"x": int(pt.x), "y": int(pt.y)}
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def inspect_window(hwnd: int | None = None) -> dict[str, Any]:
        """Inspect the foreground window or a specified native window handle."""
        try:
            import psutil
            import win32gui  # type: ignore
            import win32process  # type: ignore
            
            hwnd = hwnd if hwnd is not None else win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindow(hwnd):
                return {"error": "The selected window is no longer available."}
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            proc = psutil.Process(pid)
            return {
                "hwnd": hwnd,
                "title": title,
                "process_id": pid,
                "process_name": proc.name(),
                "executable": proc.exe(),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def inspect_monitor() -> dict[str, Any]:
        """Provide read-only information about the monitor(s)."""
        try:
            import ctypes

            from screeninfo import get_monitors  # type: ignore
            
            monitors = get_monitors()
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            
            return {
                "monitor_count": len(monitors),
                "screen_width": sw,
                "screen_height": sh,
                "monitors": [
                    {
                        "name": m.name,
                        "x": m.x,
                        "y": m.y,
                        "width": m.width,
                        "height": m.height,
                        "is_primary": m.is_primary
                    } for m in monitors
                ]
            }
        except Exception as e:
            return {"error": str(e)}
