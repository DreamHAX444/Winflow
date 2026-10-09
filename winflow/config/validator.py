"""Configuration validator for WinFlow workflows.

Validates the structure and runtime-supported values of workflow definitions
before the engine starts listeners or performs desktop actions.
"""

import math
from datetime import datetime
from typing import Any

from winflow.config.enablement import is_workflow_enabled
from winflow.config.initial_variables import config_variables
from winflow.config.step_aliases import canonical_step
from winflow.config.schema import SUPPORTED_SCHEMA_VERSIONS, WINFLOW_CONFIG_SCHEMA
from winflow.core.emergency_hotkey import parse_emergency_hotkey
from winflow.core.errors import ConfigurationError, SchemaValidationError, TriggerConfigurationError
from winflow.triggers.matcher import validate_notification_trigger_config
from winflow.triggers.schedule import (
    SCHEDULE_TRIGGER_TYPES,
    ScheduleConfigurationError,
    validate_schedule_trigger_config,
)

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _finite_non_negative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0
    except (OverflowError, TypeError, ValueError):
        return False


def _validate_loop_block(loop: Any, path: str) -> None:
    if not isinstance(loop, dict):
        raise SchemaValidationError(f"'{path}' must be a dictionary, got {type(loop).__name__}")

    if "count" in loop:
        count = loop["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            if isinstance(count, str) and count.strip().lower() == "infinite":
                raise SchemaValidationError(
                    f"'{path}.count'='infinite' is not supported; configure a finite positive integer."
                )
            raise SchemaValidationError(f"'{path}.count' must be a positive integer.")

    delay = loop.get("delay_between_seconds", 0.0)
    if not _finite_non_negative(delay):
        raise SchemaValidationError(
            f"'{path}.delay_between_seconds' must be a finite non-negative number."
        )


def validate_config(config: Any, now: datetime | None = None) -> bool:
    """Validate a parsed workflow configuration against schema and runtime rules.

    Args:
        config: Parsed workflow configuration.
        now: Reference time for one-time schedule checks (defaults to the local clock).

    Raises:
        SchemaValidationError: If the structure or any runtime-supported value is invalid.
    """
    if not isinstance(config, dict):
        raise SchemaValidationError(
            f"Configuration root must be a dictionary/object, got {type(config).__name__}"
        )

    if "schema_version" not in config:
        raise SchemaValidationError("Configuration missing required field: 'schema_version'")
    schema_version = config["schema_version"]
    if not isinstance(schema_version, str):
        raise SchemaValidationError(
            f"'schema_version' must be a string, got {type(schema_version).__name__}"
        )
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise SchemaValidationError(
            f"Unsupported schema_version '{schema_version}'. Supported versions: {supported}"
        )

    workflow = config.get("workflow")
    if not isinstance(workflow, dict):
        raise SchemaValidationError("'workflow' must be a dictionary/object")
    if "id" not in workflow or not isinstance(workflow["id"], str) or not workflow["id"].strip():
        raise SchemaValidationError("'workflow' must contain a non-empty string 'id'")
    if "enabled" in workflow and not isinstance(workflow["enabled"], bool):
        raise SchemaValidationError("'workflow.enabled' must be a boolean")

    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        raise SchemaValidationError(
            f"'settings' must be a dictionary, got {type(settings).__name__}"
        )
    if "enabled" in settings and not isinstance(settings["enabled"], bool):
        raise SchemaValidationError("'settings.enabled' must be a boolean")
    for timeout_key in ("timeout_seconds", "global_timeout_seconds"):
        if timeout_key in settings and not _finite_non_negative(settings[timeout_key]):
            raise SchemaValidationError(
                f"'settings.{timeout_key}' must be a finite non-negative number"
            )
    if "retry_attempts" in settings:
        retry_attempts = settings["retry_attempts"]
        if isinstance(retry_attempts, bool) or not isinstance(retry_attempts, int) or retry_attempts < 0:
            raise SchemaValidationError(
                "'settings.retry_attempts' must be a non-negative integer "
                "(number of retries after the first attempt)"
            )
    if "retry_delay_seconds" in settings and not _finite_non_negative(settings["retry_delay_seconds"]):
        raise SchemaValidationError("'settings.retry_delay_seconds' must be a finite non-negative number")
    if "emergency_stop_hotkey" in settings:
        try:
            parse_emergency_hotkey(settings["emergency_stop_hotkey"])
        except ValueError as exc:
            raise SchemaValidationError(
                f"Invalid 'settings.emergency_stop_hotkey': {exc}"
            ) from exc

    if "steps" in workflow:
        steps = workflow["steps"]
    elif "steps" in config:
        steps = config["steps"]
    else:
        raise SchemaValidationError(
            "Configuration missing required field: 'steps' (expected under 'workflow' or at root)"
        )
    if not isinstance(steps, list):
        raise SchemaValidationError(f"'steps' must be a list, got {type(steps).__name__}")
    if not steps:
        raise SchemaValidationError("'steps' list must contain at least one step")

    max_depth = settings.get("max_control_depth", 10)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise SchemaValidationError("'settings.max_control_depth' must be a non-negative integer")

    def _validate_steps(steps_list: Any, current_depth: int, path: str) -> None:
        if not isinstance(steps_list, list):
            raise SchemaValidationError(f"'{path}' must be a list of steps")
        if current_depth > max_depth:
            raise SchemaValidationError(
                f"Configuration exceeds maximum control depth of {max_depth}"
            )

        for index, step in enumerate(steps_list):
            step_path = f"{path}[{index}]"
            if not isinstance(step, dict):
                raise SchemaValidationError(
                    f"Step at {step_path} must be a dictionary/object"
                )
            action = step.get("action")
            if not isinstance(action, str) or not action.strip():
                raise SchemaValidationError(
                    f"Step at {step_path} missing required non-empty string 'action'"
                )
            try:
                canonical_step(step, step_path)
            except ConfigurationError as exc:
                raise SchemaValidationError(str(exc)) from exc
            for key in ("verify", "verification", "failure", "retry"):
                if key in step and not isinstance(step[key], dict):
                    raise SchemaValidationError(f"Step at {step_path} '{key}' must be a dictionary")
            for timeout_key in ("timeout", "timeout_seconds"):
                if timeout_key in step and not _finite_non_negative(step[timeout_key]):
                    raise SchemaValidationError(
                        f"Step at {step_path} '{timeout_key}' must be a finite non-negative number"
                    )
            if action == "if":
                condition = step.get("condition", {})
                if not isinstance(condition, dict):
                    raise SchemaValidationError(f"Condition at {step_path} must be an object")
                for branch in ("then", "else"):
                    if branch in step:
                        _validate_steps(step[branch], current_depth + 1, f"{step_path}.{branch}")
            elif action in {"while", "for_each"}:
                limit_name = "max_iterations" if action == "while" else "max_items"
                if limit_name in step:
                    value = step[limit_name]
                    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                        raise SchemaValidationError(
                            f"'{step_path}.{limit_name}' must be a positive integer"
                        )
                condition = step.get("condition")
                if condition is not None and not isinstance(condition, dict):
                    raise SchemaValidationError(f"Condition at {step_path} must be an object")
                body = step.get("do", step.get("steps", []))
                _validate_steps(body, current_depth + 1, f"{step_path}.do")

    _validate_steps(steps, 0, "steps")

    # Raises SchemaValidationError for malformed, reserved, or conflicting variables.
    config_variables(config)

    for loop_path, block in (("loop", config), ("workflow.loop", workflow)):
        if "loop" in block:
            _validate_loop_block(block["loop"], loop_path)

    def _validate_trigger_block(trigger: Any, path: str, workflow_enabled: bool) -> None:
        _validate_trigger_structure(trigger, path)
        trigger_type = str(trigger.get("type", "")).strip().lower() if "type" in trigger else ""
        if trigger_type == "windows_notification":
            try:
                validate_notification_trigger_config(trigger)
            except TriggerConfigurationError as exc:
                raise SchemaValidationError(f"'{path}': {exc.message}") from exc
        elif trigger_type in SCHEDULE_TRIGGER_TYPES:
            try:
                validate_schedule_trigger_config(trigger, now=now, workflow_enabled=workflow_enabled)
            except ScheduleConfigurationError as exc:
                raise SchemaValidationError(f"'{path}': {exc.message}") from exc

    def _validate_trigger_structure(trigger: Any, path: str) -> None:
        if not isinstance(trigger, dict):
            raise SchemaValidationError(
                f"'{path}' must be a dictionary, got {type(trigger).__name__}"
            )
        if "type" in trigger and (
            not isinstance(trigger["type"], str) or not trigger["type"].strip()
        ):
            raise SchemaValidationError(f"'{path}.type' must be a non-empty string")
        if "enabled" in trigger and not isinstance(trigger["enabled"], bool):
            raise SchemaValidationError(f"'{path}.enabled' must be a boolean")
        if "match" in trigger and not isinstance(trigger["match"], dict):
            raise SchemaValidationError(f"'{path}.match' must be a dictionary")
        if "deduplication" in trigger and not isinstance(
            trigger["deduplication"], (bool, dict)
        ):
            raise SchemaValidationError(
                f"'{path}.deduplication' must be a boolean or dictionary"
            )
        if "cooldown_seconds" in trigger and not _finite_non_negative(
            trigger["cooldown_seconds"]
        ):
            raise SchemaValidationError(
                f"'{path}.cooldown_seconds' must be a finite non-negative number"
            )
        for policy_key in ("while_running", "while_running_policy"):
            if policy_key not in trigger:
                continue
            policy = trigger[policy_key]
            normalized_policy = policy.strip().lower() if isinstance(policy, str) else policy
            if normalized_policy in {"terminate_and_restart", "run_concurrently"}:
                raise SchemaValidationError(
                    f"'{path}.{policy_key}' value '{policy}' is not supported yet; "
                    "use 'ignore', 'queue', or 'restart'"
                )
            if normalized_policy not in {"ignore", "queue", "restart"}:
                raise SchemaValidationError(
                    f"'{path}.{policy_key}' must be one of 'ignore', 'queue', or 'restart'"
                )

    # Effective enablement uses the same rule as the engine. Structure is validated for
    # every trigger; temporal activation rules apply only when the workflow is enabled.
    effective_workflow_enabled = is_workflow_enabled(config)
    if "trigger" in config:
        _validate_trigger_block(config["trigger"], "trigger", effective_workflow_enabled)
    if "trigger" in workflow:
        _validate_trigger_block(workflow["trigger"], "workflow.trigger", effective_workflow_enabled)

    if HAS_JSONSCHEMA:
        try:
            validator = jsonschema.Draft202012Validator(WINFLOW_CONFIG_SCHEMA)
            errors = sorted(validator.iter_errors(config), key=lambda error: list(error.path))
            if errors:
                first_error = errors[0]
                path = ".".join(str(part) for part in first_error.path) or "root"
                raise SchemaValidationError(
                    f"JSON Schema error at '{path}': {first_error.message}"
                )
        except jsonschema.exceptions.SchemaError as exc:
            raise SchemaValidationError(f"Schema definition error: {exc.message}") from exc

    return True
