#!/usr/bin/env python
"""Test script to verify Windows Notification Trigger works with wa.json workflow."""

import sys
import time
import threading
from pathlib import Path

# Ensure package directory is in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from winflow.engine.workflow_engine import WorkflowEngine
from winflow.triggers.event import NotificationEvent
from winflow.core.logger import setup_logger


def test_trigger_simulation():
    """Test that a simulated notification triggers the workflow."""
    logger = setup_logger(name="winflow.test", level="DEBUG")
    logger.info("=" * 60)
    logger.info("TEST: Simulating notification trigger for wa.json")
    logger.info("=" * 60)

    engine = WorkflowEngine(logger=logger)
    engine.initialize(log_level="DEBUG")
    engine.prepare_environment()

    # Load the workflow
    workflow_path = CURRENT_DIR / "winflow" / "workflows" / "wa.json"
    if not workflow_path.exists():
        workflow_path = CURRENT_DIR / "wa.json"
    config = engine.load_workflow(workflow_path)
    workflow_id = config.get("workflow", {}).get("id", "unknown")
    logger.info(f"Loaded workflow: {workflow_id}")

    # Start the trigger listener
    trigger = engine.start_workflow_listener(config)
    if trigger is None:
        logger.error("FAIL: No trigger started")
        return False

    logger.info(f"Trigger started: {trigger.name}")
    logger.info(f"Trigger config: {trigger.config}")

    # Wait a moment for trigger to fully start
    time.sleep(0.5)

    # Create a matching notification event
    test_event = NotificationEvent(
        event_id="test-notification-001",
        application_id="Antigravity IDE",
        application_name="Antigravity IDE",
        title="Code review ready",
        body="New changes to controller_app ready for review",
        source="windows_notification",
    )

    logger.info("=" * 60)
    logger.info("SIMULATING NOTIFICATION EVENT")
    logger.info(f"  event_id: {test_event.event_id}")
    logger.info(f"  application_id: {test_event.application_id}")
    logger.info(f"  application_name: {test_event.application_name}")
    logger.info(f"  title: {test_event.title}")
    logger.info(f"  body: {test_event.body}")
    logger.info("=" * 60)

    # Simulate the trigger event - need to use "application" key for simulate_trigger_event
    event_data = test_event.to_dict()
    event_data["application"] = event_data["application_id"]  # simulate_trigger_event expects "application" not "application_id"
    result = engine.simulate_trigger_event(trigger.name, event_data)
    logger.info(f"simulate_trigger_event returned: {result}")

    if not result:
        logger.error("FAIL: simulate_trigger_event returned False")
        return False

    # Wait for workflow to start executing
    logger.info("Waiting for workflow to start...")
    time.sleep(2)

    # Check if workflow execution was registered
    status = engine.get_status()
    active_executions = status.get("active_executions", [])
    logger.info(f"Active executions: {active_executions}")

    if active_executions:
        exec_id = active_executions[0]
        exec_state = engine.get_current_execution(exec_id)
        logger.info(f"SUCCESS: Workflow started! Execution ID: {exec_id}")
        logger.info(f"Execution state: {exec_state}")
        
        # Wait for workflow to complete (or at least progress)
        logger.info("Waiting for workflow to progress (max 30s)...")
        for i in range(30):
            time.sleep(1)
            exec_state = engine.get_current_execution(exec_id)
            if exec_state:
                step = exec_state.get("current_step", 0)
                action = exec_state.get("current_action", "unknown")
                logger.info(f"  Progress: step={step}, action={action}")
                if step >= 22:  # All steps done
                    logger.info("Workflow completed all steps!")
                    break
            else:
                logger.info("Workflow execution finished (no longer in active)")
                break
        
        return True
    else:
        logger.error("FAIL: No active execution found after trigger")
        return False


