import queue
import threading
import time
import unittest
from typing import Any

from winflow.actions.base import BaseAction
from winflow.core.emergency_hotkey import (
    EmergencyStopHotkey,
    WM_HOTKEY,
    parse_emergency_hotkey,
)
from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import ConfigurationError, EmergencyStopTriggered
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.trigger_registry import TriggerRegistry
from winflow.engine.workflow_engine import WorkflowEngine
from winflow.storage.execution_history import SQLiteExecutionHistory
from winflow.triggers.base import BaseTrigger
from winflow.triggers.event import NotificationEvent
from winflow.triggers.sources.mock import MockNotificationSource
from winflow.triggers.windows_notification import WindowsNotificationTrigger


class _FakeTrigger(BaseTrigger):
    def start(self, callback: Any) -> None:
        self._callback = callback
        self._is_running = True

    def stop(self) -> None:
        self._is_running = False

    def fire(self, event_id: str) -> None:
        self.emit_event({"event_id": event_id})


class _PolicyAction(BaseAction):
    handler = staticmethod(lambda _context: None)

    def execute(self, context: Any, **kwargs: Any) -> None:
        type(self).handler(context)


class _ShutdownAction(BaseAction):
    entered = threading.Event()
    cleaned = threading.Event()

    def execute(self, context: Any, **kwargs: Any) -> None:
        context.register_cleanup(type(self).cleaned.set)
        type(self).entered.set()
        while True:
            context.check_cancellation()
            time.sleep(0.002)


class _FakeWin32Gui:
    def __init__(self) -> None:
        self.messages: queue.Queue[tuple[Any, ...]] = queue.Queue()

    def PeekMessage(self, *_args: Any) -> None:
        return None

    def GetMessage(self, *_args: Any) -> tuple[Any, ...]:
        return self.messages.get(timeout=3.0)

    def TranslateMessage(self, _message: tuple[Any, ...]) -> None:
        return None

    def DispatchMessage(self, _message: tuple[Any, ...]) -> None:
        return None


class _FakeWin32Api:
    def __init__(self, gui: _FakeWin32Gui) -> None:
        self.gui = gui
        self.registered: list[tuple[Any, ...]] = []
        self.unregistered: list[tuple[Any, ...]] = []

    def GetCurrentThreadId(self) -> int:
        return threading.get_ident()

    def RegisterHotKey(self, *args: Any) -> None:
        self.registered.append(args)

    def UnregisterHotKey(self, *args: Any) -> None:
        self.unregistered.append(args)

    def PostThreadMessage(self, _thread_id: int, message: int, wparam: int, lparam: int) -> None:
        self.gui.messages.put((None, message, wparam, lparam, 0, (0, 0)))


class EmergencyHotkeyTests(unittest.TestCase):
    def test_hotkey_parser_accepts_modifiers_and_rejects_ambiguous_keys(self) -> None:
        spec = parse_emergency_hotkey("ctrl+alt+F8")
        self.assertEqual(spec.canonical, "CTRL+ALT+F8")
        self.assertEqual(spec.virtual_key, 0x77)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            parse_emergency_hotkey("CTRL+ALT")
        with self.assertRaisesRegex(ValueError, "Duplicate modifier"):
            parse_emergency_hotkey("CTRL+CONTROL+F8")

    def test_listener_registers_dispatches_and_unregisters_hotkey(self) -> None:
        gui = _FakeWin32Gui()
        api = _FakeWin32Api(gui)
        stop = EmergencyStop()
        listener = EmergencyStopHotkey(
            "CTRL+F8",
            stop,
            win32api=api,
            win32gui=gui,
        )
        listener.start()
        gui.messages.put((None, WM_HOTKEY, 0x5746, 0, 0, (0, 0)))

        deadline = time.monotonic() + 1.0
        while not stop.is_triggered() and time.monotonic() < deadline:
            time.sleep(0.005)
        listener.stop()

        self.assertTrue(stop.is_triggered())
        self.assertEqual(stop.get_reason(), "Emergency hotkey 'CTRL+F8' pressed")
        self.assertEqual(api.registered[0][1:], (0x5746, 0x4002, 0x77))
        self.assertEqual(api.unregistered, [(None, 0x5746)])


class WorkflowConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        _PolicyAction.handler = staticmethod(lambda _context: None)
        self.emergency_stop = EmergencyStop()
        self.history = SQLiteExecutionHistory(":memory:")
        self.actions = ActionRegistry()
        self.actions.register("policy", _PolicyAction)
        self.triggers = TriggerRegistry()
        self.triggers.register("fake", _FakeTrigger)
        self.engine = WorkflowEngine(
            action_registry=self.actions,
            trigger_registry=self.triggers,
            emergency_stop=self.emergency_stop,
            history_storage=self.history,
        )
        self.config: dict[str, Any] = {
            "schema_version": "1.0",
            "workflow": {
                "id": "policy-workflow",
                "steps": [{"action": "policy"}],
            },
            "settings": {"use_desktop_lock": False},
            "trigger": {"type": "fake", "while_running": "ignore"},
        }
        self.trigger = self.engine.start_workflow_listener(self.config)
        assert isinstance(self.trigger, _FakeTrigger)
        self.trigger_name = self.trigger.name

    def tearDown(self) -> None:
        self.engine.shutdown()
        self.history.close()

    def _wait_idle(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with self.engine._worker_condition:
                state = self.engine._trigger_executions.get(self.trigger_name)
                if state is None or not state.running:
                    return
            time.sleep(0.005)
        self.fail("trigger worker did not become idle")

    def test_ignore_reserves_before_worker_start_and_drops_rapid_events(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        seen: list[str] = []

        def action(context: Any) -> None:
            seen.append(context.trigger_event["event_id"])
            entered.set()
            release.wait(timeout=1.0)

        _PolicyAction.handler = staticmethod(action)
        self.trigger.fire("first")
        # This event can arrive before the worker gets CPU time; the engine's
        # reservation must already be visible and reject it under ignore.
        self.trigger.fire("second")
        self.assertTrue(entered.wait(timeout=1.0))
        release.set()
        self._wait_idle()
        self.assertEqual(seen, ["first"])

    def test_queue_runs_events_once_in_order(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        seen: list[str] = []

        def action(context: Any) -> None:
            seen.append(context.trigger_event["event_id"])
            if context.trigger_event["event_id"] == "first":
                entered.set()
                release.wait(timeout=1.0)

        _PolicyAction.handler = staticmethod(action)
        self.config["trigger"]["while_running"] = "queue"
        self.engine.shutdown()
        self.history.close()
        self.emergency_stop.reset()
        self.history = SQLiteExecutionHistory(":memory:")
        self.engine = WorkflowEngine(
            action_registry=self.actions,
            trigger_registry=self.triggers,
            emergency_stop=self.emergency_stop,
            history_storage=self.history,
        )
        self.trigger = self.engine.start_workflow_listener(self.config)
        assert isinstance(self.trigger, _FakeTrigger)
        self.trigger_name = self.trigger.name

        self.trigger.fire("first")
        self.assertTrue(entered.wait(timeout=1.0))
        self.trigger.fire("second")
        self.trigger.fire("third")
        release.set()
        self._wait_idle()
        self.assertEqual(seen, ["first", "second", "third"])

    def test_restart_cancels_safely_then_runs_latest_event(self) -> None:
        entered = threading.Event()
        seen: list[str] = []

        def action(context: Any) -> None:
            event_id = context.trigger_event["event_id"]
            seen.append(event_id)
            if event_id == "first":
                entered.set()
                while True:
                    context.check_cancellation()
                    time.sleep(0.002)

        _PolicyAction.handler = staticmethod(action)
        self.config["trigger"]["while_running"] = "restart"
        self.engine.shutdown()
        self.history.close()
        self.emergency_stop.reset()
        self.history = SQLiteExecutionHistory(":memory:")
        self.engine = WorkflowEngine(
            action_registry=self.actions,
            trigger_registry=self.triggers,
            emergency_stop=self.emergency_stop,
            history_storage=self.history,
        )
        self.trigger = self.engine.start_workflow_listener(self.config)
        assert isinstance(self.trigger, _FakeTrigger)
        self.trigger_name = self.trigger.name

        self.trigger.fire("first")
        self.assertTrue(entered.wait(timeout=1.0))
        self.trigger.fire("replacement")
        self._wait_idle()
        self.assertEqual(seen, ["first", "replacement"])

    def test_engine_honors_both_enabled_flags(self) -> None:
        self.engine.stop_workflow_listener(self.trigger_name)
        self.config["settings"]["enabled"] = False
        self.assertIsNone(self.engine.start_workflow_listener(self.config))
        with self.assertRaisesRegex(ConfigurationError, "disabled"):
            self.engine.run_workflow(self.config)


class WorkflowShutdownTests(unittest.TestCase):
    def test_shutdown_cancels_and_waits_for_manual_workflow(self) -> None:
        _ShutdownAction.entered = threading.Event()
        _ShutdownAction.cleaned = threading.Event()
        actions = ActionRegistry()
        actions.register("shutdown_action", _ShutdownAction)
        stop = EmergencyStop()
        history = SQLiteExecutionHistory(":memory:")
        engine = WorkflowEngine(
            action_registry=actions,
            emergency_stop=stop,
            history_storage=history,
        )
        config = {
            "schema_version": "1.0",
            "workflow": {
                "id": "manual-shutdown-workflow",
                "steps": [{"action": "shutdown_action"}],
            },
            "settings": {"use_desktop_lock": False},
        }
        outcomes: list[BaseException] = []

        def run_workflow() -> None:
            try:
                engine.run_workflow(config)
            except BaseException as exc:
                outcomes.append(exc)

        worker = threading.Thread(target=run_workflow, name="manual-workflow-test")
        worker.start()
        self.assertTrue(_ShutdownAction.entered.wait(timeout=1.0))

        engine.shutdown()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(stop.is_triggered())
        self.assertTrue(_ShutdownAction.cleaned.is_set())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], EmergencyStopTriggered)
        self.assertFalse(engine._active_contexts)
        history.close()

    def test_shutdown_cancels_workers_runs_cleanup_and_stops_hotkey(self) -> None:
        _ShutdownAction.entered = threading.Event()
        _ShutdownAction.cleaned = threading.Event()
        actions = ActionRegistry()
        actions.register("shutdown_action", _ShutdownAction)
        triggers = TriggerRegistry()
        triggers.register("fake", _FakeTrigger)
        stop = EmergencyStop()
        history = SQLiteExecutionHistory(":memory:")
        hotkeys: list[Any] = []

        class _FakeHotkeyListener:
            def __init__(self, hotkey: str, emergency_stop: EmergencyStop, logger: Any) -> None:
                self.hotkey = hotkey
                self.emergency_stop = emergency_stop
                self.started = False
                self.stopped = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        def factory(hotkey: str, emergency_stop: EmergencyStop, logger: Any) -> _FakeHotkeyListener:
            listener = _FakeHotkeyListener(hotkey, emergency_stop, logger)
            hotkeys.append(listener)
            return listener

        engine = WorkflowEngine(
            action_registry=actions,
            trigger_registry=triggers,
            emergency_stop=stop,
            history_storage=history,
            emergency_hotkey_factory=factory,
        )
        config = {
            "schema_version": "1.0",
            "workflow": {
                "id": "shutdown-workflow",
                "steps": [{"action": "shutdown_action"}],
            },
            "settings": {
                "use_desktop_lock": False,
                "emergency_stop_hotkey": "F8",
            },
            "trigger": {"type": "fake"},
        }
        trigger = engine.start_workflow_listener(config)
        assert isinstance(trigger, _FakeTrigger)
        trigger.fire("shutdown-event")
        self.assertTrue(_ShutdownAction.entered.wait(timeout=1.0))

        engine.shutdown()

        self.assertTrue(stop.is_triggered())
        self.assertTrue(_ShutdownAction.cleaned.is_set())
        self.assertEqual(len(hotkeys), 1)
        self.assertTrue(hotkeys[0].started)
        self.assertTrue(hotkeys[0].stopped)
        self.assertFalse(engine._active_workers)
        self.assertFalse(engine._active_contexts)
        self.assertFalse(trigger.is_running)
        history.close()


class WindowsNotificationConcurrencyTests(unittest.TestCase):
    def test_notification_trigger_defers_admission_to_engine(self) -> None:
        source = MockNotificationSource()
        trigger = WindowsNotificationTrigger(
            config={"while_running": "ignore", "match_any": True},
            source=source,
            emergency_stop=EmergencyStop(),
        )
        received: list[dict[str, Any]] = []
        trigger.start(received.append)
        trigger.set_workflow_executing(True)
        trigger._handle_notification_event(
            NotificationEvent(event_id="e1", application_id="app", title="title")
        )
        trigger.stop()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["event_id"], "e1")


if __name__ == "__main__":
    unittest.main()
