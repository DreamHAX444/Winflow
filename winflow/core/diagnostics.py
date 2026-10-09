import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from winflow.engine.execution_context import ExecutionContext


class DiagnosticsManager:
    """Handles error snapshots, screenshots, and debug data gathering."""

    def __init__(self, base_dir: str = "diagnostics"):
        self.base_dir = Path(base_dir)

    def generate_snapshot(
        self, 
        context: ExecutionContext, 
        error: Exception,
        step: int | None = None,
        action: str | None = None,
        verification: str | None = None,
        retry: int | None = None,
        capture_screenshot: bool = False
    ) -> dict[str, Any]:
        """Create a diagnostic snapshot on failure."""
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        date_folder = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        exec_dir = self.base_dir / date_folder / f"execution_{context.execution_id}"
        exec_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = None
        if capture_screenshot:
            try:
                from PIL import ImageGrab
                safe_step = f"step_{step:02d}" if step is not None else "unknown"
                filename = f"{safe_step}_failure_{timestamp_str}.png"
                full_path = exec_dir / filename
                ImageGrab.grab().save(str(full_path))
                screenshot_path = str(full_path)
            except Exception:
                pass # Failed to capture screenshot, ignore

        snapshot = {
            "execution_id": context.execution_id,
            "workflow_id": context.workflow_id,
            "step": step,
            "action": action,
            "verification": verification,
            "retry_attempt": retry,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "timestamp": timestamp_str,
            "current_window": context.current_window,
            "screenshot_path": screenshot_path,
        }
        
        # Save snapshot json
        safe_step = f"step_{step:02d}" if step is not None else "unknown"
        json_path = exec_dir / f"{safe_step}_snapshot_{timestamp_str}.json"
        try:
            with open(json_path, "w") as f:
                json.dump(snapshot, f, indent=2)
        except Exception:
            pass

        return snapshot

_global_diagnostics = DiagnosticsManager()

def get_diagnostics() -> DiagnosticsManager:
    return _global_diagnostics
