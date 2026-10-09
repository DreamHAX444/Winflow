"""Configuration schema and data definitions for WinFlow.

Defines supported schema versions and basic JSON Schema constraints.
"""

from typing import Any

SCHEMA_VERSION: str = "1.0"
SUPPORTED_SCHEMA_VERSIONS = {SCHEMA_VERSION}

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
                "trigger": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "minLength": 1},
                        "match": {"type": "object"},
                        "deduplication": {
                            "oneOf": [
                                {"type": "boolean"},
                                {"type": "object"},
                            ]
                        },
                        "cooldown_seconds": {"type": "number", "minimum": 0},
                        "while_running": {
                            "type": "string",
                            "enum": ["ignore", "queue", "restart"],
                        },
                    },
                    "additionalProperties": True,
                },
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
                "timeout_seconds": {"type": "number", "minimum": 0},
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
        "trigger": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "minLength": 1},
                "config": {"type": "object"},
                "match": {"type": "object"},
                "deduplication": {
                    "oneOf": [
                        {"type": "boolean"},
                        {"type": "object"},
                    ]
                },
                "cooldown_seconds": {"type": "number", "minimum": 0},
                "while_running": {
                    "type": "string",
                    "enum": ["ignore", "queue", "restart"],
                },
            },
            "additionalProperties": True,
        },
        "loop": {
            "type": "object",
            "properties": {
                "count": {
                    "oneOf": [
                        {"type": "integer", "minimum": 1},
                        {"type": "string", "enum": ["infinite"]},
                    ]
                },
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

