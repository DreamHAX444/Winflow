import threading
import time
import unittest
from typing import Any

from winflow.actions.base import BaseAction
from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import ActionExecutionError, WorkflowExecutionError
from winflow.core.execution_lock import DesktopExecutionLock
from winflow.core.timeout import execute_with_timeout
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.execution_context import ExecutionContext
from winflow.engine.workflow_runner import WorkflowRunner


class _SlowAction(BaseAction):
    lock: DesktopExecutionLock | None = None
    lock_states: list[bool] = []

    def execute(self, context: ExecutionContext, **kwargs: Any) -> None:
        assert self.lock is not None
        # Read the owner marker directly: the real lock is deliberately held by
        # the runner thread, so acquiring its RLock from this action thread would
        # block until the very operation under test has returned.
        self.lock_states.append(self.lock._owner == context.execution_id)
        time.sleep(0.05)
        # The timed-out action must still own the lock until its native-like
        # operation has actually completed.
        self.lock_states.append(self.lock._owner == context.execution_id)


class _FailingHistory:
    def record_start(self, **_kwargs: Any) -> None:
        raise RuntimeError("simulated history startup failure")

    def record_complete(self, **_kwargs: Any) -> None:
        return None

    def record_trace_event(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _OverlapAction(BaseAction):
    active = 0
    max_active = 0
    calls = 0
    lock = threading.Lock()

    def execute(self, context: ExecutionContext, **kwargs: Any) -> None:
        with self.lock:
            type(self).active += 1
            type(self).calls += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            # Deliberately ignore cooperative cancellation; the wrapper must
            # wait for this attempt before the retry begins.
            time.sleep(0.035)
        finally:
            with self.lock:
                type(self).active -= 1


class TimeoutSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.emergency_stop = EmergencyStop()

    def test_timeout_signals_worker_and_waits_until_it_stops(self) -> None:
        context = ExecutionContext("cooperative-timeout", emergency_stop=self.emergency_stop)
        started = threading.Event()
        stopped = threading.Event()

        def operation() -> None:
            started.set()
            try:
                while True:
                    context.check_cancellation()
                    time.sleep(0.002)
            except ActionExecutionError:
                stopped.set()
                raise

        with self.assertRaisesRegex(ActionExecutionError, "timed out"):
            execute_with_timeout(operation, 0.02, context, "cooperative")
        self.assertTrue(started.is_set())
        self.assertTrue(stopped.is_set())
        time.sleep(0.01)
        self.assertTrue(stopped.is_set())

    def test_desktop_lock_is_held_until_a_timed_out_operation_finishes(self) -> None:
        lock = DesktopExecutionLock()
        _SlowAction.lock = lock
        _SlowAction.lock_states = []
        registry = ActionRegistry()
        registry.register("slow", _SlowAction)
        runner = WorkflowRunner(
            {
                "workflow": {"id": "lock-timeout", "steps": [{"action": "slow"}]},
                "settings": {"timeout_seconds": 0.005},
            },
            action_registry=registry,
            desktop_lock=lock,
            emergency_stop=self.emergency_stop,
        )
        context = ExecutionContext("lock-timeout", emergency_stop=self.emergency_stop)

        started_at = time.monotonic()
        with self.assertRaisesRegex(ActionExecutionError, "timed out"):
            runner.run(context)
        elapsed = time.monotonic() - started_at

        self.assertGreaterEqual(elapsed, 0.04)
        self.assertEqual(_SlowAction.lock_states, [True, True])
        self.assertFalse(lock.is_locked)

    def test_history_start_failure_releases_desktop_lock(self) -> None:
        lock = DesktopExecutionLock()
        runner = WorkflowRunner(
            {
                "workflow": {"id": "history-failure", "steps": [{"action": "unused"}]},
                "settings": {"use_desktop_lock": True},
            },
            desktop_lock=lock,
            emergency_stop=self.emergency_stop,
            history_storage=_FailingHistory(),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(
            WorkflowExecutionError, "simulated history startup failure"
        ):
            runner.run(
                ExecutionContext("history-failure", emergency_stop=self.emergency_stop)
            )

        self.assertFalse(lock.is_locked)

    def test_global_timeout_is_a_legacy_alias_and_canonical_setting_wins(self) -> None:
        registry = ActionRegistry()
        registry.register("overlap", _OverlapAction)
        _OverlapAction.active = 0
        _OverlapAction.max_active = 0
        _OverlapAction.calls = 0

        legacy_runner = WorkflowRunner(
            {
                "workflow": {"id": "legacy-timeout", "steps": [{"action": "overlap"}]},
                "settings": {
                    "use_desktop_lock": False,
                    "global_timeout_seconds": 0.005,
                },
            },
            action_registry=registry,
            emergency_stop=self.emergency_stop,
        )
        with self.assertRaisesRegex(ActionExecutionError, "timed out"):
            legacy_runner.run(
                ExecutionContext("legacy-timeout", emergency_stop=self.emergency_stop)
            )
        self.assertEqual(_OverlapAction.calls, 1)

        # The new canonical key wins when both settings are present. A zero
        # timeout intentionally preserves the existing no-timeout semantics.
        _OverlapAction.active = 0
        _OverlapAction.max_active = 0
        _OverlapAction.calls = 0
        canonical_runner = WorkflowRunner(
            {
                "workflow": {"id": "canonical-timeout", "steps": [{"action": "overlap"}]},
                "settings": {
                    "use_desktop_lock": False,
                    "timeout_seconds": 0.0,
                    "global_timeout_seconds": 0.005,
                },
            },
            action_registry=registry,
            emergency_stop=self.emergency_stop,
        )
        canonical_runner.run(
            ExecutionContext("canonical-timeout", emergency_stop=self.emergency_stop)
        )
        self.assertEqual(_OverlapAction.calls, 1)

    def test_retries_do_not_overlap_a_timed_out_attempt(self) -> None:
        _OverlapAction.active = 0
        _OverlapAction.max_active = 0
        _OverlapAction.calls = 0
        registry = ActionRegistry()
        registry.register("overlap", _OverlapAction)
        runner = WorkflowRunner(
            {
                "workflow": {
                    "id": "retry-timeout",
                    "steps": [
                        {
                            "action": "overlap",
                            "timeout_seconds": 0.005,
                            "failure": {"policy": "retry", "attempts": 2},
                        }
                    ],
                },
                "settings": {"use_desktop_lock": False},
            },
            action_registry=registry,
            emergency_stop=self.emergency_stop,
        )

        with self.assertRaisesRegex(ActionExecutionError, "timed out"):
            runner.run(ExecutionContext("retry-timeout", emergency_stop=self.emergency_stop))

        self.assertEqual(_OverlapAction.calls, 2)
        self.assertEqual(_OverlapAction.max_active, 1)
        self.assertEqual(_OverlapAction.active, 0)


if __name__ == "__main__":
    unittest.main()
