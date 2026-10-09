"""WinFlow Entry Point - Phase 4 Windows Notification & Event Trigger System.

Initializes logging, validates workflow configuration, boots the core engine,
prepares the execution environment, and listens for configured workflow triggers.
"""

import argparse
import sys
import time
from pathlib import Path

# Ensure package directory and its parent are in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from winflow.core.errors import ConfigurationError, WinFlowError
from winflow.core.logger import setup_logger
from winflow.engine.workflow_engine import WorkflowEngine


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments for WinFlow."""
    default_config = CURRENT_DIR / "workflows" / "example.json"
    parser = argparse.ArgumentParser(
        description="WinFlow - Configurable Windows Desktop Automation Framework (Phase 4)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(default_config),
        help=f"Path to workflow configuration file (default: {default_config})",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging severity level (default: INFO)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and prepare the workflow without starting its trigger listener.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the configured workflow listener until interrupted.

    Lifecycle:
        1. Initialize logging
        2. Initialize the engine
        3. Load configuration
        4. Validate configuration
        5. Prepare the execution environment and trigger listener
        6. Wait for Ctrl+C, then shut down cleanly
    """
    args = parse_arguments()

    # 1. Initialize logging
    logger = setup_logger(name="winflow", level=args.log_level)
    logger.info("=== WinFlow Automation Framework - Phase 4 Windows Notification & Event Trigger System ===")
    engine = None

    try:
        # 2. Initialize the engine
        logger.info("Step 1/5: Initializing WorkflowEngine...")
        engine = WorkflowEngine(logger=logger)
        engine.initialize(log_level=args.log_level)

        # 3 & 4. Load & Validate configuration
        config_path = Path(args.config)
        logger.info("Step 2/5: Loading and validating configuration from '%s'...", config_path)
        workflow_config = engine.load_workflow(config_path)

        workflow_id = workflow_config.get("workflow", {}).get("id")
        wf = workflow_config.get("workflow", {})
        steps_count = len(wf.get("steps", workflow_config.get("steps", [])))
        logger.info(
            "Step 3/5: Configuration valid. Workflow ID: '%s', Steps defined: %d.",
            workflow_id,
            steps_count,
        )

        # 5. Prepare execution environment
        logger.info("Step 4/5: Preparing execution environment...")
        engine.prepare_environment()

        logger.info("Step 5/5: Environment verification completed successfully.")

        if args.validate_only:
            logger.info("Configuration validated. Exiting (--validate-only).")
            return 0

        trigger = engine.start_workflow_listener(workflow_config)
        if trigger is None:
            raise ConfigurationError(
                "No event trigger listener was started for this workflow. "
                "Configure an event trigger or run with --validate-only."
            )

        logger.info(
            "Workflow trigger engine is listening. Press Ctrl+C to stop."
        )
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutdown requested. Stopping the workflow listener...")
        return 0
    except ConfigurationError as exc:
        logger.error("Configuration failure: %s", exc)
        return 1
    except WinFlowError as exc:
        logger.error("WinFlow engine failure: %s", exc)
        return 2
    except Exception:
        logger.exception("Unexpected system exception occurred.")
        return 3
    finally:
        if engine is not None:
            engine.shutdown()


if __name__ == "__main__":
    sys.exit(main())
