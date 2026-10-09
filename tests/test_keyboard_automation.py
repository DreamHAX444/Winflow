import unittest
from typing import Any
from unittest.mock import patch

from winflow.actions.keyboard import HotkeyAction, KeyAction, TypeAction
from winflow.backend import reset_backend, set_backend
from winflow.backend import windows
from winflow.backend.base import AutomationBackend
from winflow.core.errors import ActionExecutionError, ActionNotFoundError
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.execution_context import ExecutionContext
from winflow.engine.workflow_runner import WorkflowRunner


class _KeyEventRecorder:
    def __init__(self, fail_on: tuple[int, int] | None = None) -> None:
        self.events: list[tuple[int, int, int]] = []
        self.fail_on = fail_on
        self.fail_events: set[tuple[int, int]] = set()
        self.failed = False

    def keybd_event(self, vk: int, scan_code: int, flags: int, _extra: int) -> None:
        self.events.append((vk, scan_code, flags))
        if (vk, flags) in self.fail_events:
            self.fail_events.remove((vk, flags))
            raise OSError("simulated input failure")
        if not self.failed and self.fail_on == (vk, flags):
            self.failed = True
            raise OSError("simulated input failure")


class _FakeWindowBackend:
    def __init__(self, found: dict[str, Any] | None, active: int | None = None) -> None:
        self.found = found
        self.active = active
        self.find_calls: list[dict[str, Any]] = []
        self.focus_calls: list[dict[str, Any]] = []

    def find_window(self, target: dict[str, Any]) -> dict[str, Any] | None:
        self.find_calls.append(target)
        return self.found

    def focus_window(self, target: dict[str, Any], state: str = "restore") -> bool:
        self.focus_calls.append(target)
        if self.found and target.get("hwnd") == self.found.get("hwnd"):
            self.active = target["hwnd"]
            return True
        return False

    def get_active_window(self) -> dict[str, Any]:
        return {"hwnd": self.active}


class _FakeKeyboardBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.releases = 0
        self.fail_cleanup = False

    def press_key(self, key: str) -> None:
        self.calls.append(("press", key))

    def hotkey(self, keys: list[str]) -> None:
        self.calls.append(("hotkey", keys))

    def type_text(self, text: str, interval_ms: int = 0) -> None:
        self.calls.append(("type", (text, interval_ms)))

    def key_down(self, key: str) -> None:
        self.calls.append(("down", key))

    def key_up(self, key: str) -> None:
        self.calls.append(("up", key))

    def release_all(self) -> None:
        self.releases += 1
        if self.fail_cleanup:
            raise ActionExecutionError("simulated cleanup failure")


class _Context:
    def __init__(self) -> None:
        self.callbacks: list[Any] = []

    def check_cancellation(self) -> None:
        pass

    def update_window(self, _title: str) -> None:
        pass

    def register_cleanup(self, callback: Any) -> None:
        if callback not in self.callbacks:
            self.callbacks.append(callback)


class KeyboardBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_user32 = windows.user32
        self.recorder = _KeyEventRecorder()
        windows.user32 = self.recorder
        self.keyboard = windows.WindowsKeyboardBackend()

    def tearDown(self) -> None:
        windows.user32 = self.original_user32

    def test_single_key_and_modifier_hotkey_release_in_reverse_order(self) -> None:
        self.keyboard.press_key("A")
        self.keyboard.hotkey(["CTRL", "C"])
        self.assertEqual(
            self.recorder.events,
            [
                (ord("A"), 0, 0),
                (ord("A"), 0, windows.KEYEVENTF_KEYUP),
                (0x11, 0, 0),
                (ord("C"), 0, 0),
                (ord("C"), 0, windows.KEYEVENTF_KEYUP),
                (0x11, 0, windows.KEYEVENTF_KEYUP),
            ],
        )

    def test_key_down_and_up_track_held_keys(self) -> None:
        self.keyboard.key_down("SHIFT")
        self.assertEqual(self.keyboard._held_keys, [0x10])
        self.keyboard.key_up("SHIFT")
        self.assertEqual(self.keyboard._held_keys, [])
        self.assertEqual(
            self.recorder.events,
            [(0x10, 0, 0), (0x10, 0, windows.KEYEVENTF_KEYUP)],
        )

    def test_failed_key_down_remains_tracked_when_cleanup_fails(self) -> None:
        self.recorder.fail_events = {(0x10, 0), (0x10, windows.KEYEVENTF_KEYUP)}
        with self.assertRaisesRegex(ActionExecutionError, "and release it"):
            self.keyboard.key_down("SHIFT")
        self.assertEqual(self.keyboard._held_keys, [0x10])
        self.keyboard.release_all()
        self.assertEqual(self.keyboard._held_keys, [])

    def test_hotkey_failure_releases_already_pressed_modifiers(self) -> None:
        self.recorder.fail_on = (ord("C"), 0)
        with self.assertRaisesRegex(ActionExecutionError, "simulated input failure"):
            self.keyboard.hotkey(["CTRL", "C"])
        self.assertEqual(self.keyboard._held_keys, [])
        self.assertEqual(
            self.recorder.events[-2:],
            [
                (ord("C"), 0, windows.KEYEVENTF_KEYUP),
                (0x11, 0, windows.KEYEVENTF_KEYUP),
            ],
        )

    def test_typed_unicode_uses_utf16_and_respects_delay(self) -> None:
        with patch.object(windows.time, "sleep") as sleep:
            self.keyboard.type_text("A😀", interval_ms=12)
        units = [ord("A"), 0xD83D, 0xDE00]
        self.assertEqual(
            self.recorder.events,
            [
                event
                for unit in units
                for event in (
                    (0, unit, windows.KEYEVENTF_UNICODE),
                    (0, unit, windows.KEYEVENTF_UNICODE | windows.KEYEVENTF_KEYUP),
                )
            ],
        )
        self.assertEqual(sleep.call_count, len(units))

    def test_failed_typed_character_is_released(self) -> None:
        self.recorder.fail_on = (0, windows.KEYEVENTF_UNICODE)
        with self.assertRaisesRegex(ActionExecutionError, "simulated input failure"):
            self.keyboard.type_text("x")
        self.assertEqual(
            self.recorder.events[-1],
            (0, ord("x"), windows.KEYEVENTF_UNICODE | windows.KEYEVENTF_KEYUP),
        )

    def test_invalid_key_and_delay_are_controlled_errors(self) -> None:
        with self.assertRaisesRegex(ActionExecutionError, "Unsupported key"):
            self.keyboard.press_key("NOT_A_KEY")
        with self.assertRaisesRegex(ActionExecutionError, "non-negative integer"):
            self.keyboard.type_text("x", interval_ms=-1)
        with self.assertRaisesRegex(ActionExecutionError, "Unsupported key"):
            self.keyboard.hotkey(["CTRL", "not_a_key"])
        with self.assertRaisesRegex(ActionExecutionError, "invalid Unicode"):
            self.keyboard.type_text("\ud800")
        self.assertEqual(self.recorder.events, [])

    def test_cleanup_releases_keys_left_held(self) -> None:
        self.keyboard.key_down("ALT")
        self.keyboard.key_down("TAB")
        self.keyboard.release_all()
        self.assertEqual(self.keyboard._held_keys, [])
        self.assertEqual(
            self.recorder.events[-2:],
            [
                (0x09, 0, windows.KEYEVENTF_KEYUP),
                (0x12, 0, windows.KEYEVENTF_KEYUP),
            ],
        )


