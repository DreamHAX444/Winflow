import unittest
from typing import Any

from winflow.actions.mouse import ClickAction
from winflow.actions.window import FocusWindowAction
from winflow.backend import windows
from winflow.backend.base import AutomationBackend
from winflow.core.errors import ActionExecutionError


class _FakeUser32:
    def __init__(self, foreground: int = 0) -> None:
        self.foreground = foreground
        self.visible: dict[int, bool] = {}
        self.valid: dict[int, bool] = {}
        self.focused: list[tuple[int, int]] = []

    def EnumWindows(self, callback: Any, _lparam: int) -> None:
        for hwnd in self.visible:
            if not callback(hwnd, 0):
                break

    def IsWindowVisible(self, hwnd: int) -> bool:
        return self.visible.get(hwnd, False)

    def IsWindow(self, hwnd: int) -> bool:
        return self.valid.get(hwnd, False)

    def GetForegroundWindow(self) -> int:
        return self.foreground

    def ShowWindow(self, hwnd: int, state: int) -> None:
        self.focused.append((hwnd, state))

    def SetForegroundWindow(self, hwnd: int) -> bool:
        self.foreground = hwnd
        return True


class _FakeWindowBackend:
    def __init__(
        self,
        found: dict[str, Any] | None = None,
        focus_result: bool = True,
    ) -> None:
        self.found = found
        self.focus_result = focus_result
        self.find_calls: list[dict[str, Any]] = []
        self.focus_calls: list[tuple[dict[str, Any], str]] = []

    def find_window(self, target: dict[str, Any]) -> dict[str, Any] | None:
        self.find_calls.append(target)
        return self.found

    def focus_window(self, target: dict[str, Any], state: str = "restore") -> bool:
        self.focus_calls.append((target, state))
        return self.focus_result

    def get_active_window(self) -> dict[str, Any]:
        return self.found or {}


class _FakeMouseBackend:
    def __init__(self) -> None:
        self.click_calls: list[dict[str, Any]] = []

    def click(self, **kwargs: Any) -> None:
        self.click_calls.append(kwargs)


class _Context:
    execution_id = "test"

    def check_cancellation(self) -> None:
        pass

    def update_window(self, _title: str) -> None:
        pass

    def set_variable(self, _name: str, _value: Any) -> None:
        pass


class _WindowBackendFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.original_user32 = windows.user32
        self.user32 = _FakeUser32(foreground=2)
        windows.user32 = self.user32
        self.backend = windows.WindowsWindowBackend()
        self.window_info = {
            1: {"hwnd": 1, "title": "Mail", "pid": 10, "process_name": "chrome.exe"},
            2: {"hwnd": 2, "title": "YouTube", "pid": 10, "process_name": "chrome.exe"},
            3: {"hwnd": 3, "title": "Editor", "pid": 20, "process_name": "code.exe"},
        }
        self.user32.visible = {1: True, 2: True, 3: True}
        self.user32.valid = {1: True, 2: True, 3: True}
        self.backend._get_window_info = lambda hwnd: dict(self.window_info[hwnd])

    def tearDown(self) -> None:
        windows.user32 = self.original_user32


