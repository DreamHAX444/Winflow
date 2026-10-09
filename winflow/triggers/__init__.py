"""WinFlow Triggers Module.

Provides event trigger abstractions, notification event models, matchers,
deduplication, cooldown trackers, event dispatchers, and concrete triggers.
"""

from typing import TYPE_CHECKING, List, Type

from winflow.triggers.base import BaseTrigger
from winflow.triggers.cooldown import CooldownTracker
from winflow.triggers.deduplication import Deduplicator
from winflow.triggers.dispatcher import EventDispatcher
from winflow.triggers.event import NotificationEvent
from winflow.triggers.matcher import MatchResult, NotificationMatcher
from winflow.triggers.schedule import ScheduleTrigger
from winflow.triggers.windows_notification import WindowsNotificationTrigger

if TYPE_CHECKING:
    from winflow.engine.trigger_registry import TriggerRegistry

PHASE4_TRIGGERS: list[tuple[str, type[BaseTrigger]]] = [
    ("windows_notification", WindowsNotificationTrigger),
    ("one_time", ScheduleTrigger),
    ("daily", ScheduleTrigger),
    ("weekly", ScheduleTrigger),
    ("startup", ScheduleTrigger),
]


def register_desktop_triggers(registry: "TriggerRegistry") -> None:
    """Register all Phase 4 trigger implementations into the provided registry.

    Args:
        registry: Target TriggerRegistry to register triggers into.
    """
    for name, trigger_cls in PHASE4_TRIGGERS:
        registry.register(name, trigger_cls, allow_override=True)


__all__ = [
    "PHASE4_TRIGGERS",
    "BaseTrigger",
    "CooldownTracker",
    "Deduplicator",
    "EventDispatcher",
    "MatchResult",
    "NotificationEvent",
    "NotificationMatcher",
    "ScheduleTrigger",
    "WindowsNotificationTrigger",
    "register_desktop_triggers",
]
