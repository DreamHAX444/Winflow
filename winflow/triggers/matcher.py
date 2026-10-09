"""Notification matching subsystem for WinFlow.

Provides configurable pattern matching for notification application, title, and body,
supporting case sensitivity and exact/contains modes.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from winflow.core.errors import TriggerConfigurationError
from winflow.triggers.event import NotificationEvent


@dataclass
class MatchResult:
    """Result of evaluating a notification event against match rules."""

    matched: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Allow evaluating MatchResult directly as a boolean."""
        return self.matched


class FieldMatcher:
    """Matcher for an individual string field (exact, contains, or regex)."""

    def __init__(self, mode: str, value: str, case_sensitive: bool = False) -> None:
        """Initialize field matcher.

        Args:
            mode: "exact" or "contains".
            value: Target match pattern.
            case_sensitive: Whether to evaluate case-sensitively.
        """
        mode = mode.lower().strip()
        if mode not in ("exact", "contains", "regex"):
            raise TriggerConfigurationError(
                f"Invalid match mode '{mode}'. Must be 'exact', 'contains', or 'regex'."
            )
        self.mode = mode
        self.value = value
        self.case_sensitive = case_sensitive
        try:
            self._pattern = re.compile(value, 0 if case_sensitive else re.IGNORECASE) if mode == "regex" else None
        except re.error as exc:
            raise TriggerConfigurationError(f"Invalid regular expression '{value}': {exc}") from exc

    def matches(self, candidate: str) -> bool:
        """Check if candidate string matches this field rule."""
        if candidate is None:
            candidate = ""

        target_val = self.value
        cand_val = candidate

        if not self.case_sensitive:
            target_val = target_val.lower()
            cand_val = cand_val.lower()

        if self.mode == "exact":
            return cand_val == target_val
        if self.mode == "contains":
            return target_val in cand_val
        return bool(self._pattern and self._pattern.search(candidate))


NOTIFICATION_CRITERIA_FIELDS: tuple[str, ...] = ("application", "title", "body")
NOTIFICATION_MATCH_OPTION_KEYS: frozenset[str] = frozenset({"case_sensitive", "match_any"})
_FIELD_OPTION_KEYS: frozenset[str] = frozenset({"mode", "value", "case_sensitive"})
_NOTIFICATION_TRIGGER_CRITERIA_KEYS: frozenset[str] = frozenset(
    {*NOTIFICATION_CRITERIA_FIELDS, "match", "match_any"}
)
# Legacy flat keys (e.g. app_name, title_contains) were never supported; they were
# silently ignored, which made the trigger match everything. Reject them instead.
_LEGACY_NOTIFICATION_KEY_PATTERN = re.compile(
    r"^(app|application|title|body)_[a-z_]+$|^app$|_(contains|regex|exact|equals)$",
    re.IGNORECASE,
)


def _is_legacy_criteria_key(key: str) -> bool:
    return bool(_LEGACY_NOTIFICATION_KEY_PATTERN.search(key))


