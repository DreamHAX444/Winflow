"""Event dispatcher subsystem for WinFlow triggers.

Provides non-blocking, thread-safe distribution of notification events to registered
trigger handlers and workflow listeners with strict exception isolation.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from winflow.core.logger import get_logger
from winflow.triggers.event import NotificationEvent


class EventDispatcher:
    """Thread-safe event dispatcher that decouples event listening from execution."""

    def __init__(
        self,
        max_workers: int = 4,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize event dispatcher.

        Args:
            max_workers: Maximum worker threads for asynchronous handler dispatch.
            logger: Custom logger.
        """
        self.logger = logger or get_logger()
        self._handlers: dict[str, Callable[[NotificationEvent], None]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="WinFlowEventDispatcher",
        )
        self._is_stopped = False

    def register_handler(
        self,
        handler: Callable[[NotificationEvent], None],
        handler_id: str | None = None,
    ) -> str:
        """Register a handler callback for incoming events.

        Args:
            handler: Callable accepting NotificationEvent.
            handler_id: Optional unique identifier for the handler.

        Returns:
            The handler_id assigned.
        """
        hid = handler_id or f"handler_{uuid.uuid4().hex[:8]}"
        with self._lock:
            if self._is_stopped:
                self.logger.warning("Attempted to register handler on stopped dispatcher.")
                return hid
            self._handlers[hid] = handler
            self.logger.debug("Registered trigger handler '%s'.", hid)
        return hid

    def unregister_handler(self, handler_id: str) -> None:
        """Remove a registered handler callback.

        Args:
            handler_id: Identifier returned during registration.
        """
        with self._lock:
            if handler_id in self._handlers:
                del self._handlers[handler_id]
                self.logger.debug("Unregistered trigger handler '%s'.", handler_id)

    def dispatch(self, event: NotificationEvent, asynchronous: bool = True) -> None:
        """Dispatch a notification event to all registered handlers.

        Args:
            event: The incoming NotificationEvent.
            asynchronous: If True, executes handlers in thread pool without blocking caller.
        """
        with self._lock:
            if self._is_stopped:
                self.logger.warning("Event dropped: dispatcher is stopped.")
                return
            handlers_snapshot = list(self._handlers.items())

        for hid, handler in handlers_snapshot:
            if asynchronous:
                try:
                    self._executor.submit(self._safe_invoke, hid, handler, event)
                except RuntimeError as exc:
                    self.logger.warning("Could not submit event to dispatcher pool: %s", exc)
            else:
                self._safe_invoke(hid, handler, event)

    def _safe_invoke(
        self,
        handler_id: str,
        handler: Callable[[NotificationEvent], None],
        event: NotificationEvent,
    ) -> None:
        """Execute a handler with complete exception isolation."""
        try:
            handler(event)
        except Exception as exc:
            self.logger.error(
                "Exception in trigger handler '%s' processing event '%s': %s",
                handler_id,
                event.event_id,
                exc,
                exc_info=True,
            )

    def stop(self, timeout: float = 3.0) -> None:
        """Cleanly shut down the dispatcher and wait for active handlers to complete."""
        with self._lock:
            if self._is_stopped:
                return
            self._is_stopped = True
            self._handlers.clear()

        self._executor.shutdown(wait=True, cancel_futures=True)
        self.logger.debug("EventDispatcher stopped cleanly.")
