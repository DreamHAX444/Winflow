import ctypes
import unittest
from ctypes import wintypes

from winflow.backend import windows


class _FakeFunction:
    def __init__(self) -> None:
        self.argtypes = None
        self.restype = None


class _FakeLibrary:
    def __getattr__(self, name: str) -> _FakeFunction:
        function = _FakeFunction()
        setattr(self, name, function)
        return function


class Win32SignatureTests(unittest.TestCase):
    def test_pointer_and_handle_api_signatures_are_pointer_sized(self) -> None:
        user32 = _FakeLibrary()
        kernel32 = _FakeLibrary()
        windows._configure_win32_apis(user32, kernel32)

        self.assertIs(user32.GetForegroundWindow.restype, wintypes.HWND)
        self.assertIs(user32.GetClipboardData.restype, wintypes.HANDLE)
        self.assertIs(user32.SetClipboardData.restype, wintypes.HANDLE)
        self.assertIs(user32.SetClipboardData.argtypes[1], wintypes.HANDLE)
        self.assertIs(kernel32.GlobalAlloc.restype, wintypes.HGLOBAL)
        self.assertIs(kernel32.GlobalLock.restype, wintypes.LPVOID)
        self.assertIs(kernel32.GlobalLock.argtypes[0], wintypes.HGLOBAL)
        self.assertIs(kernel32.OpenProcess.restype, wintypes.HANDLE)
        self.assertIs(kernel32.QueryFullProcessImageNameW.argtypes[0], wintypes.HANDLE)
        self.assertIs(kernel32.CloseHandle.argtypes[0], wintypes.HANDLE)
        self.assertEqual(
            user32.mouse_event.argtypes,
            [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t],
        )

    def test_window_and_clipboard_calls_have_declared_arguments(self) -> None:
        user32 = _FakeLibrary()
        kernel32 = _FakeLibrary()
        windows._configure_win32_apis(user32, kernel32)

        self.assertEqual(user32.GetWindowTextLengthW.argtypes, [wintypes.HWND])
        self.assertEqual(user32.GetWindowThreadProcessId.argtypes[0], wintypes.HWND)
        self.assertEqual(user32.EnumWindows.argtypes[0], windows._WNDENUMPROC)
        self.assertEqual(kernel32.GlobalAlloc.argtypes[-1], ctypes.c_size_t)
        self.assertEqual(kernel32.GlobalUnlock.argtypes, [wintypes.HGLOBAL])

    def test_win32_backend_module_can_be_imported_for_non_windows_mocks(self) -> None:
        # Existing keyboard/window backend unit tests replace user32 after import.
        self.assertTrue(hasattr(windows, "WindowsWindowBackend"))
        self.assertTrue(hasattr(windows, "WindowsClipboardBackend"))


if __name__ == "__main__":
    unittest.main()
