"""Listener-path tests: disabled or expired schedules must never start a workflow.

These exercise WorkflowEngine.start_workflow_listener directly, the same entry point the
CLI uses. Triggers are registered through an injected registry so no global state or
desktop integration is involved.
"""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import ConfigurationError
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.trigger_registry import TriggerRegistry
from winflow.engine.workflow_engine import WorkflowEngine
from winflow.storage.execution_history import SQLiteExecutionHistory
from winflow.config.loader import load_and_validate_config
from winflow.config.validator import validate_config
from winflow.triggers import register_desktop_triggers
from winflow.triggers.schedule import ScheduleConfigurationError

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "winflow" / "workflows"


def _config(trigger):
    return {
        "schema_version": "1.0",
        "workflow": {"id": "listener-test", "steps": [{"action": "wait", "seconds": 0}]},
        "trigger": trigger,
    }


class ListenerSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        triggers = TriggerRegistry()
        register_desktop_triggers(triggers)
        self.history = SQLiteExecutionHistory(":memory:")
        self.engine = WorkflowEngine(
            action_registry=ActionRegistry(),
            trigger_registry=triggers,
            emergency_stop=EmergencyStop(),
            history_storage=self.history,
        )

    def tearDown(self) -> None:
        self.history.close()

    def _assert_no_listener(self, config) -> None:
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            result = self.engine.start_workflow_listener(config)
        self.assertIsNone(result)
        timer_cls.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_disabled_expired_one_time_does_not_start_a_listener(self) -> None:
        self._assert_no_listener(_config({"type": "one_time", "datetime": "2000-01-01T09:00", "enabled": False}))

    def test_disabled_future_daily_does_not_start_a_listener(self) -> None:
        self._assert_no_listener(_config({"type": "daily", "time": "14:30", "enabled": False}))

    def test_disabled_startup_does_not_fire(self) -> None:
        with mock.patch("winflow.triggers.schedule.ScheduleTrigger._build_event") as build_event:
            self.assertIsNone(self.engine.start_workflow_listener(_config({"type": "startup", "enabled": False})))
        build_event.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_enabled_expired_one_time_is_refused_by_the_listener(self) -> None:
        config = _config({"type": "one_time", "datetime": "2000-01-01T09:00", "enabled": True})
        with self.assertRaisesRegex(ScheduleConfigurationError, "not in the future"):
            self.engine.start_workflow_listener(config)
        self.assertEqual(self.engine.active_triggers, {})

    def test_malformed_disabled_schedule_is_not_started(self) -> None:
        config = _config({"type": "daily", "enabled": False})
        self.assertIsNone(self.engine.start_workflow_listener(config))
        self.assertEqual(self.engine.active_triggers, {})

    def test_enabled_legacy_nested_daily_starts_a_listener(self) -> None:
        config = _config({"type": "daily", "config": {"time": "14:30"}, "enabled": True})
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            trigger = self.engine.start_workflow_listener(config)
        self.assertIsNotNone(trigger)
        timer_cls.assert_called_once()
        self.engine.stop_workflow_listener(trigger.name)


class ShippedSampleListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        triggers = TriggerRegistry()
        register_desktop_triggers(triggers)
        self.history = SQLiteExecutionHistory(":memory:")
        self.engine = WorkflowEngine(
            action_registry=ActionRegistry(),
            trigger_registry=triggers,
            emergency_stop=EmergencyStop(),
            history_storage=self.history,
        )

    def tearDown(self) -> None:
        self.history.close()

    def test_asd_sample_cannot_start_its_workflow_through_the_listener_path(self) -> None:
        config = load_and_validate_config(WORKFLOWS_DIR / "asd.json")
        with mock.patch("winflow.triggers.windows_notification.WindowsNotificationTrigger.start") as start:
            result = self.engine.start_workflow_listener(config)
        self.assertIsNone(result)
        start.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_asd_sample_enabled_without_criteria_is_still_match_all_so_it_must_stay_disabled(self) -> None:
        # Documents the risk the sample description warns about: enabling the
        # placeholder as shipped would match every notification.
        config = load_and_validate_config(WORKFLOWS_DIR / "asd.json")
        enabled = copy.deepcopy(config)
        enabled["workflow"]["trigger"]["enabled"] = True
        # Still valid only because match_any is explicit; this is why the sample ships disabled.
        self.assertTrue(validate_config(enabled))
        self.assertIs(enabled["workflow"]["trigger"]["match_any"], True)

    def test_asd_sample_trigger_is_disabled_in_the_file_itself(self) -> None:
        raw = json.loads((WORKFLOWS_DIR / "asd.json").read_text(encoding="utf-8"))
        self.assertIs(raw["workflow"]["trigger"]["enabled"], False)


if __name__ == "__main__":
    unittest.main()
