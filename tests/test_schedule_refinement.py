"""Regression tests for schedule refinement: legacy integer hours and disabled schedules.

Reference clock: Friday 2026-10-09 10:00 local time. No test depends on the system clock.
"""

import unittest
from datetime import datetime
from unittest import mock

from winflow.config.validator import validate_config
from winflow.core.errors import SchemaValidationError
from winflow.triggers.schedule import (
    ScheduleConfigurationError,
    ScheduleTrigger,
    calculate_next_run,
    canonical_schedule_fields,
    normalize_schedule_config,
    validate_schedule_trigger_config,
)

NOW = datetime(2026, 10, 9, 10, 0)


def _workflow(trigger=None, root_trigger=None):
    config = {
        "schema_version": "1.0",
        "workflow": {"id": "refinement-test", "steps": [{"action": "wait", "seconds": 0}]},
    }
    if trigger is not None:
        config["workflow"]["trigger"] = trigger
    if root_trigger is not None:
        config["trigger"] = root_trigger
    return config


class IntegerHourCompatibilityTests(unittest.TestCase):
    def test_integer_hour_means_on_the_hour(self) -> None:
        cfg = {"type": "daily", "time": 14}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 9, 14, 0))
        self.assertEqual(canonical_schedule_fields(normalize_schedule_config(cfg)),
                         {"type": "daily", "time": "14:00"})

    def test_integer_hour_bounds_are_zero_to_twenty_three(self) -> None:
        self.assertEqual(
            canonical_schedule_fields(normalize_schedule_config({"type": "daily", "time": 0}))["time"], "00:00")
        self.assertEqual(
            canonical_schedule_fields(normalize_schedule_config({"type": "daily", "time": 23}))["time"], "23:00")

    def test_integer_hour_in_legacy_nested_location_is_normalized(self) -> None:
        cfg = {"type": "daily", "config": {"time": 9}}
        self.assertEqual(canonical_schedule_fields(normalize_schedule_config(cfg))["time"], "09:00")

    def test_integer_hour_applies_to_weekly_and_one_time_locations(self) -> None:
        weekly = {"type": "weekly", "time": 7, "days": ["mon"]}
        self.assertEqual(canonical_schedule_fields(normalize_schedule_config(weekly))["time"], "07:00")
        one_time = {"type": "one_time", "date": "2026-10-10", "at": 9}
        self.assertEqual(normalize_schedule_config(one_time).run_at, datetime(2026, 10, 10, 9, 0))

    def test_integer_hour_is_accepted_at_root_and_workflow_levels(self) -> None:
        trigger = {"type": "daily", "time": 14}
        self.assertTrue(validate_config(_workflow(root_trigger=trigger), now=NOW))
        self.assertTrue(validate_config(_workflow(trigger=trigger), now=NOW))

    def test_out_of_range_integers_are_rejected(self) -> None:
        for bad in (-1, 24, 99):
            with self.subTest(hour=bad), self.assertRaisesRegex(ScheduleConfigurationError, "out of range"):
                normalize_schedule_config({"type": "daily", "time": bad})

    def test_floats_are_rejected(self) -> None:
        for bad in (14.0, 14.5, 0.0):
            with self.subTest(value=bad), self.assertRaisesRegex(ScheduleConfigurationError, "got float"):
                normalize_schedule_config({"type": "daily", "time": bad})

    def test_booleans_are_rejected_even_though_they_are_ints(self) -> None:
        for bad in (True, False):
            with self.subTest(value=bad), self.assertRaisesRegex(ScheduleConfigurationError, "boolean"):
                normalize_schedule_config({"type": "daily", "time": bad})

    def test_malformed_strings_are_rejected(self) -> None:
        for bad in ("14", "1400", "2pm", "14.5", "14:5", "24:00", "-1", "  "):
            with self.subTest(value=bad):
                with self.assertRaises(ScheduleConfigurationError):
                    normalize_schedule_config({"type": "daily", "time": bad})

    def test_strict_hh_mm_meaning_is_unchanged(self) -> None:
        for text, expected in (("14:30", "14:30"), ("09:05", "09:05"), ("9:05", "09:05"), (" 07:45 ", "07:45")):
            with self.subTest(text=text):
                cfg = {"type": "daily", "time": text}
                self.assertEqual(canonical_schedule_fields(normalize_schedule_config(cfg))["time"], expected)

    def test_integer_and_string_agreeing_values_are_accepted(self) -> None:
        cfg = {"type": "daily", "time": 14, "config": {"time": "14:00"}}
        self.assertEqual(canonical_schedule_fields(normalize_schedule_config(cfg))["time"], "14:00")
        cfg_alias = {"type": "daily", "time": 14, "at": "14:00"}
        self.assertEqual(canonical_schedule_fields(normalize_schedule_config(cfg_alias))["time"], "14:00")

    def test_conflicting_integer_and_string_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ScheduleConfigurationError, "Conflicting values for time"):
            normalize_schedule_config({"type": "daily", "time": 14, "config": {"time": "14:30"}})
        with self.assertRaisesRegex(ScheduleConfigurationError, "Conflicting values for time"):
            normalize_schedule_config({"type": "daily", "time": 14, "config": {"time": 15}})
        with self.assertRaisesRegex(SchemaValidationError, "Conflicting values for time"):
            validate_config(_workflow(root_trigger={"type": "daily", "time": 14, "config": {"time": 15}}), now=NOW)

    def test_canonical_output_is_deterministic(self) -> None:
        equivalent = [
            {"type": "weekly", "time": 14, "days": "fri,mon"},
            {"type": "weekly", "config": {"time": "14:00"}, "weekdays": ["Monday", 4]},
            {"type": "weekly", "at": "14:00", "days": [0, "friday", "mon"]},
        ]
        outputs = {repr(canonical_schedule_fields(normalize_schedule_config(cfg))) for cfg in equivalent}
        self.assertEqual(len(outputs), 1)
        self.assertEqual(canonical_schedule_fields(normalize_schedule_config(equivalent[0])),
                         {"type": "weekly", "time": "14:00", "days": ["mon", "fri"]})


