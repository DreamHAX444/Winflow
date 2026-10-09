"""Windows Notification Database event source for WinFlow.

Listens for Windows 10/11 toast notifications by observing new records in the
Windows Notification Platform SQLite database (%LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db).
Zero third-party dependencies; uses Python's built-in sqlite3 and xml.etree.
"""

import logging
import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
from winflow.core.errors import (
    UnsupportedEnvironmentError,
)
from winflow.core.logger import get_logger
from winflow.triggers.event import NotificationEvent
from winflow.triggers.sources.base import BaseNotificationSource


class WindowsDatabaseNotificationSource(BaseNotificationSource):
    """Event source that reads Windows toast notifications from wpndatabase.db."""

    DEFAULT_DB_PATH = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db"
    )
    FILETIME_EPOCH_DIFF = 116444736000000000
    FILETIME_TICKS_PER_SEC = 10000000.0

    def __init__(
        self,
        db_path: str | None = None,
        poll_interval: float = 0.25,
        start_from_current: bool = True,
        emergency_stop: EmergencyStop | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the Windows database notification source.

        Args:
            db_path: Path to wpndatabase.db. Defaults to standard user appdata location.
            poll_interval: Polling frequency in seconds (default: 0.25s).
            start_from_current: If True, skips historical notifications and only emits new ones.
            emergency_stop: EmergencyStop instance to monitor.
            logger: Custom logger.
        """
        super().__init__()
        self.db_path = str(Path(db_path or self.DEFAULT_DB_PATH).resolve())
        self.poll_interval = max(0.05, float(poll_interval))
        self.start_from_current = start_from_current
        self.emergency_stop = emergency_stop or get_emergency_stop()
        self.logger = logger or get_logger()

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_seen_id: int = 0
        self._lock = threading.Lock()

    def _convert_filetime(self, filetime: int | None) -> float:
        """Convert a 64-bit Windows FILETIME (100ns intervals since 1601) to a UNIX timestamp."""
        if not filetime or filetime <= self.FILETIME_EPOCH_DIFF:
            return time.time()
        try:
            return (filetime - self.FILETIME_EPOCH_DIFF) / self.FILETIME_TICKS_PER_SEC
        except Exception:
            return time.time()

    def _parse_payload(self, raw_payload: Any) -> tuple[str, str]:
        """Extract title and body strings from a toast notification XML payload.

        Args:
            raw_payload: Binary blob, string, or None.

        Returns:
            Tuple of (title, body).
        """
        if not raw_payload:
            return "", ""

        xml_str = ""
        if isinstance(raw_payload, bytes):
            xml_str = raw_payload.decode("utf-8", errors="replace")
        elif isinstance(raw_payload, str):
            xml_str = raw_payload
        else:
            return "", ""

        try:
            root = ET.fromstring(xml_str)
            text_elements = [
                elem.text.strip()
                for elem in root.iter("text")
                if elem.text and elem.text.strip()
            ]
            if not text_elements:
                return "", ""

            title = text_elements[0]
            body = "\n".join(text_elements[1:]) if len(text_elements) > 1 else ""
            return title, body
        except ET.ParseError:
            self.logger.debug("Failed to parse toast payload XML.")
            return "", ""

    def _get_max_id(self) -> int:
        """Query current maximum notification ID from database."""
        if not os.path.exists(self.db_path):
            return 0
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=1.0,
            )
            try:
                cur = conn.cursor()
                cur.execute("SELECT MAX(Id) FROM Notification;")
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.logger.warning("Could not read initial max notification ID: %s", exc)
            return 0

    def start(self, on_event: Callable[[NotificationEvent], None]) -> None:
        """Start the background notification listener thread.

        Args:
            on_event: Function called with each detected NotificationEvent.

        Raises:
            UnsupportedEnvironmentError: If the notification database file does not exist.
            ListenerInitializationError: If the listener is already running or cannot start.
        """
        with self._lock:
            if self._is_running:
                self.logger.warning("Notification listener is already running.")
                return

            if not os.path.exists(self.db_path):
                raise UnsupportedEnvironmentError(
                    f"Windows notification database not found at '{self.db_path}'. "
                    "Ensure you are running on Windows 10/11 with notifications enabled."
                )

            self._callback = on_event
            self._stop_event.clear()

            if self.start_from_current:
                self._last_seen_id = self._get_max_id()
                self.logger.debug(
                    "Notification listener initialized from current max ID: %d",
                    self._last_seen_id,
                )
            else:
                self._last_seen_id = 0

            self._is_running = True
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="WinFlowNotificationDBListener",
                daemon=True,
            )
            self._thread.start()
            self.logger.info("Notification listener started (source: wpndatabase.db).")

    def stop(self) -> None:
        """Stop the background listener thread cleanly and release resources."""
        with self._lock:
            if not self._is_running:
                return

            self._is_running = False
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None

        self._callback = None
        self.logger.info("Notification listener stopped.")

    def _poll_loop(self) -> None:
        """Background polling loop reading new records from wpndatabase.db."""
        while not self._stop_event.is_set():
            if self.emergency_stop.is_triggered():
                self.logger.info("Notification listener detected emergency stop. Stopping loop.")
                break

            try:
                self._check_for_new_notifications()
            except sqlite3.OperationalError as exc:
                err_str = str(exc).lower()
                if "locked" in err_str or "busy" in err_str:
                    self.logger.debug("Transient notification DB lock: %s", exc)
                else:
                    self.logger.error("Database operational error in notification listener: %s", exc)
            except Exception as exc:
                self.logger.error("Unexpected error in notification poll loop: %s", exc)

            # Sliced wait responding immediately to stop_event
            self._stop_event.wait(timeout=self.poll_interval)

    def _check_for_new_notifications(self) -> None:
        """Execute a read query to check for notifications newer than _last_seen_id."""
        if not os.path.exists(self.db_path):
            return

        conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT n.Id, n.ArrivalTime, n.Type, h.PrimaryId, n.Payload, n.Tag, n.[Group]
                FROM Notification n
                LEFT JOIN NotificationHandler h ON n.HandlerId = h.RecordId
                WHERE n.Id > ?
                ORDER BY n.Id ASC
                """,
                (self._last_seen_id,),
            )
            rows = cur.fetchall()
            for row in rows:
                if self._stop_event.is_set() or self.emergency_stop.is_triggered():
                    break

                nid, arrival_raw, ntype, app_id, payload, tag, group = row
                title, body = self._parse_payload(payload)
                ts = self._convert_filetime(arrival_raw)

                event = NotificationEvent(
                    event_id=str(nid),
                    application_id=str(app_id or "unknown"),
                    title=title,
                    body=body,
                    timestamp=ts,
                    source="windows_notification",
                    raw_metadata={
                        "tag": tag,
                        "group": group,
                        "arrival_raw": arrival_raw,
                        "type": ntype,
                    },
                )

                self._last_seen_id = max(self._last_seen_id, nid)

                # Dispatch event to registered callback
                cb = self._callback
                if cb is not None:
                    try:
                        cb(event)
                    except Exception as exc:
                        self.logger.error(
                            "Error in notification event handler callback: %s", exc
                        )

        finally:
            conn.close()
