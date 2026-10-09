import sqlite3

from winflow.core.logger import get_logger
from winflow.engine.workflow_engine import WorkflowEngine


class HealthChecker:
    def __init__(self, engine: WorkflowEngine):
        self.engine = engine
        self.logger = get_logger()

    def run_check(self) -> dict[str, str]:
        results = {}
        
        # 1. Configuration system
        try:
            from winflow.config.validator import validate_config
            validate_config({
                "schema_version": "1.0",
                "workflow": {"id": "health_check"},
                "steps": [{"action": "wait", "params": {"seconds": 0}}],
            })
            results["Configuration System"] = "PASS"
        except Exception:
            results["Configuration System"] = "FAIL"
            
        # 2. Action Registry
        try:
            if len(self.engine.action_registry.list_actions()) > 0:
                results["Action Registry"] = "PASS"
            else:
                results["Action Registry"] = "WARNING"
        except Exception:
            results["Action Registry"] = "FAIL"
            
        # 3. Verification Registry
        try:
            if len(self.engine.verification_registry.list_verifications()) > 0:
                results["Verification Registry"] = "PASS"
            else:
                results["Verification Registry"] = "WARNING"
        except Exception:
            results["Verification Registry"] = "FAIL"
            
        # 4. Trigger Registry
        try:
            if len(self.engine.trigger_registry.list_triggers()) > 0:
                results["Trigger Registry"] = "PASS"
            else:
                results["Trigger Registry"] = "WARNING"
        except Exception:
            results["Trigger Registry"] = "FAIL"
            
        # 5. Database (SQLite)
        try:
            history_path = getattr(self.engine.history_storage, "db_path", None)
            if history_path:
                with sqlite3.connect(history_path) as conn:
                    conn.execute("SELECT 1 FROM execution_history LIMIT 1")
                results["Database (SQLite)"] = "PASS"
            else:
                results["Database (SQLite)"] = "WARNING"
        except Exception:
            results["Database (SQLite)"] = "FAIL"
            
        # 6. Desktop Backend
        try:
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                results["Desktop Backend"] = "PASS"
            else:
                results["Desktop Backend"] = "WARNING (Desktop access unavailable)"
        except Exception:
            results["Desktop Backend"] = "WARNING (Desktop access unavailable)"
            
        # 7. Emergency Stop
        try:
            is_trig = self.engine.emergency_stop.is_triggered()
            results["Emergency Stop"] = "PASS"
        except Exception:
            results["Emergency Stop"] = "FAIL"
            
        # 8. Desktop Execution Lock
        try:
            # Check if we can safely interact with it without actually locking forever
            if self.engine.desktop_lock.acquire(owner="health_check", timeout=0.01):
                self.engine.desktop_lock.release(owner="health_check")
                results["Desktop Execution Lock"] = "PASS"
            else:
                results["Desktop Execution Lock"] = "WARNING (Currently Locked)"
        except Exception:
            results["Desktop Execution Lock"] = "FAIL"
            
        return results
