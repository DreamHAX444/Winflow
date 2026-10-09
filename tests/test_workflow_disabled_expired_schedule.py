"""Disabled workflows with expired one-time schedules.

Rules under test:
* Structure is always validated (enabled or disabled, workflow or trigger).
* An expired enabled one-time schedule blocks activation only when the workflow is
  effectively enabled (workflow.enabled AND settings.enabled, both defaulting to true).
* A disabled workflow loads and can be edited, but never starts a listener or timer.
* Enabling the workflow without updating the expired schedule fails clearly, before
  any timer or execution is created, including when a config bypasses load validation.

The reference clock is injected. The loader-path tests use a date in 2000 (always in
the past) and 2999 (always in the future), so they do not depend on the current date.
"""

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from winflow.config.enablement import is_workflow_enabled
from winflow.config.loader import load_and_validate_config
from winflow.config.validator import validate_config
from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import ConfigurationError, SchemaValidationError
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.trigger_registry import TriggerRegistry
from winflow.engine.workflow_engine import WorkflowEngine
from winflow.storage.execution_history import SQLiteExecutionHistory
from winflow.triggers import register_desktop_triggers
from winflow.triggers.schedule import ScheduleConfigurationError

NOW = datetime(2026, 10, 9, 10, 0)  # Friday; injected for validate_config


def _config(trigger, workflow_enabled=None, settings_enabled=None):
    # Deep-copy so tests that edit a config never mutate the shared module constants.
    config = {
        "schema_version": "1.0",
        "workflow": {"id": "disabled-expired", "steps": [{"action": "wait", "seconds": 0}]},
        "trigger": copy.deepcopy(trigger),
    }
    if workflow_enabled is not None:
        config["workflow"]["enabled"] = workflow_enabled
    if settings_enabled is not None:
        config["settings"] = {"enabled": settings_enabled}
    return config


EXPIRED_ONE_TIME = {"type": "one_time", "datetime": "2000-01-01T09:00", "enabled": True}
FUTURE_ONE_TIME = {"type": "one_time", "datetime": "2999-01-01T09:00", "enabled": True}


class EffectiveEnablementTests(unittest.TestCase):
    def test_defaults_to_enabled_when_flags_are_absent(self) -> None:
        self.assertTrue(is_workflow_enabled(_config(None)))

    def test_workflow_flag_disables(self) -> None:
        self.assertFalse(is_workflow_enabled(_config(None, workflow_enabled=False)))

    def test_settings_flag_disables_even_when_workflow_is_enabled(self) -> None:
        self.assertFalse(is_workflow_enabled(_config(None, workflow_enabled=True, settings_enabled=False)))

    def test_non_boolean_flags_are_rejected(self) -> None:
        bad = _config(None)
        bad["workflow"]["enabled"] = "false"
        with self.assertRaisesRegex(ConfigurationError, "workflow.enabled"):
            is_workflow_enabled(bad)

    def test_engine_and_shared_helper_agree_on_every_combination(self) -> None:
        for workflow_flag in (None, True, False):
            for settings_flag in (None, True, False):
                config = _config(None, workflow_enabled=workflow_flag, settings_enabled=settings_flag)
                with self.subTest(workflow=workflow_flag, settings=settings_flag):
                    self.assertEqual(
                        WorkflowEngine._workflow_is_enabled(config),
                        is_workflow_enabled(config),
                    )


