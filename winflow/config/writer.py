"""Validated, atomic workflow configuration writer.

Shared by the workflow editor and the Workflows page so every save goes through the
same validation and serialization path that the loader reads.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from winflow.config.loader import load_and_validate_config
from winflow.config.validator import validate_config
from winflow.core.errors import ConfigurationError

_YAML_SUFFIXES = {".yaml", ".yml"}


def serialize_config(config: dict[str, Any], path: Path) -> str:
    """Serialize ``config`` in the format implied by ``path``'s suffix."""
    if path.suffix.lower() in _YAML_SUFFIXES:
        try:
            import yaml
        except ImportError as exc:
            raise ConfigurationError(
                "Saving YAML requires PyYAML. Choose a JSON filename or install PyYAML."
            ) from exc
        return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def save_config(config: dict[str, Any], path: Path) -> None:
    """Validate ``config`` and atomically replace ``path`` with its serialization.

    The file is written to a temporary sibling and then swapped in, so a failure
    part-way through never leaves a truncated workflow file behind.

    Raises:
        ConfigurationError: If validation fails, serialization is unavailable, or
            the file cannot be written. The existing file is left unchanged.
    """
    validate_config(config)
    content = serialize_config(config, path)
    directory = path.parent
    try:
        handle, temp_name = tempfile.mkstemp(
            dir=str(directory), prefix=f".{path.name}.", suffix=".tmp"
        )
    except OSError as exc:
        raise ConfigurationError(f"Unable to write '{path}': {exc}") from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise ConfigurationError(f"Unable to write '{path}': {exc}") from exc


def set_workflow_enabled(path: Path, enabled: bool) -> dict[str, Any]:
    """Persist ``workflow.enabled`` for the workflow file at ``path``.

    Reads through the normal loader (so an invalid file is refused, not rewritten),
    changes only ``workflow.enabled``, and saves through :func:`save_config`.

    Returns:
        The saved configuration.

    Raises:
        ConfigurationError: If the file cannot be loaded, validated, or saved.
    """
    config = load_and_validate_config(path)
    workflow = config.get("workflow")
    if workflow is None:
        workflow = {}
        config["workflow"] = workflow
    if not isinstance(workflow, dict):
        raise ConfigurationError("'workflow' must be an object to change enablement.")
    workflow["enabled"] = bool(enabled)
    save_config(config, path)
    return config
