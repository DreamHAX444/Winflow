"""Coordinate rules for mouse steps, shared by the workflow editor and tests.

For click, double-click, and right-click steps, omitted ``x``/``y`` means "use the
current cursor position" at run time. An explicit ``0`` is a real coordinate and is
preserved. The editor never writes a placeholder ``0`` for an omitted coordinate.
Move Mouse always needs both coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CURSOR_ACTIONS: frozenset[str] = frozenset({"click", "double_click", "right_click"})
REQUIRED_COORDINATE_ACTIONS: frozenset[str] = frozenset({"move"})
COORDINATE_ACTIONS: frozenset[str] = CURSOR_ACTIONS | REQUIRED_COORDINATE_ACTIONS

CURSOR_HELP_TEXT = (
    "Leave X and Y empty to act at the current cursor position. "
    "Enter both values, or use Pick Location, to fix a point on screen."
)


class CoordinateError(ValueError):
    """Raised when coordinate text cannot be saved. The message is user-facing."""


@dataclass
class CoordinateChange:
    """The edit to apply to a step's ``params``: values to set and keys to remove."""

    set_values: dict[str, int] = field(default_factory=dict)
    remove_keys: tuple[str, ...] = ()


def _parse_int(text: str, label: str) -> int:
    try:
        return int(text.strip())
    except ValueError as exc:
        raise CoordinateError(f"{label} must be a whole number of pixels.") from exc


def coordinate_change_for(action: str, x_text: str, y_text: str) -> CoordinateChange:
    """Translate the X/Y field text for ``action`` into a params change.

    Rules:
        * Both fields blank: click-type actions remove ``x`` and ``y`` (current
          cursor); Move Mouse is refused because it needs a target.
        * Exactly one field blank: refused. Partial pairs are never saved.
        * Both filled: integers are stored, including an explicit ``0``.

    Raises:
        CoordinateError: For partial pairs, blank Move Mouse coordinates, or
            non-integer text.
    """
    x_blank = not x_text.strip()
    y_blank = not y_text.strip()
    if x_blank and y_blank:
        if action in REQUIRED_COORDINATE_ACTIONS:
            raise CoordinateError("Move Mouse needs both X and Y. Use Pick Location to choose a point.")
        return CoordinateChange(remove_keys=("x", "y"))
    if x_blank or y_blank:
        raise CoordinateError(
            "Enter both X and Y, or clear both to use the current cursor position."
        )
    return CoordinateChange(
        set_values={"x": _parse_int(x_text, "X"), "y": _parse_int(y_text, "Y")}
    )


def check_coordinate_presence(action: str, params: dict) -> None:
    """Refuse a loaded step whose coordinates are incomplete, without converting values.

    Used for steps whose coordinate fields were not edited, so existing values such as
    variable references are not reinterpreted. Partial pairs and a Move Mouse without
    both coordinates are reported before the file is written.
    """
    has_x = params.get("x") is not None
    has_y = params.get("y") is not None
    if action in REQUIRED_COORDINATE_ACTIONS and not (has_x and has_y):
        raise CoordinateError("Move Mouse needs both X and Y. Use Pick Location to choose a point.")
    if has_x != has_y:
        raise CoordinateError("Enter both X and Y, or clear both to use the current cursor position.")


def apply_coordinate_change(params: dict, change: CoordinateChange) -> None:
    """Apply ``change`` to ``params`` in place."""
    for key in change.remove_keys:
        params.pop(key, None)
    params.update(change.set_values)