class KeyboardActionTests(unittest.TestCase):
    def _backend(
        self,
        found: dict[str, Any] | None = None,
        active: int | None = None,
    ) -> AutomationBackend:
        return AutomationBackend(
            mouse=None,
            keyboard=_FakeKeyboardBackend(),
            clipboard=None,
            window=_FakeWindowBackend(found, active),
        )

    def test_keyboard_actions_focus_and_verify_the_specific_target(self) -> None:
        backend = self._backend({"hwnd": 515, "title": "Target"}, active=22)
        context = _Context()
        KeyAction(
            params={"key": "ENTER", "target": {"hwnd": 515}}, backend=backend
        ).execute(context)
        self.assertEqual(backend.window.focus_calls, [{"hwnd": 515}])
        self.assertEqual(backend.window.active, 515)
        self.assertEqual(backend.keyboard.calls, [("press", "ENTER")])

    def test_hotkey_and_type_actions_use_the_requested_target(self) -> None:
        backend = self._backend({"hwnd": 515, "title": "Target"})
        context = _Context()
        HotkeyAction(
            params={"keys": ["CTRL", "C"], "target": {"hwnd": 515}},
            backend=backend,
        ).execute(context)
        TypeAction(
            params={"text": "hello", "target": {"hwnd": 515}},
            backend=backend,
        ).execute(context)
        self.assertEqual(backend.window.focus_calls, [{"hwnd": 515}, {"hwnd": 515}])
        self.assertEqual(
            backend.keyboard.calls,
            [("hotkey", ["CTRL", "C"]), ("type", ("hello", 0))],
        )

    def test_missing_or_wrong_target_prevents_keyboard_input(self) -> None:
        for found, focus_result, active in (
            (None, True, None),
            ({"hwnd": 515}, False, 22),
        ):
            backend = self._backend(found, active)
            if found is not None:
                backend.window.focus_window = lambda _target, state="restore": focus_result
            with self.assertRaisesRegex(ActionExecutionError, "no input was sent"):
                TypeAction(params={"text": "x", "target": {"title_exact": "Target"}}, backend=backend).execute(_Context())
            self.assertEqual(backend.keyboard.calls, [])

    def test_no_target_preserves_existing_keyboard_calls(self) -> None:
        backend = self._backend()
        context = _Context()
        KeyAction(params={"key": "A"}, backend=backend).execute(context)
        HotkeyAction(params={"keys": ["CTRL", "C"]}, backend=backend).execute(context)
        TypeAction(params={"text": "hi", "interval_ms": 7}, backend=backend).execute(context)
        self.assertEqual(
            backend.keyboard.calls,
            [
                ("press", "A"),
                ("hotkey", ["CTRL", "C"]),
                ("type", ("hi", 7)),
            ],
        )
        self.assertEqual(backend.window.find_calls, [])

    def test_key_action_supports_down_and_up_without_new_action_names(self) -> None:
        backend = self._backend()
        context = _Context()
        KeyAction(params={"key": "CTRL", "key_state": "down"}, backend=backend).execute(context)
        KeyAction(params={"key": "CTRL", "key_state": "up"}, backend=backend).execute(context)
        self.assertEqual(
            backend.keyboard.calls,
            [("down", "CTRL"), ("up", "CTRL")],
        )
        self.assertEqual(len(context.callbacks), 1)

    def test_non_string_hotkey_entries_are_rejected(self) -> None:
        action = HotkeyAction(params={"keys": ["CTRL", 7]}, backend=self._backend())
        with self.assertRaisesRegex(ActionExecutionError, "entries must all be strings"):
            action.execute(_Context())

    def test_runner_releases_held_keys_when_a_later_step_fails(self) -> None:
        keyboard = _FakeKeyboardBackend()
        set_backend(
            AutomationBackend(mouse=None, keyboard=keyboard, clipboard=None, window=None)
        )
        try:
            registry = ActionRegistry()
            registry.register("key", KeyAction)
            runner = WorkflowRunner(
                {
                    "workflow": {
                        "id": "keyboard-cleanup",
                        "steps": [
                            {"action": "key", "params": {"key": "CTRL", "key_state": "down"}},
                            {"action": "missing_action"},
                        ],
                    },
                    "settings": {"use_desktop_lock": False},
                },
                action_registry=registry,
            )
            with self.assertRaises(ActionNotFoundError):
                runner.run(ExecutionContext(workflow_id="keyboard-cleanup"))
            self.assertEqual(keyboard.releases, 1)
        finally:
            reset_backend()

    def test_runner_does_not_report_success_when_cleanup_fails(self) -> None:
        keyboard = _FakeKeyboardBackend()
        keyboard.fail_cleanup = True
        set_backend(
            AutomationBackend(mouse=None, keyboard=keyboard, clipboard=None, window=None)
        )
        try:
            registry = ActionRegistry()
            registry.register("key", KeyAction)
            runner = WorkflowRunner(
                {
                    "workflow": {
                        "id": "keyboard-cleanup-failure",
                        "steps": [
                            {"action": "key", "params": {"key": "CTRL", "key_state": "down"}}
                        ],
                    },
                    "settings": {"use_desktop_lock": False},
                },
                action_registry=registry,
            )
            with self.assertRaisesRegex(ActionExecutionError, "Keyboard cleanup failed"):
                runner.run(ExecutionContext(workflow_id="keyboard-cleanup-failure"))
            self.assertEqual(keyboard.releases, 1)
        finally:
            reset_backend()


if __name__ == "__main__":
    unittest.main()
