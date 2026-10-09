"""Regression tests for schedule trigger configuration (canonical + legacy forms).

All expected times are derived from an injected reference clock, never from the
system clock. Reference point: Friday 2026-10-09 10:00 local time.
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
    normalize_schedule_config,
    validate_schedule_trigger_config,
)

NOW = datetime(2026, 10, 9, 10, 0)  # Friday


def _workflow(trigger=None, root_trigger=None):
    config = {
        "schema_version": "1.0",
        "workflow": {
            "id": "schedule-test",
            "steps": [{"action": "wait", "seconds": 0}],
        },
    }
    if trigger is not None:
        config["workflow"]["trigger"] = trigger
    if root_trigger is not None:
        config["trigger"] = root_trigger
    return config


class DailyScheduleTests(unittest.TestCase):
    def test_canonical_top_level_time_is_used(self) -> None:
        cfg = {"type": "daily", "time": "14:30"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 9, 14, 30))

    def test_legacy_nested_config_time_is_used_not_midnight(self) -> None:
        cfg = {"type": "daily", "config": {"time": "14:30"}}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 9, 14, 30))

    def test_daily_rolls_to_next_day_after_time_has_passed(self) -> None:
        cfg = {"type": "daily", "time": "09:15"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 10, 9, 15))

    def test_at_alias_is_accepted(self) -> None:
        cfg = {"type": "daily", "at": "07:05"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 10, 7, 5))

    def test_agreeing_top_level_and_nested_time_are_accepted(self) -> None:
        cfg = {"type": "daily", "time": "14:30", "config": {"time": "14:30"}}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 9, 14, 30))

    def test_conflicting_top_level_and_nested_time_are_rejected(self) -> None:
        cfg = {"type": "daily", "time": "14:30", "config": {"time": "09:00"}}
        with self.assertRaisesRegex(ScheduleConfigurationError, "Conflicting values for time"):
            normalize_schedule_config(cfg)
        with self.assertRaises(ScheduleConfigurationError):
            calculate_next_run(cfg, now=NOW)

    def test_missing_time_is_rejected_instead_of_defaulting_to_midnight(self) -> None:
        for cfg in (
            {"type": "daily"},
            {"type": "daily", "config": {}},
            {"type": "daily", "config": {"time": ""}},
            {"type": "daily", "time": None},
        ):
            with self.subTest(cfg=cfg), self.assertRaisesRegex(ScheduleConfigurationError, "requires 'time'"):
                calculate_next_run(cfg, now=NOW)

    def test_invalid_time_formats_are_rejected(self) -> None:
        for bad in ("25:00", "12:60", "abc", "14", "14:30:00Z", "2pm", "-1:00", 14.5, True):
            cfg = {"type": "daily", "time": bad}
            with self.subTest(time=bad), self.assertRaises(ScheduleConfigurationError):
                calculate_next_run(cfg, now=NOW)

    def test_nested_invalid_time_is_rejected(self) -> None:
        cfg = {"type": "daily", "config": {"time": "99:99"}}
        with self.assertRaisesRegex(ScheduleConfigurationError, "config.time|'time'"):
            calculate_next_run(cfg, now=NOW)

    def test_disabled_daily_trigger_has_no_next_run(self) -> None:
        self.assertIsNone(calculate_next_run({"type": "daily", "time": "14:30", "enabled": False}, now=NOW))


class WeeklyScheduleTests(unittest.TestCase):
    def test_weekly_with_valid_weekday_names(self) -> None:
        cfg = {"type": "weekly", "time": "10:00", "days": ["mon", "wed"]}
        # Friday 10:00 -> next Monday 10:00
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 12, 10, 0))

    def test_weekly_accepts_comma_string_and_weekdays_alias(self) -> None:
        cfg = {"type": "weekly", "config": {"time": "10:00"}, "weekdays": "Mon, Fri"}
        # Friday 10:00 is not after 10:00 now, so the next match is Monday Oct 12.
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 12, 10, 0))

    def test_weekly_includes_today_when_time_is_still_ahead(self) -> None:
        cfg = {"type": "weekly", "time": "14:00", "days": [4]}  # 4 = Friday (Mon=0)
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 9, 14, 0))

    def test_weekly_without_days_is_rejected(self) -> None:
        for cfg in (
            {"type": "weekly", "time": "10:00"},
            {"type": "weekly", "time": "10:00", "days": []},
            {"type": "weekly", "time": "10:00", "days": ""},
            {"type": "weekly", "time": "10:00", "days": None},
        ):
            with self.subTest(cfg=cfg), self.assertRaises(ScheduleConfigurationError):
                calculate_next_run(cfg, now=NOW)

    def test_weekly_with_unknown_or_out_of_range_days_is_rejected(self) -> None:
        for days in ("funday", "mon,xyz", ["mon", "blur"], 7, [9], True):
            cfg = {"type": "weekly", "time": "10:00", "days": days}
            with self.subTest(days=days), self.assertRaises(ScheduleConfigurationError):
                calculate_next_run(cfg, now=NOW)

    def test_weekly_without_time_is_rejected(self) -> None:
        cfg = {"type": "weekly", "days": ["mon"]}
        with self.assertRaisesRegex(ScheduleConfigurationError, "requires 'time'"):
            calculate_next_run(cfg, now=NOW)

    def test_weekly_conflicting_days_are_rejected(self) -> None:
        cfg = {"type": "weekly", "time": "10:00", "days": ["mon"], "weekdays": ["tue"]}
        with self.assertRaisesRegex(ScheduleConfigurationError, "Conflicting values for weekdays"):
            normalize_schedule_config(cfg)


class OneTimeScheduleTests(unittest.TestCase):
    def test_future_datetime_field_is_scheduled(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-10T09:00"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 10, 9, 0))
        self.assertIsNotNone(validate_schedule_trigger_config(cfg, now=NOW))

    def test_date_and_time_fields_are_combined(self) -> None:
        cfg = {"type": "one_time", "date": "2026-12-31", "time": "23:45"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 12, 31, 23, 45))

    def test_nested_legacy_date_and_time_are_combined(self) -> None:
        cfg = {"type": "one_time", "config": {"date": "2026-12-31", "time": "23:45"}}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 12, 31, 23, 45))

    def test_matching_datetime_and_date_time_are_accepted(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-10T09:00", "date": "2026-10-10", "time": "09:00"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), datetime(2026, 10, 10, 9, 0))

    def test_conflicting_datetime_and_date_are_rejected(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-10T09:00", "date": "2026-10-11"}
        with self.assertRaisesRegex(ScheduleConfigurationError, "conflicts with 'datetime'"):
            normalize_schedule_config(cfg)

    def test_conflicting_datetime_and_time_are_rejected(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-10T09:00", "time": "17:00"}
        with self.assertRaisesRegex(ScheduleConfigurationError, "conflicts with 'datetime'"):
            normalize_schedule_config(cfg)

    def test_missing_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScheduleConfigurationError, "requires 'datetime'"):
            validate_schedule_trigger_config({"type": "one_time", "time": "09:00"}, now=NOW)

    def test_date_without_time_is_rejected_instead_of_midnight(self) -> None:
        cfg = {"type": "one_time", "date": "2026-10-10"}
        with self.assertRaisesRegex(ScheduleConfigurationError, "requires 'time'"):
            validate_schedule_trigger_config(cfg, now=NOW)

    def test_invalid_date_and_datetime_are_rejected(self) -> None:
        for cfg in (
            {"type": "one_time", "date": "2026-13-01", "time": "09:00"},
            {"type": "one_time", "date": "10/10/2026", "time": "09:00"},
            {"type": "one_time", "date": "2026-02-30", "time": "09:00"},
            {"type": "one_time", "datetime": "2026-10-10"},
            {"type": "one_time", "datetime": "not-a-date"},
            {"type": "one_time", "datetime": "2026-10-10T09:00+02:00"},
        ):
            with self.subTest(cfg=cfg), self.assertRaises(ScheduleConfigurationError):
                validate_schedule_trigger_config(cfg, now=NOW)

    def test_past_one_time_is_rejected_at_validation(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-08T09:00"}
        with self.assertRaisesRegex(ScheduleConfigurationError, "not in the future"):
            validate_schedule_trigger_config(cfg, now=NOW)

    def test_exactly_now_is_not_in_the_future(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-09T10:00"}
        with self.assertRaisesRegex(ScheduleConfigurationError, "not in the future"):
            validate_schedule_trigger_config(cfg, now=NOW)

    def test_past_one_time_has_no_runtime_run(self) -> None:
        cfg = {"type": "one_time", "datetime": "2026-10-08T09:00"}
        self.assertIsNone(calculate_next_run(cfg, now=NOW))


class StartupScheduleTests(unittest.TestCase):
    def test_startup_requires_no_time_fields(self) -> None:
        cfg = {"type": "startup"}
        self.assertEqual(validate_schedule_trigger_config(cfg, now=NOW).trigger_type, "startup")
        self.assertEqual(calculate_next_run(cfg, now=NOW), NOW)

    def test_startup_ignores_clock_fields(self) -> None:
        cfg = {"type": "startup", "time": "not-a-time"}
        self.assertEqual(calculate_next_run(cfg, now=NOW), NOW)

    def test_startup_trigger_fires_immediately_on_start(self) -> None:
        calls = []
        trigger = ScheduleTrigger(name="wf_startup", config={"type": "startup", "workflow_id": "wf"})
        trigger.start(calls.append)
        trigger.stop()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["scheduled"])


class UnsupportedScheduleTests(unittest.TestCase):
    def test_unknown_schedule_type_is_rejected_by_normalizer(self) -> None:
        with self.assertRaisesRegex(ScheduleConfigurationError, "Unsupported schedule trigger type"):
            normalize_schedule_config({"type": "hourly", "time": "10:00"})

    def test_non_dict_config_is_rejected(self) -> None:
        with self.assertRaises(ScheduleConfigurationError):
            normalize_schedule_config("daily")

    def test_non_dict_nested_config_is_rejected(self) -> None:
        with self.assertRaises(ScheduleConfigurationError):
            normalize_schedule_config({"type": "daily", "config": "14:30"})


class ScheduleTriggerRuntimeTests(unittest.TestCase):
    def test_invalid_daily_trigger_fails_start_without_timer_or_running_state(self) -> None:
        trigger = ScheduleTrigger(name="wf_daily", config={"type": "daily", "config": {"time": ""}})
        with mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            with self.assertRaises(ScheduleConfigurationError):
                trigger.start(lambda event: None)
        timer_cls.assert_not_called()
        self.assertFalse(trigger.is_running)

    def test_valid_legacy_daily_trigger_schedules_at_requested_time(self) -> None:
        trigger = ScheduleTrigger(name="wf_daily", config={"type": "daily", "config": {"time": "14:30"}})
        fixed_now = datetime(2026, 10, 9, 10, 0)
        with mock.patch("winflow.triggers.schedule.datetime") as fake_datetime, \
                mock.patch("winflow.triggers.schedule.Timer") as timer_cls:
            fake_datetime.now.return_value = fixed_now
            fake_datetime.combine = datetime.combine
            trigger.start(lambda event: None)
        delay = timer_cls.call_args.args[0]
        self.assertAlmostEqual(delay, 4.5 * 3600, places=3)
        trigger.stop()


class SchemaLevelScheduleValidationTests(unittest.TestCase):
    """Root-level and workflow-level trigger blocks must receive identical checks."""

    def test_root_and_workflow_forms_both_accept_valid_canonical_daily(self) -> None:
        trigger = {"type": "daily", "time": "14:30"}
        self.assertTrue(validate_config(_workflow(root_trigger=trigger), now=NOW))
        self.assertTrue(validate_config(_workflow(trigger=trigger), now=NOW))

    def test_root_and_workflow_forms_both_accept_legacy_nested_daily(self) -> None:
        trigger = {"type": "daily", "config": {"time": "14:30"}}
        self.assertTrue(validate_config(_workflow(root_trigger=trigger), now=NOW))
        self.assertTrue(validate_config(_workflow(trigger=trigger), now=NOW))

    def test_root_and_workflow_forms_both_reject_daily_without_time(self) -> None:
        trigger = {"type": "daily", "config": {"time": ""}}
        with self.assertRaisesRegex(SchemaValidationError, "'trigger'.*requires 'time'"):
            validate_config(_workflow(root_trigger=trigger), now=NOW)
        with self.assertRaisesRegex(SchemaValidationError, "'workflow.trigger'.*requires 'time'"):
            validate_config(_workflow(trigger=trigger), now=NOW)

    def test_validation_rejects_past_one_time_with_injected_clock(self) -> None:
        trigger = {"type": "one_time", "datetime": "2026-10-08T09:00"}
        with self.assertRaisesRegex(SchemaValidationError, "not in the future"):
            validate_config(_workflow(root_trigger=trigger), now=NOW)
        future = {"type": "one_time", "datetime": "2026-10-10T09:00"}
        self.assertTrue(validate_config(_workflow(root_trigger=future), now=NOW))

    def test_validation_rejects_weekly_without_days(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "requires 'days'"):
            validate_config(_workflow(trigger={"type": "weekly", "time": "10:00"}), now=NOW)

    def test_validation_rejects_conflicting_nested_and_top_level_time(self) -> None:
        trigger = {"type": "daily", "time": "14:30", "config": {"time": "09:00"}}
        with self.assertRaisesRegex(SchemaValidationError, "Conflicting"):
            validate_config(_workflow(root_trigger=trigger), now=NOW)


if __name__ == "__main__":
    unittest.main()