class DisabledWorkflowLoadTests(unittest.TestCase):
    def test_1_disabled_workflow_with_expired_enabled_one_time_loads(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        self.assertTrue(validate_config(config, now=NOW))

    def test_disabled_via_settings_flag_also_loads(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=True, settings_enabled=False)
        self.assertTrue(validate_config(config, now=NOW))

    def test_disabled_workflow_loads_from_file_through_the_loader(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "disabled_expired.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            loaded = load_and_validate_config(path)
        self.assertIs(loaded["workflow"]["enabled"], False)

    def test_disabled_workflow_with_workflow_level_trigger_also_loads(self) -> None:
        config = {
            "schema_version": "1.0",
            "workflow": {
                "id": "nested", "enabled": False,
                "trigger": dict(EXPIRED_ONE_TIME),
                "steps": [{"action": "wait", "seconds": 0}],
            },
        }
        self.assertTrue(validate_config(config, now=NOW))


class DisabledWorkflowRuntimeTests(unittest.TestCase):
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

    def test_2_disabled_workflow_cannot_start_a_listener(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls, \
                mock.patch("winflow.triggers.schedule.ScheduleTrigger.start") as trigger_start:
            self.assertIsNone(self.engine.start_workflow_listener(config))
        timer_cls.assert_not_called()
        trigger_start.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_disabled_workflow_cannot_execute_its_workflow(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        with self.assertRaisesRegex(ConfigurationError, "disabled"):
            self.engine.run_workflow(config)

    def test_disabled_workflow_with_future_schedule_also_starts_nothing(self) -> None:
        config = _config(FUTURE_ONE_TIME, workflow_enabled=False)
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            self.assertIsNone(self.engine.start_workflow_listener(config))
        timer_cls.assert_not_called()

    def test_3_enabling_without_updating_expired_schedule_fails_validation(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        self.assertTrue(validate_config(config, now=NOW))
        config["workflow"]["enabled"] = True
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(config, now=NOW)
        with self.assertRaisesRegex(SchemaValidationError, "Set a new future date/time"):
            validate_config(config, now=NOW)

    def test_3b_enabling_fails_clearly_through_the_loader(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "enabled_expired.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
                load_and_validate_config(path)

    def test_3c_enabling_without_validation_still_cannot_start_a_timer(self) -> None:
        # Bypass for configs passed directly to the engine: the disabled config is
        # flipped to enabled in memory and handed to the listener without reloading.
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        config = copy.deepcopy(config)
        config["workflow"]["enabled"] = True
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            with self.assertRaisesRegex(ScheduleConfigurationError, "not in the future"):
                self.engine.start_workflow_listener(config)
        timer_cls.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_updating_the_schedule_to_the_future_allows_activation(self) -> None:
        config = _config(EXPIRED_ONE_TIME, workflow_enabled=False)
        config["workflow"]["enabled"] = True
        config["trigger"]["datetime"] = "2999-01-01T09:00"
        self.assertTrue(validate_config(config, now=NOW))
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            trigger = self.engine.start_workflow_listener(config)
        self.assertIsNotNone(trigger)
        timer_cls.assert_called_once()
        self.engine.stop_workflow_listener(trigger.name)


class ActivationAndTimerTests(unittest.TestCase):
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

    def test_4_enabled_workflow_with_expired_enabled_trigger_is_rejected_before_activation(self) -> None:
        config = _config(EXPIRED_ONE_TIME)
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(config, now=NOW)
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            with self.assertRaises(ScheduleConfigurationError):
                self.engine.start_workflow_listener(config)
        timer_cls.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_5_disabled_trigger_does_not_register_a_timer_in_an_enabled_workflow(self) -> None:
        config = _config({"type": "daily", "time": "14:30", "enabled": False}, workflow_enabled=True)
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            self.assertIsNone(self.engine.start_workflow_listener(config))
        timer_cls.assert_not_called()
        self.assertEqual(self.engine.active_triggers, {})

    def test_5b_disabled_expired_trigger_in_enabled_workflow_registers_no_timer(self) -> None:
        trigger = dict(EXPIRED_ONE_TIME, enabled=False)
        config = _config(trigger, workflow_enabled=True)
        self.assertTrue(validate_config(config, now=NOW))
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            self.assertIsNone(self.engine.start_workflow_listener(config))
        timer_cls.assert_not_called()


class StructureStillValidatedWhenDisabledTests(unittest.TestCase):
    def test_6_invalid_structure_is_rejected_even_when_the_workflow_is_disabled(self) -> None:
        malformed = [
            {"type": "one_time", "datetime": "2000-01-01T25:00", "enabled": True},
            {"type": "one_time", "date": "2000-13-01", "time": "09:00", "enabled": True},
            {"type": "one_time", "date": "2000-01-01", "enabled": True},          # no time
            {"type": "one_time", "time": "09:00", "enabled": True},              # no date
            {"type": "one_time", "datetime": "2000-01-01T09:00", "date": "2000-01-02", "enabled": True},
            {"type": "one_time", "datetime": "2999-01-01T09:00", "time": "17:00", "enabled": False},
            {"type": "daily", "enabled": False},                                  # no time
            {"type": "daily", "time": 25, "enabled": True},
            {"type": "daily", "time": 14.5, "enabled": True},
            {"type": "daily", "time": True, "enabled": True},
            {"type": "daily", "time": "09:00", "config": {"time": "10:00"}},     # conflict
            {"type": "weekly", "time": "09:00", "days": [], "enabled": True},
            {"type": "weekly", "time": "09:00", "enabled": True},                # no days
            {"type": "daily", "time": "09:00", "enabled": "false"},
        ]
        for trigger in malformed:
            for workflow_enabled in (False, True):
                with self.subTest(trigger=trigger, workflow_enabled=workflow_enabled):
                    config = _config(trigger, workflow_enabled=workflow_enabled)
                    with self.assertRaises(SchemaValidationError):
                        validate_config(config, now=NOW)

    def test_disabled_workflow_with_malformed_notification_is_still_rejected(self) -> None:
        for trigger in (
            {"type": "windows_notification"},                                    # no criteria
            {"type": "windows_notification", "app_name": "DemoApp"},             # legacy key
            {"type": "windows_notification", "match": {"application": ""}},      # empty value
        ):
            with self.subTest(trigger=trigger):
                with self.assertRaises(SchemaValidationError):
                    validate_config(_config(trigger, workflow_enabled=False), now=NOW)


class UnchangedBehaviourTests(unittest.TestCase):
    """Non-regression: valid schedules and notification triggers keep their behaviour."""

    def test_7_valid_schedules_validate_identically_enabled_and_disabled(self) -> None:
        valid = [
            {"type": "daily", "time": "14:30"},
            {"type": "daily", "config": {"time": 14}},
            {"type": "weekly", "time": "10:00", "days": ["mon", "fri"]},
            {"type": "startup"},
            {"type": "one_time", "datetime": "2999-01-01T09:00"},
        ]
        for trigger in valid:
            for workflow_enabled in (True, False):
                with self.subTest(trigger=trigger, workflow_enabled=workflow_enabled):
                    self.assertTrue(validate_config(_config(trigger, workflow_enabled=workflow_enabled), now=NOW))

    def test_notification_match_any_and_criteria_validate_unchanged(self) -> None:
        self.assertTrue(validate_config(_config({"type": "windows_notification", "match_any": True}), now=NOW))
        self.assertTrue(validate_config(_config(
            {"type": "windows_notification", "match": {"application": "Example"}}, workflow_enabled=False,
        ), now=NOW))

    def test_enabled_expired_one_time_still_rejected_when_workflow_is_explicitly_enabled(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(_config(EXPIRED_ONE_TIME, workflow_enabled=True, settings_enabled=True), now=NOW)


if __name__ == "__main__":
    unittest.main()
