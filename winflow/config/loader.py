"""Configuration loader for WinFlow workflows.

Loads workflow specifications from JSON (and YAML if PyYAML is installed) files,
with validation support and comprehensive error handling.
"""

import json
from pathlib import Path
from typing import Any

from winflow.config.validator import validate_config
from winflow.core.errors import ConfigurationError

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_config(file_path: str | Path) -> dict[str, Any]:
    """Load and parse a workflow configuration file.

    Args:
        file_path: Path to the configuration file (.json or .yaml/.yml).

    Returns:
        Parsed configuration dictionary.

    Raises:
        ConfigurationError: If the file is missing, unreadable, or contains syntax errors.
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    if not path.is_file():
        raise ConfigurationError(f"Configuration path is not a regular file: {path}")

    suffix = path.suffix.lower()

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ConfigurationError(f"Failed to read file '{path}': {exc}") from exc

    if not content.strip():
        raise ConfigurationError(f"Configuration file is empty: {path}")

    if suffix == ".json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Invalid JSON syntax in '{path}' at line {exc.lineno}, col {exc.colno}: {exc.msg}"
            ) from exc
    elif suffix in {".yaml", ".yml"}:
        if not HAS_YAML:
            raise ConfigurationError(
                f"Cannot parse YAML file '{path}': PyYAML is not installed. "
                f"Install 'pyyaml' or use JSON format."
            )
        try:
            parsed = yaml.safe_load(content)
        except Exception as exc:
            raise ConfigurationError(f"Invalid YAML syntax in '{path}': {exc}") from exc
    else:
        # Attempt JSON decoding by default for extensionless or other extensions
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            raise ConfigurationError(
                f"Unsupported file extension '{suffix}' for '{path}'. Expected .json or .yaml"
            )

    if not isinstance(parsed, dict):
        raise ConfigurationError(
            f"Configuration in '{path}' must evaluate to a dictionary/object, got {type(parsed).__name__}"
        )

    return parsed


def load_and_validate_config(file_path: str | Path) -> dict[str, Any]:
    """Load and validate a workflow configuration file.

    Args:
        file_path: Path to the configuration file.

    Returns:
        Validated configuration dictionary.

    Raises:
        ConfigurationError: If loading fails.
        SchemaValidationError: If validation fails.
    """
    config = load_config(file_path)
    validate_config(config)
    return config
