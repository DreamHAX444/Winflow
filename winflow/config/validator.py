"""Configuration validator for WinFlow workflows.

Validates the structure, required fields, and types of workflow definitions
to detect malformed configurations early before execution.
"""

from typing import Any

from winflow.config.schema import SUPPORTED_SCHEMA_VERSIONS, WINFLOW_CONFIG_SCHEMA
from winflow.core.errors import SchemaValidationError

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def validate_config(config: Any) -> bool:
    """Validate a parsed configuration structure against WinFlow requirements.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        True if the configuration is valid.

    Raises:
        SchemaValidationError: If configuration fails validation.
    """
    if not isinstance(config, dict):
        raise SchemaValidationError(
            f"Configuration root must be a dictionary/object, got {type(config).__name__}"
        )

    # 1. Validate schema_version
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

    # 2. Validate workflow metadata
    if "workflow" not in config:
        raise SchemaValidationError("Configuration missing required field: 'workflow'")
    workflow = config["workflow"]
    if not isinstance(workflow, dict):
        raise SchemaValidationError(
            f"'workflow' must be a dictionary, got {type(workflow).__name__}"
        )
    if "id" not in workflow or not isinstance(workflow["id"], str) or not workflow["id"].strip():
        raise SchemaValidationError("'workflow' must contain a non-empty string 'id'")

    # 3. Validate steps (V2 nests under workflow, V1 at root)
    if "steps" in workflow:
        steps = workflow["steps"]
    elif "steps" in config:
        steps = config["steps"]
    else:
        raise SchemaValidationError("Configuration missing required field: 'steps' (expected under 'workflow' or at root)")
    if not isinstance(steps, list):
        raise SchemaValidationError(f"'steps' must be a list, got {type(steps).__name__}")
    if len(steps) == 0:
        raise SchemaValidationError("'steps' list must contain at least one step")

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise SchemaValidationError(f"Step at index {idx} must be a dictionary")
        if "action" not in step or not isinstance(step["action"], str) or not step["action"].strip():
            raise SchemaValidationError(
                f"Step at index {idx} missing required non-empty string 'action'"
            )
        if "verify" in step and not isinstance(step["verify"], dict):
            raise SchemaValidationError(f"Step at index {idx} 'verify' must be a dictionary")
        if "failure" in step and not isinstance(step["failure"], dict):
            raise SchemaValidationError(f"Step at index {idx} 'failure' must be a dictionary")

    # 4. Recursively validate control flow depth
    max_depth = config.get("settings", {}).get("max_control_depth", 10)
    
    def _check_depth(steps_list: list, current_depth: int) -> None:
        if current_depth > max_depth:
            raise SchemaValidationError(f"Configuration exceeds maximum control depth of {max_depth}")
            
        for step in steps_list:
            action = step.get("action")
            if action == "if":
                if "then" in step:
                    _check_depth(step["then"], current_depth + 1)
                if "else" in step:
                    _check_depth(step["else"], current_depth + 1)
            elif action == "while" or action == "for_each":
                child_steps = step.get("do", step.get("steps", []))
                if not isinstance(child_steps, list):
                    raise SchemaValidationError(f"Control-flow step at depth {current_depth} must contain a list of child steps")
                _check_depth(child_steps, current_depth + 1)

    _check_depth(steps, 0)

    # 5. Validate settings (optional)
    if "settings" in config:
        if not isinstance(config["settings"], dict):
            raise SchemaValidationError(
                f"'settings' must be a dictionary, got {type(config['settings']).__name__}"
            )

    # 5. Validate trigger (optional, allowed at root or under workflow)
    def _validate_trigger_block(trig: Any, path: str) -> None:
        if not isinstance(trig, dict):
            raise SchemaValidationError(f"'{path}' must be a dictionary, got {type(trig).__name__}")
        if "type" in trig and (not isinstance(trig["type"], str) or not trig["type"].strip()):
            raise SchemaValidationError(f"'{path}.type' must be a non-empty string")
        if "match" in trig and not isinstance(trig["match"], dict):
            raise SchemaValidationError(f"'{path}.match' must be a dictionary")
        if "deduplication" in trig and not isinstance(trig["deduplication"], (bool, dict)):
            raise SchemaValidationError(f"'{path}.deduplication' must be a boolean or dictionary")
        if "cooldown_seconds" in trig:
            cooldown = trig["cooldown_seconds"]
            if not isinstance(cooldown, (int, float)) or cooldown < 0:
                raise SchemaValidationError(f"'{path}.cooldown_seconds' must be a non-negative number")
        if "while_running" in trig:
            policy = str(trig["while_running"]).lower()
            if policy not in ("ignore", "queue", "restart"):
                raise SchemaValidationError(
                    f"'{path}.while_running' must be one of 'ignore', 'queue', 'restart'"
                )

    if "trigger" in config:
        _validate_trigger_block(config["trigger"], "trigger")

    if "trigger" in workflow:
        _validate_trigger_block(workflow["trigger"], "workflow.trigger")

    # 6. Validate loop (optional)
    if "loop" in config:
        loop = config["loop"]
        if not isinstance(loop, dict):
            raise SchemaValidationError(
                f"'loop' must be a dictionary, got {type(loop).__name__}"
            )
        if "count" in loop:
            count = loop["count"]
            if isinstance(count, int):
                if count < 1:
                    raise SchemaValidationError(f"'loop.count' must be >= 1, got {count}")
            elif isinstance(count, str):
                if count.lower() != "infinite":
                    raise SchemaValidationError(
                        f"String 'loop.count' must be 'infinite', got '{count}'"
                    )
            else:
                raise SchemaValidationError(
                    f"'loop.count' must be an integer or 'infinite', got {type(count).__name__}"
                )

    # 7. JSON Schema cross-validation if jsonschema library is installed
    if HAS_JSONSCHEMA:
        try:
            validator = jsonschema.Draft202012Validator(WINFLOW_CONFIG_SCHEMA)
            errors = sorted(validator.iter_errors(config), key=lambda e: e.path)
            if errors:
                first_err = errors[0]
                path = ".".join(str(p) for p in first_err.path) or "root"
                raise SchemaValidationError(
                    f"JSON Schema error at '{path}': {first_err.message}"
                )
        except jsonschema.exceptions.SchemaError as exc:
            # Internal schema definition error fallback
            raise SchemaValidationError(f"Schema definition error: {exc.message}")

    return True
