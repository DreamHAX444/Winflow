"""Centralized logging subsystem for WinFlow.

Provides structured logging capable of tracking execution ID, workflow ID,
step numbers, action names, statuses, errors, and timestamps.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
STRUCTURED_LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] "
    "[exec_id=%(execution_id)s] [wf=%(workflow_id)s] [step=%(step)s] [action=%(action)s] "
    "%(message)s"
)


class WinFlowLogAdapter(logging.LoggerAdapter):
    """Contextual logger adapter binding execution metadata to log records."""

    def __init__(self, logger: logging.Logger, extra: dict[str, Any] | None = None) -> None:
        super().__init__(logger, extra or {})

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = dict(self.extra)
        if "extra" in kwargs:
            extra.update(kwargs["extra"])

        # Ensure default values for structured tokens
        extra.setdefault("execution_id", "-")
        extra.setdefault("workflow_id", "-")
        extra.setdefault("step", "-")
        extra.setdefault("action", "-")

        kwargs["extra"] = extra
        return msg, kwargs


def close_logger_handlers(logger: logging.Logger) -> None:
    """Close and flush all handlers attached to a logger."""
    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)


from logging.handlers import RotatingFileHandler

TRACE_LEVEL_NUM = 5
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")

def trace(self, message, *args, **kws):
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)
logging.Logger.trace = trace
logging.LoggerAdapter.trace = trace

def setup_logger(
    name: str = "winflow",
    level: int | str = logging.INFO,
    log_file: str | Path | None = None,
    console: bool = True,
    propagate: bool = False,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure and initialize a WinFlow logger.

    Args:
        name: Logger name.
        level: Logging level (e.g., logging.INFO, "DEBUG", "TRACE").
        log_file: Optional file destination for logs.
        console: Whether to attach stdout stream handler.
        propagate: Whether to propagate to root logger.
        max_bytes: Max size in bytes before rotating.
        backup_count: Number of rotated files to keep.

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    if isinstance(level, str):
        level_str = level.upper()
        if level_str == "TRACE":
            level = TRACE_LEVEL_NUM
        else:
            level = getattr(logging, level_str, logging.INFO)
    
    logger.setLevel(level)
    logger.propagate = propagate

    # Close existing handlers to prevent duplicate lines and release open file handles
    close_logger_handlers(logger)

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(file_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "winflow") -> logging.Logger:
    """Retrieve a logger by name, configuring default stdout logger if none exists."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        setup_logger(name)
    return logger


def create_context_logger(
    execution_id: str = "-",
    workflow_id: str = "-",
    step: str | int = "-",
    action: str = "-",
    parent_logger: logging.Logger | None = None,
) -> WinFlowLogAdapter:
    """Create a contextual adapter with predefined execution metadata."""
    logger = parent_logger or get_logger()
    return WinFlowLogAdapter(
        logger,
        extra={
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "step": str(step),
            "action": action,
        },
    )


def log_step_event(
    logger: logging.Logger,
    execution_id: str,
    workflow_id: str,
    step: int | str,
    action: str,
    status: str,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a structured step event log entry.

    Args:
        logger: Target logger.
        execution_id: Current execution ID.
        workflow_id: Current workflow ID.
        step: Step number or identifier.
        action: Action name.
        status: Execution status (e.g., STARTED, SUCCESS, FAILED, RETRY).
        error: Optional error description.
        extra: Additional arbitrary metadata.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    msg = f"StepEvent [status={status}]"
    if error:
        msg += f" [error={error}]"

    adapter = WinFlowLogAdapter(
        logger,
        extra={
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "step": str(step),
            "action": action,
            "timestamp": timestamp,
            **(extra or {}),
        },
    )

    if status.upper() in {"FAILED", "ERROR"}:
        adapter.error(msg)
    else:
        adapter.info(msg)

    # Publish to EventBus
    try:
        from winflow.core.execution_trace import ExecutionTraceEvent, get_event_bus
        try:
            step_int = int(step) if step not in ("-", "") else None
        except ValueError:
            step_int = None
            
        trace_event = ExecutionTraceEvent(
            event=f"STEP_{status.upper()}",
            execution_id=execution_id,
            workflow_id=workflow_id,
            step=step_int,
            action=action,
            status=status,
            metadata=extra or {}
        )
        if error:
            trace_event.metadata["error"] = error
            
        get_event_bus().publish(trace_event)
    except Exception:
        pass
