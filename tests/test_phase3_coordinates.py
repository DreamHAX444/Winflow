"""Phase 3 (GAP-005): mouse step coordinates are absent when omitted, never a default 0.

Pure-logic tests. No mouse action is performed.
"""

import unittest

from winflow.config.step_coordinates import (
    CoordinateError,
    apply_coordinate_change,
    coordinate_change_for,
)


class CoordinateRuleTests(unittest.TestCase):
    def test_both_blank_on_click_removes_keys(self):
        params = {"x": 5, "y": 7, "button": "left"}
        apply_coordinate_change(params, coordinate_change_for("click", "", "  "))
        self.assertEqual(params, {"button": "left"})

    def test_explicit_zero_is_preserved(self):
        params = {}
        apply_coordinate_change(params, coordinate_change_for("click", "0", "0"))
        self.assertEqual(params, {"x": 0, "y": 0})

    def test_negative_coordinates_allowed(self):
        params = {}
        apply_coordinate_change(params, coordinate_change_for("right_click", "-1200", "40"))
        self.assertEqual(params, {"x": -1200, "y": 40})

    def test_partial_pair_is_refused(self):
        for action in ("click", "double_click", "right_click", "move"):
            with self.subTest(action=action, which="x only"):
                with self.assertRaises(CoordinateError):
                    coordinate_change_for(action, "10", "")
            with self.subTest(action=action, which="y only"):
                with self.assertRaises(CoordinateError):
                    coordinate_change_for(action, "", "10")

    def test_move_requires_both(self):
        with self.assertRaises(CoordinateError):
            coordinate_change_for("move", "", "")

    def test_move_with_both_is_accepted(self):
        params = {}
        apply_coordinate_change(params, coordinate_change_for("move", "3", "4"))
        self.assertEqual(params, {"x": 3, "y": 4})

    def test_non_integer_is_refused(self):
        with self.assertRaises(CoordinateError):
            coordinate_change_for("click", "ten", "5")
        with self.assertRaises(CoordinateError):
            coordinate_change_for("click", "1.5", "5")

    def test_preserves_unrelated_params(self):
        params = {"button": "right", "clicks": 2, "interval_ms": 50}
        apply_coordinate_change(params, coordinate_change_for("double_click", "9", "9"))
        self.assertEqual(params, {"button": "right", "clicks": 2, "interval_ms": 50, "x": 9, "y": 9})


if __name__ == "__main__":
    unittest.main()
