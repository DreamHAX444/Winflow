"""Desktop automation execution lock for WinFlow.

Provides a thread-safe global mutual exclusion mechanism ensuring that only one
workflow or desktop automation routine can control the physical desktop
(mouse, keyboard, window focus) at any given time.
"""

import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager

from winflow.core.errors import DesktopLockError
from winflow.core.logger import get_logger


class DesktopExecutionLock:
    """Thread-safe global execution lock guarding physical desktop interaction."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the desktop execution lock."""
        self._lock = threading.RLock()
        self._owner: str | None = None
        self._acquired_at: float | None = None
        self._acquisition_count: int = 0
        self.logger = logger or get_logger()

    @property
    def is_locked(self) -> bool:
        """Return True if the desktop lock is currently held."""
        with self._lock:
            return self._owner is not None

    @property
    def current_owner(self) -> str | None:
        """Return the identifier of the current lock holder, or None."""
        with self._lock:
            return self._owner

    @property
    def acquired_at(self) -> float | None:
        """Return timestamp when the lock was acquired, or None."""
        with self._lock:
            return self._acquired_at

    def acquire(self, owner: str, timeout: float | None = 10.0) -> bool:
        """Acquire the desktop lock for a given owner.

        Args:
            owner: Identifier for the entity acquiring the lock (e.g. execution_id).
            timeout: Maximum seconds to wait for acquisition. None means wait indefinitely.

        Returns:
            True if acquired successfully.

        Raises:
            DesktopLockError: If acquisition times out or fails.
        """
        blocking = timeout is None or timeout > 0.0
        wait_time = -1 if timeout is None else timeout

        acquired = self._lock.acquire(blocking=blocking, timeout=wait_time if blocking else -1)
        if not acquired:
            raise DesktopLockError(
                f"Failed to acquire desktop lock for owner '{owner}' within {timeout}s. "
                f"Currently held by '{self._owner}'."
            )

        if self._owner is not None and self._owner != owner:
            # Reentrant lock acquired by same thread under different owner name
            self.logger.warning(
                "Desktop lock held by '%s' being re-entered under owner '%s'.",
                self._owner,
                owner,
            )

        self._owner = owner
        self._acquired_at = time.time()
        self._acquisition_count += 1
        self.logger.debug("Desktop lock acquired by '%s'.", owner)
        return True

    def release(self, owner: str | None = None) -> None:
        """Release the desktop lock.

        Args:
            owner: Optional owner identifier to verify ownership.
        """
        if self._owner is None:
            self.logger.warning("Attempted to release desktop lock that is not held.")
            return

        if owner is not None and self._owner != owner:
            raise DesktopLockError(
                "Desktop lock release denied for owner '%s'; currently owned by '%s'."
                % (owner, self._owner)
            )

        self._acquisition_count -= 1
        released_owner = self._owner
        if self._acquisition_count <= 0:
            self._owner = None
            self._acquired_at = None
            self._acquisition_count = 0

        self._lock.release()
        self.logger.debug("Desktop lock released by '%s'.", released_owner)

    def force_release(self) -> None:
        """Forcefully reset and release the desktop lock in emergency or shutdown scenarios."""
        try:
            while self._acquisition_count > 0:
                self._acquisition_count -= 1
                self._lock.release()
        except RuntimeError:
            pass
        finally:
            self._owner = None
            self._acquired_at = None
            self._acquisition_count = 0
            self.logger.info("Desktop lock forcefully released.")

    @contextmanager
    def hold(
        self,
        owner: str,
        timeout: float | None = 10.0,
    ) -> Generator["DesktopExecutionLock", None, None]:
        """Context manager to reliably acquire and release the desktop lock.

        Args:
            owner: Identifier for the entity acquiring the lock.
            timeout: Acquisition timeout in seconds.
        """
        self.acquire(owner=owner, timeout=timeout)
        try:
            yield self
        finally:
            self.release(owner=owner)


_GLOBAL_DESKTOP_LOCK: DesktopExecutionLock | None = None
_GLOBAL_LOCK_MUTEX = threading.Lock()


def get_desktop_lock() -> DesktopExecutionLock:
    """Retrieve the global desktop execution lock singleton."""
    global _GLOBAL_DESKTOP_LOCK
    with _GLOBAL_LOCK_MUTEX:
        if _GLOBAL_DESKTOP_LOCK is None:
            _GLOBAL_DESKTOP_LOCK = DesktopExecutionLock()
        return _GLOBAL_DESKTOP_LOCK


def reset_desktop_lock() -> None:
    """Reset the global desktop execution lock (primarily for testing)."""
    global _GLOBAL_DESKTOP_LOCK
    with _GLOBAL_LOCK_MUTEX:
        if _GLOBAL_DESKTOP_LOCK is not None:
            _GLOBAL_DESKTOP_LOCK.force_release()
        _GLOBAL_DESKTOP_LOCK = None
