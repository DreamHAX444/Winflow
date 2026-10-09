"""WinFlow Verification Subsystem.

Exports verification base classes, results, and standard concrete implementations.
"""

from typing import TYPE_CHECKING, Dict, Optional, Type

from winflow.verification.base import BaseVerification, VerificationResult
from winflow.verification.process import ProcessRunningVerification
from winflow.verification.window import (
    WindowActiveVerification,
    WindowExistsVerification,
)

if TYPE_CHECKING:
    from winflow.engine.verification_registry import VerificationRegistry

PHASE3_VERIFICATIONS: dict[str, type[BaseVerification]] = {
    "window_exists": WindowExistsVerification,
    "window_active": WindowActiveVerification,
    "process_running": ProcessRunningVerification,
}


def register_desktop_verifications(registry: Optional["VerificationRegistry"] = None) -> None:
    """Register all Phase 3 verification checks into target VerificationRegistry."""
    from winflow.engine.verification_registry import get_verification_registry

    target = registry if registry is not None else get_verification_registry()
    for name, ver_cls in PHASE3_VERIFICATIONS.items():
        target.register(name, ver_cls, allow_override=True)


__all__ = [
    "PHASE3_VERIFICATIONS",
    "BaseVerification",
    "ProcessRunningVerification",
    "VerificationResult",
    "WindowActiveVerification",
    "WindowExistsVerification",
    "register_desktop_verifications",
]