def build_notification_match_config(trigger_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Build the NotificationMatcher config from a notification trigger configuration.

    Criteria may be given either in a ``match`` object (canonical) or as top-level
    ``application``/``title``/``body`` fields (V1 form, including ``{"contains": ...}``).
    Using both forms at once is rejected. ``match_any: true`` may be given at the
    trigger top level or inside ``match``; disagreeing values are rejected.

    Raises:
        TriggerConfigurationError: On unsupported legacy keys, conflicting forms, or
            malformed values. Criteria are never silently dropped.
    """
    cfg = trigger_cfg or {}
    if "case_sensitive" in cfg:
        # Never read by the runtime in any released form; accepting it would give a
        # false sense of control. Case sensitivity is configured inside "match".
        raise TriggerConfigurationError(
            "Top-level 'case_sensitive' is not a supported notification option. "
            "Set case sensitivity inside 'match', for example: "
            "\"match\": {\"title\": \"Done\", \"case_sensitive\": true}."
        )
    for key in cfg:
        if _is_legacy_criteria_key(str(key)):
            raise TriggerConfigurationError(
                f"Unsupported notification matching key '{key}'. Use a 'match' object instead, "
                "for example: \"match\": {\"application\": \"DemoApp\", \"title\": \"Start Demo\"}."
            )
    nested = cfg.get("config")
    if isinstance(nested, dict):
        for key in nested:
            if (
                key in _NOTIFICATION_TRIGGER_CRITERIA_KEYS
                or key == "case_sensitive"
                or _is_legacy_criteria_key(str(key))
            ):
                raise TriggerConfigurationError(
                    f"Notification matching key '{key}' inside 'config' is not read. "
                    "Move matching criteria to the trigger's top level or into 'match'."
                )

    top_level = {
        name: cfg[name] for name in NOTIFICATION_CRITERIA_FIELDS if cfg.get(name) is not None
    }
    match_block = cfg.get("match")
    if match_block is not None:
        if not isinstance(match_block, dict):
            raise TriggerConfigurationError("Notification 'match' must be an object.")
        if top_level:
            raise TriggerConfigurationError(
                "Notification criteria are given both in 'match' and at the trigger's top level "
                f"({', '.join(sorted(top_level))}). Use only one form."
            )
        match_cfg = dict(match_block)
    else:
        match_cfg = {}
        for name, criterion in top_level.items():
            if isinstance(criterion, dict) and "contains" in criterion:
                match_cfg[name] = {"mode": "contains", "value": criterion["contains"]}
            else:
                match_cfg[name] = criterion

    if cfg.get("match_any") is not None:
        top_any = cfg["match_any"]
        if not isinstance(top_any, bool):
            raise TriggerConfigurationError("Notification 'match_any' must be true or false.")
        inner_any = match_cfg.get("match_any")
        if inner_any is not None and inner_any != top_any:
            raise TriggerConfigurationError(
                "Notification 'match_any' is set to conflicting values at the top level and in 'match'."
            )
        match_cfg["match_any"] = top_any
    return match_cfg


def validate_notification_trigger_config(trigger_cfg: dict[str, Any] | None) -> "NotificationMatcher":
    """Validate a notification trigger configuration and return its matcher.

    This is the shared entry point used by configuration validation and by
    WindowsNotificationTrigger, so both enforce identical rules.
    """
    return NotificationMatcher(config=build_notification_match_config(trigger_cfg))


class NotificationMatcher:
    """Evaluates NotificationEvent instances against configured criteria."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize notification matcher.

        Args:
            config: Matching criteria dictionary (``application``/``title``/``body``,
                ``case_sensitive``, and ``match_any``).

        Raises:
            TriggerConfigurationError: If configuration is invalid, contains unknown keys,
                or has no effective criteria while ``match_any`` is not explicitly true.
        """
        self.config = config or {}
        unknown = sorted(
            str(key)
            for key in self.config
            if key not in NOTIFICATION_CRITERIA_FIELDS and key not in NOTIFICATION_MATCH_OPTION_KEYS
        )
        if unknown:
            raise TriggerConfigurationError(
                f"Unsupported notification match key(s): {', '.join(unknown)}. "
                f"Supported keys: {', '.join(NOTIFICATION_CRITERIA_FIELDS)}, case_sensitive, match_any."
            )
        case_sensitive = self.config.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise TriggerConfigurationError("Notification 'case_sensitive' must be true or false.")
        self.case_sensitive: bool = case_sensitive
        match_any = self.config.get("match_any", False)
        if not isinstance(match_any, bool):
            raise TriggerConfigurationError("Notification 'match_any' must be true or false.")
        self.match_any: bool = match_any
        self.app_matcher: FieldMatcher | None = self._parse_field("application")
        self.title_matcher: FieldMatcher | None = self._parse_field("title")
        self.body_matcher: FieldMatcher | None = self._parse_field("body")
        has_criteria = any(
            matcher is not None
            for matcher in (self.app_matcher, self.title_matcher, self.body_matcher)
        )
        if self.match_any and has_criteria:
            raise TriggerConfigurationError(
                "Notification 'match_any: true' cannot be combined with application/title/body criteria."
            )
        if not self.match_any and not has_criteria:
            raise TriggerConfigurationError(
                "Notification trigger has no matching criteria, so it would match every notification. "
                "Add 'match' with application, title, or body, or set 'match_any': true to intentionally match all."
            )

    def _parse_field(self, field_name: str) -> FieldMatcher | None:
        """Parse configuration for a specific field."""
        if field_name not in self.config:
            return None

        val = self.config[field_name]
        if val is None:
            return None

        if isinstance(val, str):
            if not val.strip():
                raise TriggerConfigurationError(
                    f"Matcher field '{field_name}' has an empty value; remove the field or provide text to match."
                )
            # Shorthand: string value implies "contains" mode
            return FieldMatcher(
                mode="contains",
                value=val,
                case_sensitive=self.case_sensitive,
            )

        if isinstance(val, dict):
            unknown = sorted(str(key) for key in val if key not in _FIELD_OPTION_KEYS)
            if unknown:
                raise TriggerConfigurationError(
                    f"Matcher field '{field_name}' has unsupported key(s): {', '.join(unknown)}."
                )
            mode = str(val.get("mode", "contains"))
            if "value" not in val:
                raise TriggerConfigurationError(
                    f"Matcher field '{field_name}' dictionary missing required 'value'."
                )
            if val["value"] is None or not str(val["value"]).strip():
                raise TriggerConfigurationError(
                    f"Matcher field '{field_name}' has an empty value; remove the field or provide text to match."
                )
            value = str(val["value"])
            # Allow field-level override of case_sensitive
            case_sens = val.get("case_sensitive", self.case_sensitive)
            if not isinstance(case_sens, bool):
                raise TriggerConfigurationError(
                    f"Matcher field '{field_name}' case_sensitive must be true or false."
                )
            return FieldMatcher(mode=mode, value=value, case_sensitive=case_sens)

        raise TriggerConfigurationError(
            f"Matcher field '{field_name}' must be a string or dictionary, got {type(val).__name__}."
        )

    def matches(self, event: NotificationEvent) -> MatchResult:
        """Evaluate a notification event against all configured criteria.

        Args:
            event: The incoming NotificationEvent.

        Returns:
            MatchResult indicating whether all configured filters matched.
        """
        if self.match_any:
            return MatchResult(
                matched=True,
                reason="match_any is enabled; all notifications match.",
                details={"event_id": event.event_id},
            )

        # 1. Match application (checks application_name and application_id)
        if self.app_matcher is not None:
            name_matched = self.app_matcher.matches(event.application_name)
            id_matched = self.app_matcher.matches(event.application_id)
            if not (name_matched or id_matched):
                return MatchResult(
                    matched=False,
                    reason=f"Application '{event.application_id}' does not match rule "
                    f"[{self.app_matcher.mode}='{self.app_matcher.value}']",
                    details={"field": "application", "actual": event.application_id},
                )

        # 2. Match title
        if self.title_matcher is not None:
            if not self.title_matcher.matches(event.title):
                return MatchResult(
                    matched=False,
                    reason=f"Title '{event.title}' does not match rule "
                    f"[{self.title_matcher.mode}='{self.title_matcher.value}']",
                    details={"field": "title", "actual": event.title},
                )

        # 3. Match body
        if self.body_matcher is not None:
            if not self.body_matcher.matches(event.body):
                return MatchResult(
                    matched=False,
                    reason=f"Body does not match rule "
                    f"[{self.body_matcher.mode}='{self.body_matcher.value}']",
                    details={"field": "body"},
                )

        return MatchResult(
            matched=True,
            reason="All configured criteria matched successfully.",
            details={"event_id": event.event_id},
        )