class DisabledScheduleTests(unittest.TestCase):
    def test_disabled_expired_one_time_remains_loadable_and_has_no_next_run(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-08T09:00", "enabled": False}
        self.assertTrue(validate_config(_workflow(root_trigger=cfg), now=NOW))
        self.assertTrue(validate_config(_workflow(trigger=cfg), now=NOW))
        self.assertIsNotNone(validate_schedule_trigger_config(cfg, now=NOW))
        self.assertIsNone(calculate_next_run(cfg, now=NOW))

    def test_disabled_future_one_time_is_valid(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-10T09:00", "enabled": False}
        self.assertTrue(validate_config(_workflow(root_trigger=cfg), now=NOW))

    def test_enabled_expired_one_time_is_rejected_before_execution(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-08T09:00", "enabled": True}
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(_workflow(root_trigger=cfg), now=NOW)
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(_workflow(trigger=cfg), now=NOW)

    def test_enabling_an_expired_schedule_requires_a_new_future_time(self) -> None:
        cfg = {"type": "one_time", "date": "2026-10-08", "time": "09:00", "enabled": False}
        self.assertTrue(validate_config(_workflow(root_trigger=cfg), now=NOW))
        cfg["enabled"] = True
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(_workflow(root_trigger=cfg), now=NOW)
        cfg["date"] = "2026-10-11"
        self.assertTrue(validate_config(_workflow(root_trigger=cfg), now=NOW))

    def test_disabled_schedules_still_require_valid_structure(self) -> None:
        malformed = [
            {"type": "one_time", "date": "2026-13-01", "time": "09:00", "enabled": False},
            {"type": "one_time", "date": "2026-10-08", "enabled": False},          # no time
            {"type": "one_time", "time": "09:00", "enabled": False},              # no date
            {"type": "one_time", "datetime": "not-a-date", "enabled": False},
            {"type": "daily", "enabled": False},                                  # no time
            {"type": "daily", "time": "25:00", "enabled": False},
            {"type": "weekly", "time": "09:00", "days": [], "enabled": False},
            {"type": "weekly", "time": "09:00", "enabled": False},                # no days
            {"type": "daily", "time": "09:00", "enabled": "false"},               # non-boolean
        ]
        for cfg in malformed:
            with self.subTest(cfg=cfg), self.assertRaises(SchemaValidationError):
                validate_config(_workflow(root_trigger=cfg), now=NOW)

    def test_disabled_trigger_registers_no_timer_and_does_not_start(self) -> None:
        trigger = ScheduleTrigger(name="wf_daily", config={"type": "daily", "time": "14:30", "enabled": False})
        callback = mock.Mock()
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            trigger.start(callback)
        timer_cls.assert_not_called()
        callback.assert_not_called()
        self.assertFalse(trigger.is_running)

    def test_disabled_expired_one_time_starts_nothing(self) -> None:
        trigger = ScheduleTrigger(
            name="wf_once",
            config={"type": "one_time", "datetime": "2000-01-01T09:00", "enabled": False},
        )
        callback = mock.Mock()
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            trigger.start(callback)
        timer_cls.assert_not_called()
        callback.assert_not_called()

    def test_disabled_malformed_trigger_fails_start_without_side_effects(self) -> None:
        trigger = ScheduleTrigger(name="wf_bad", config={"type": "daily", "enabled": False})
        callback = mock.Mock()
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            with self.assertRaises(ScheduleConfigurationError):
                trigger.start(callback)
        timer_cls.assert_not_called()
        callback.assert_not_called()
        self.assertFalse(trigger.is_running)

    def test_enabled_expired_one_time_is_refused_at_start_for_unvalidated_configs(self) -> None:
        trigger = ScheduleTrigger(
            name="wf_once",
            config={"type": "one_time", "datetime": "2000-01-01T09:00", "enabled": True},
        )
        callback = mock.Mock()
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            with self.assertRaisesRegex(ScheduleConfigurationError, "not in the future"):
                trigger.start(callback)
        timer_cls.assert_not_called()
        callback.assert_not_called()
        self.assertFalse(trigger.is_running)

    def test_enabled_future_one_time_schedules_its_timer(self) -> None:
        trigger = ScheduleTrigger(
            name="wf_once",
            config={"type": "one_time", "datetime": "2999-01-01T09:00", "enabled": True},
        )
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            trigger.start(mock.Mock())
        timer_cls.assert_called_once()
        trigger.stop()


if __name__ == "__main__":
    unittest.main()