class WindowTargetMatchingTests(_WindowBackendFixture):
    def test_specific_window_uses_hwnd_and_rejects_conflicting_criteria(self) -> None:
        match = self.backend.find_window(
            {"hwnd": 2, "title_exact": "YouTube", "process_name": "chrome.exe"}
        )
        self.assertEqual(match["hwnd"], 2)
        self.assertIsNone(
            self.backend.find_window({"hwnd": 2, "title_exact": "Mail"})
        )

    def test_ambiguous_specific_title_is_not_guessed(self) -> None:
        self.window_info[4] = dict(self.window_info[2], hwnd=4)
        self.user32.visible[4] = True
        self.user32.valid[4] = True
        self.assertIsNone(
            self.backend.find_window(
                {"title_exact": "YouTube", "process_name": "chrome.exe"}
            )
        )

    def test_stale_hwnd_re_resolves_only_with_unique_window_criteria(self) -> None:
        self.user32.valid[999] = False
        match = self.backend.find_window(
            {
                "hwnd": 999,
                "title_exact": "YouTube",
                "process_name": "chrome.exe",
                "process_id": 9999,
            }
        )
        self.assertEqual(match["hwnd"], 2)
        self.assertIsNone(self.backend.find_window({"hwnd": 999, "process_name": "chrome.exe"}))

    def test_application_target_prefers_matching_foreground_window(self) -> None:
        match = self.backend.find_window({"process_name": "chrome.exe"})
        self.assertEqual(match["hwnd"], 2)
        self.user32.foreground = 3
        match = self.backend.find_window({"executable_name": "chrome.exe"})
        self.assertEqual(match["hwnd"], 1)

    def test_missing_invalid_and_unsupported_targets_do_not_match(self) -> None:
        self.assertIsNone(self.backend.find_window({}))
        self.assertIsNone(self.backend.find_window({"custom": "value"}))
        self.assertIsNone(self.backend.find_window({"hwnd": 0, "process_name": "chrome.exe"}))
        self.assertIsNone(self.backend.find_window({"hwnd": "not-a-handle"}))

    def test_focus_verifies_the_requested_window_reached_foreground(self) -> None:
        self.assertTrue(self.backend.focus_window({"hwnd": 1}))
        self.assertEqual(self.user32.focused[-1][0], 1)
        self.user32.SetForegroundWindow = lambda _hwnd: False
        self.user32.GetForegroundWindow = lambda: 2
        self.assertFalse(self.backend.focus_window({"hwnd": 1}))


class WindowTargetActionTests(unittest.TestCase):
    def test_focus_action_focuses_the_resolved_handle(self) -> None:
        window_backend = _FakeWindowBackend(
            {"hwnd": 824, "title": "Target", "pid": 3, "process_name": "app.exe"}
        )
        backend = AutomationBackend(
            mouse=None, keyboard=None, clipboard=None, window=window_backend
        )
        action = FocusWindowAction(params={"target": {"title_exact": "Target"}}, backend=backend)
        result = action.execute(_Context())
        self.assertEqual(window_backend.focus_calls, [({"hwnd": 824}, "restore")])
        self.assertEqual(result["hwnd"], 824)

    def test_focus_action_reports_missing_and_unfocusable_targets(self) -> None:
        for found, focus_result, message in (
            (None, True, "could not be found confidently"),
            ({"hwnd": 824, "title": "Target"}, False, "could not be brought"),
        ):
            backend = AutomationBackend(
                mouse=None,
                keyboard=None,
                clipboard=None,
                window=_FakeWindowBackend(found, focus_result),
            )
            action = FocusWindowAction(
                params={"target": {"title_exact": "Target"}}, backend=backend
            )
            with self.assertRaisesRegex(ActionExecutionError, message):
                action.execute(_Context())

    def test_missing_target_is_a_controlled_error(self) -> None:
        backend = AutomationBackend(
            mouse=None,
            keyboard=None,
            clipboard=None,
            window=_FakeWindowBackend(),
        )
        with self.assertRaisesRegex(ActionExecutionError, "requires target criteria"):
            FocusWindowAction(backend=backend).execute(_Context())

    def test_actions_without_target_keep_existing_mouse_behavior(self) -> None:
        mouse = _FakeMouseBackend()
        backend = AutomationBackend(
            mouse=mouse, keyboard=None, clipboard=None, window=None
        )
        ClickAction(params={"x": 12, "y": 34}, backend=backend).execute(_Context())
        self.assertEqual(
            mouse.click_calls,
            [{"x": 12, "y": 34, "button": "left", "clicks": 1, "interval_ms": 50}],
        )


if __name__ == "__main__":
    unittest.main()
