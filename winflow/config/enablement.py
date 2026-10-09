"""Effective workflow enablement, shared by configuration validation and the engine.

A workflow is enabled only when both ``workflow.enabled`` and the legacy
``settings.enabled`` are true. Both default to true when absent. Validation and the
runtime engine must use this single rule so they cannot disagree.
"""

from dataclasses import dataclass
from typing import Any

from winflow.core.errors import ConfigurationError


def is_workflow_enabled(workflow_config: Any) -> bool:
    """Return the effective enablement of a workflow configuration.

    Raises:
        ConfigurationError: If ``workflow``/``settings`` are not objects, or either
            ``enabled`` flag is present but not a boolean.
    """
    workflow = workflow_config.get("workflow", {}) if isinstance(workflow_config, dict) else {}
    settings = workflow_config.get("settings", {}) if isinstance(workflow_config, dict) else {}
    if not isinstance(workflow, dict) or not isinstance(settings, dict):
        raise ConfigurationError("'workflow' and 'settings' must be objects.")
    for path, block in (("workflow.enabled", workflow), ("settings.enabled", settings)):
        if "enabled" in block and not isinstance(block["enabled"], bool):
            raise ConfigurationError(f"'{path}' must be a boolean.")
    return bool(workflow.get("enabled", True) and settings.get("enabled", True))


__all__ = ["is_workflow_enabled"]



@dataclass(frozen=True)
class EnablementStatus:
    """What the stored flags say about a workflow, in user-facing terms.

    ``configured`` is the value of ``workflow.enabled`` alone. ``eligible`` is the
    shared effective rule (``workflow.enabled`` AND ``settings.enabled``). Neither
    value says whether the workflow is currently running.
    """

    configured: bool
    eligible: bool
    reason: str


def describe_enablement(config: Any) -> EnablementStatus:
    """Summarize the enablement flags of a configuration for display."""
    workflow = config.get("workflow", {}) if isinstance(config, dict) else {}
    settings = config.get("settings", {}) if isinstance(config, dict) else {}
    configured = bool(workflow.get("enabled", True)) if isinstance(workflow, dict) else True
    settings_on = bool(settings.get("enabled", True)) if isinstance(settings, dict) else True
    eligible = is_workflow_enabled(config)
    if eligible:
        reason = "Enabled: configured to run."
    elif not configured:
        reason = "Off: the workflow is switched off (workflow.enabled is false)."
    else:
        reason = (
            "Off in settings: settings.enabled is false, so the workflow will not run "
            "even though it is switched on."
        )
    if eligible is False and not settings_on and not configured:
        reason = (
            "Off: the workflow is switched off, and settings.enabled is also false."
        )
    return EnablementStatus(configured=configured, eligible=eligible, reason=reason)
