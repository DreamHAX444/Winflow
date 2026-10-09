"""Configuration schema and data definitions for WinFlow.

Defines supported schema versions and basic JSON Schema constraints.
"""

from typing import Any

SCHEMA_VERSION: str = "1.0"
SUPPORTED_SCHEMA_VERSIONS = {SCHEMA_VERSION}

# Shared schedule/trigger field shapes. Runtime validation (validator.py,
# triggers/schedule.py, triggers/matcher.py) is authoritative; these patterns are
# intentionally no stricter than the runtime, which trims whitespace and treats
# blank values as "not entered".
_TIME_OF_DAY_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "pattern": r"^\s*(\d{1,2}:\d{2})?\s*$"},
        {"type": "integer", "minimum": 0, "maximum": 23, "description": "Legacy integer hour; 14 means 14:00."},
    ],
}
_WEEKDAY_ITEM_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string"},
        {"type": "integer", "minimum": 0, "maximum": 6},
    ],
}
_WEEKDAYS_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string"},
        {"type": "integer", "minimum": 0, "maximum": 6},
        {"type": "array", "items": _WEEKDAY_ITEM_SCHEMA},
    ],
}
_DATE_SCHEMA: dict[str, Any] = {"type": "string", "pattern": r"^\s*(\d{4}-\d{2}-\d{2})?\s*$"}
_DATETIME_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?)?\s*$",
}
_SCHEDULE_FIELDS_SCHEMA: dict[str, Any] = {
    "time": _TIME_OF_DAY_SCHEMA,
    "at": _TIME_OF_DAY_SCHEMA,
    "date": _DATE_SCHEMA,
    "day": _DATE_SCHEMA,
    "datetime": _DATETIME_SCHEMA,
    "date_time": _DATETIME_SCHEMA,
    "days": _WEEKDAYS_SCHEMA,
    "weekdays": _WEEKDAYS_SCHEMA,
}

# Used for both root-level "trigger" and "workflow.trigger" so both forms receive
# identical structural checks.
TRIGGER_BLOCK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "minLength": 1},
        "enabled": {"type": "boolean"},
        "config": {
            "type": "object",
            "description": "Legacy nested schedule fields; equivalent to the top-level fields.",
            "properties": dict(_SCHEDULE_FIELDS_SCHEMA),
            "additionalProperties": True,
        },
        **_SCHEDULE_FIELDS_SCHEMA,
        "match": {"type": "object"},
        "match_any": {
            "type": "boolean",
            "description": "Explicit opt-in to match every notification. Required when no match criteria are given.",
        },
        "case_sensitive": {
            "not": {},
            "description": "Not supported at the top level; set match.case_sensitive instead.",
        },
        "deduplication": {"oneOf": [{"type": "boolean"}, {"type": "object"}]},
        "cooldown_seconds": {"type": "number", "minimum": 0},
        "while_running": {
            "type": "string",
            "enum": ["ignore", "queue", "restart"],
        },
        "while_running_policy": {
            "type": "string",
            "enum": ["ignore", "queue", "restart"],
        },
    },
    "additionalProperties": True,
}

# JSON Schema definition for validation
WINFLOW_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "WinFlowConfiguration",
    "type": "object",
    "required": ["schema_version", "workflow"],
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": list(SUPPORTED_SCHEMA_VERSIONS),
        },
        "workflow": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
                "loop": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "minimum": 1},
                        "delay_between_seconds": {"type": "number", "minimum": 0},
                    },
                    "additionalProperties": True,
                },
                "trigger": TRIGGER_BLOCK_SCHEMA,
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object", "required": ["action"], "additionalProperties": True},
                },
            },
            "additionalProperties": True,
        },
        "settings": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Default per-action timeout; takes precedence over global_timeout_seconds.",
                },
                "global_timeout_seconds": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Legacy alias for timeout_seconds.",
                },
                "emergency_stop_hotkey": {"type": "string", "minLength": 1},
                "retry_attempts": {"type": "integer", "minimum": 0},
                "retry_delay_seconds": {"type": "number", "minimum": 0},
                "max_restarts": {"type": "integer", "minimum": 1},
                "log_level": {"type": "string"},
                "failure": {
                    "type": "object",
                    "properties": {
                        "policy": {
                            "type": "string",
                            "enum": ["stop", "retry", "skip", "continue", "restart_workflow"],
                        },
                        "attempts": {"type": "integer", "minimum": 1},
                        "delay_seconds": {"type": "number", "minimum": 0},
                        "max_restarts": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": True,
                },
                "use_desktop_lock": {"type": "boolean"},
                "desktop_lock_timeout": {"type": "number", "minimum": 0},
                "dry_run": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
        "debug": {
            "type": "object",
            "properties": {
                "screenshot_on_failure": {"type": "boolean"},
                "log_level": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
        "trigger": TRIGGER_BLOCK_SCHEMA,
        "loop": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1},
                "delay_between_seconds": {"type": "number", "minimum": 0},
            },
            "additionalProperties": True,
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "step_id": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                    "name": {"type": "string"},
                    "action": {"type": "string", "minLength": 1},
                    "params": {"type": "object"},
                    "verify": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "minLength": 1},
                            "target": {"type": "object"},
                            "timeout_seconds": {"type": "number", "minimum": 0},
                            "poll_interval_seconds": {"type": "number", "minimum": 0},
                        },
                        "additionalProperties": True,
                    },
                    "retry": {
                        "type": "object",
                        "properties": {
                            "attempts": {"type": "integer", "minimum": 1},
                            "delay_seconds": {"type": "number", "minimum": 0},
                        },
                        "additionalProperties": True,
                    },
                    "failure": {
                        "type": "object",
                        "properties": {
                            "policy": {
                                "type": "string",
                                "enum": ["stop", "retry", "skip", "continue", "restart_workflow"],
                            },
                            "attempts": {"type": "integer", "minimum": 1},
                            "delay_seconds": {"type": "number", "minimum": 0},
                            "max_restarts": {"type": "integer", "minimum": 1},
                        },
                        "additionalProperties": True,
                    },
                    "timeout": {"type": "number", "minimum": 0},
                    "timeout_seconds": {"type": "number", "minimum": 0},
                    "condition": {"type": "object"},
                    "then": {"type": "array"},
                    "else": {"type": "array"},
                    "do": {"type": "array"},
                    "items": {},
                    "max_iterations": {"type": "integer", "minimum": 1},
                    "max_items": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

