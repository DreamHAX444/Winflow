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


class NotificationMatcher:
    """Evaluates NotificationEvent instances against configured criteria."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize notification matcher.

        Args:
            config: Matching criteria dictionary.

        Raises:
            TriggerConfigurationError: If configuration is invalid.
        """
        self.config = config or {}
        self.case_sensitive: bool = bool(self.config.get("case_sensitive", False))
        self.app_matcher: FieldMatcher | None = self._parse_field("application")
        self.title_matcher: FieldMatcher | None = self._parse_field("title")
        self.body_matcher: FieldMatcher | None = self._parse_field("body")

    def _parse_field(self, field_name: str) -> FieldMatcher | None:
        """Parse configuration for a specific field."""
        if field_name not in self.config:
            return None

        val = self.config[field_name]
        if val is None:
            return None

        if isinstance(val, str):
            # Shorthand: string value implies "contains" mode
            return FieldMatcher(
                mode="contains",
                value=val,
                case_sensitive=self.case_sensitive,
            )

        if isinstance(val, dict):
            mode = str(val.get("mode", "contains"))
            if "value" not in val:
                raise TriggerConfigurationError(
                    f"Matcher field '{field_name}' dictionary missing required 'value'."
                )
            value = str(val.get("value", ""))
            # Allow field-level override of case_sensitive
            case_sens = bool(val.get("case_sensitive", self.case_sensitive))
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