def test_matcher_directly():
    """Test the NotificationMatcher directly with wa.json config."""
    logger = setup_logger(name="winflow.test.matcher", level="DEBUG")
    logger.info("=" * 60)
    logger.info("TEST: NotificationMatcher with wa.json trigger config")
    logger.info("=" * 60)

    from winflow.triggers.matcher import NotificationMatcher

    # Exact config from wa.json
    trigger_config = {
        "match": {
            "application": {
                "mode": "contains",
                "value": "Antigravity IDE"
            }
        }
    }

    matcher = NotificationMatcher(config=trigger_config.get("match", {}))

    # Test cases
    test_cases = [
        # (app_id, app_name, title, body, expected_match, description)
        ("Antigravity IDE", "Antigravity IDE", "Test", "Body", True, "Exact match"),
        ("Antigravity IDE.exe", "Antigravity IDE", "Test", "Body", True, "Contains in app_id"),
        ("Some Antigravity IDE App", "Other", "Test", "Body", True, "Contains in app_id"),
        ("antigravity ide", "other", "Test", "Body", True, "Case insensitive"),
        ("VS Code", "Visual Studio Code", "Test", "Body", False, "No match"),
        ("Notepad", "Notepad", "Test", "Body", False, "Wrong app"),
    ]

    all_passed = True
    for app_id, app_name, title, body, expected, desc in test_cases:
        event = NotificationEvent(
            event_id="test",
            application_id=app_id,
            application_name=app_name,
            title=title,
            body=body,
        )
        result = matcher.matches(event)
        status = "PASS" if result.matched == expected else "FAIL"
        if result.matched != expected:
            all_passed = False
        logger.info(f"  {status}: {desc} - app_id='{app_id}', app_name='{app_name}' -> matched={result.matched} (expected={expected})")
        if not result.matched:
            logger.info(f"    Reason: {result.reason}")

    return all_passed


def test_database_source_exists():
    """Check if wpndatabase.db exists and is readable."""
    import os
    logger = setup_logger(name="winflow.test.db", level="INFO")
    logger.info("=" * 60)
    logger.info("TEST: Windows Notification Database accessibility")
    logger.info("=" * 60)

    db_path = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")
    
    if not os.path.exists(db_path):
        logger.warning(f"Database NOT found at: {db_path}")
        logger.warning("This is expected if running on non-Windows or notifications disabled")
        return False

    logger.info(f"Database found at: {db_path}")

    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        cur = conn.cursor()
        cur.execute("SELECT MAX(Id), COUNT(*) FROM Notification;")
        row = cur.fetchone()
        max_id, count = row if row else (0, 0)
        logger.info(f"  Max notification ID: {max_id}")
        logger.info(f"  Total notifications: {count}")
        
        # Show recent notifications
        cur.execute("""
            SELECT Id, ArrivalTime, PrimaryId, substr(Payload, 1, 100)
            FROM Notification ORDER BY Id DESC LIMIT 3
        """)
        rows = cur.fetchall()
        logger.info("  Recent notifications:")
        for row in rows:
            logger.info(f"    ID={row[0]}, ArrivalTime={row[1]}, App={row[2]}, Payload={row[3]}...")
        
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error reading database: {e}")
        return False


if __name__ == "__main__":
    print("WinFlow Trigger Test Suite")
    print("=" * 60)

    # Test 1: Database accessibility
    print("\n[1/3] Testing database accessibility...")
    test_database_source_exists()

    # Test 2: Matcher logic
    print("\n[2/3] Testing NotificationMatcher...")
    matcher_ok = test_matcher_directly()
    print(f"  Matcher test: {'PASSED' if matcher_ok else 'FAILED'}")

    # Test 3: Full trigger simulation
    print("\n[3/3] Testing full trigger simulation...")
    trigger_ok = test_trigger_simulation()
    print(f"  Trigger simulation: {'PASSED' if trigger_ok else 'FAILED'}")

    print("\n" + "=" * 60)
    if matcher_ok and trigger_ok:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)