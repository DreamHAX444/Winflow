"""Execution history persistence subsystem for WinFlow.

Provides abstract storage interface and lightweight SQLite implementation
for tracking workflow execution status and history.
"""

import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from winflow.core.errors import StorageError


@dataclass
class ExecutionRecord:
    """Represents a persisted execution history entry."""
    execution_id: str
    workflow_id: str
    start_time: float
    end_time: float | None = None
    status: str = "PENDING"
    current_step: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary representation."""
        return asdict(self)


class BaseExecutionHistory(ABC):
    """Abstract base class for persisting workflow execution runs."""

    @abstractmethod
    def record_start(
        self,
        execution_id: str,
        workflow_id: str,
        start_time: float | None = None,
    ) -> None:
        """Record the start of a workflow execution."""

    @abstractmethod
    def record_update(
        self,
        execution_id: str,
        current_step: int | None = None,
        status: str | None = None,
    ) -> None:
        """Update the progress or state of an ongoing execution."""

    @abstractmethod
    def record_complete(
        self,
        execution_id: str,
        status: str,
        end_time: float | None = None,
        error: str | None = None,
    ) -> None:
        """Record completion (or failure/cancellation) of an execution."""

    @abstractmethod
    def get_record(self, execution_id: str) -> ExecutionRecord | None:
        """Retrieve a specific execution record by execution ID."""

    @abstractmethod
    def list_records(self, limit: int = 100) -> list[ExecutionRecord]:
        """List the most recent execution records."""


class SQLiteExecutionHistory(BaseExecutionHistory):
    """Lightweight SQLite-backed execution history tracker."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Initialize SQLite storage.

        Args:
            db_path: Path to SQLite database file or ':memory:'.
        """
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        try:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_database()
        except Exception as exc:
            raise StorageError(f"Failed to initialize SQLite history database: {exc}") from exc

    def _init_database(self) -> None:
        """Initialize tables if they do not exist."""
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_history (
                    execution_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL,
                    status TEXT NOT NULL,
                    current_step INTEGER,
                    error TEXT,
                    trigger_source TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    step INTEGER,
                    action TEXT,
                    status TEXT,
                    duration_ms REAL,
                    metadata_json TEXT
                )
                """
            )
            self._conn.commit()

    def record_start(
        self,
        execution_id: str,
        workflow_id: str,
        start_time: float | None = None,
    ) -> None:
        """Record the start of a workflow execution run."""
        started = start_time if start_time is not None else time.time()
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO execution_history
                    (execution_id, workflow_id, start_time, status)
                    VALUES (?, ?, ?, 'RUNNING')
                    """,
                    (execution_id, workflow_id, started),
                )
                self._conn.commit()
        except Exception as exc:
            raise StorageError(f"Failed to record execution start: {exc}") from exc

    def record_update(
        self,
        execution_id: str,
        current_step: int | None = None,
        status: str | None = None,
    ) -> None:
        """Update step number or status of an ongoing execution run."""
        try:
            with self._lock:
                if current_step is not None and status is not None:
                    self._conn.execute(
                        """
                        UPDATE execution_history
                        SET current_step = ?, status = ?
                        WHERE execution_id = ?
                        """,
                        (current_step, status, execution_id),
                    )
                elif current_step is not None:
                    self._conn.execute(
                        """
                        UPDATE execution_history
                        SET current_step = ?
                        WHERE execution_id = ?
                        """,
                        (current_step, execution_id),
                    )
                elif status is not None:
                    self._conn.execute(
                        """
                        UPDATE execution_history
                        SET status = ?
                        WHERE execution_id = ?
                        """,
                        (status, execution_id),
                    )
                self._conn.commit()
        except Exception as exc:
            raise StorageError(f"Failed to update execution record: {exc}") from exc

    def record_complete(
        self,
        execution_id: str,
        status: str,
        end_time: float | None = None,
        error: str | None = None,
    ) -> None:
        """Mark execution as finished (COMPLETED, FAILED, CANCELLED)."""
        finished = end_time if end_time is not None else time.time()
        try:
            with self._lock:
                self._conn.execute(
                    """
                    UPDATE execution_history
                    SET status = ?, end_time = ?, error = ?
                    WHERE execution_id = ?
                    """,
                    (status, finished, error, execution_id),
                )
                self._conn.commit()
        except Exception as exc:
            raise StorageError(f"Failed to record execution completion: {exc}") from exc

    def get_record(self, execution_id: str) -> ExecutionRecord | None:
        """Fetch a specific execution record."""
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """
                    SELECT execution_id, workflow_id, start_time, end_time, status, current_step, error
                    FROM execution_history
                    WHERE execution_id = ?
                    """,
                    (execution_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ExecutionRecord(
                    execution_id=row["execution_id"],
                    workflow_id=row["workflow_id"],
                    start_time=row["start_time"],
                    end_time=row["end_time"],
                    status=row["status"],
                    current_step=row["current_step"],
                    error=row["error"],
                )
        except Exception as exc:
            raise StorageError(f"Failed to fetch execution record: {exc}") from exc

    def list_records(self, limit: int = 100) -> list[ExecutionRecord]:
        """Retrieve recent execution records ordered by start time descending."""
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """
                    SELECT execution_id, workflow_id, start_time, end_time, status, current_step, error
                    FROM execution_history
                    ORDER BY start_time DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                return [
                    ExecutionRecord(
                        execution_id=r["execution_id"],
                        workflow_id=r["workflow_id"],
                        start_time=r["start_time"],
                        end_time=r["end_time"],
                        status=r["status"],
                        current_step=r["current_step"],
                        error=r["error"],
                    )
                    for r in rows
                ]
        except Exception as exc:
            raise StorageError(f"Failed to list execution records: {exc}") from exc
            
    def list_failures(self, limit: int = 20) -> list[ExecutionRecord]:
        """Retrieve recent failed execution records."""
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """
                    SELECT execution_id, workflow_id, start_time, end_time, status, current_step, error
                    FROM execution_history
                    WHERE status = 'FAILED'
                    ORDER BY start_time DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                return [
                    ExecutionRecord(
                        execution_id=r["execution_id"],
                        workflow_id=r["workflow_id"],
                        start_time=r["start_time"],
                        end_time=r["end_time"],
                        status=r["status"],
                        current_step=r["current_step"],
                        error=r["error"],
                    )
                    for r in rows
                ]
        except Exception as exc:
            raise StorageError(f"Failed to list failed records: {exc}") from exc

    def record_trace_event(self, event: Any) -> None:
        import json
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO execution_events
                    (execution_id, timestamp, event_type, step, action, status, duration_ms, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.execution_id,
                        event.timestamp,
                        event.event,
                        event.step,
                        event.action,
                        event.status,
                        event.duration_ms,
                        json.dumps(event.metadata) if event.metadata else None
                    ),
                )
                self._conn.commit()
        except Exception:
            pass # Suppress trace insertion failure to avoid crashing

    def get_events(self, execution_id: str) -> list[dict[str, Any]]:
        import json
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """
                    SELECT timestamp, event_type, step, action, status, duration_ms, metadata_json
                    FROM execution_events
                    WHERE execution_id = ?
                    ORDER BY id ASC
                    """,
                    (execution_id,),
                )
                rows = cursor.fetchall()
                events = []
                for r in rows:
                    meta = r["metadata_json"]
                    events.append({
                        "timestamp": r["timestamp"],
                        "event": r["event_type"],
                        "step": r["step"],
                        "action": r["action"],
                        "status": r["status"],
                        "duration_ms": r["duration_ms"],
                        "metadata": json.loads(meta) if meta else {}
                    })
                return events
        except Exception as exc:
            raise StorageError(f"Failed to fetch execution events: {exc}") from exc

    def close(self) -> None:
        """Close SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
