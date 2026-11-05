import json
import math
import os
import tempfile
import unittest

import trend_display


class TrendDisplayPersistenceTests(unittest.TestCase):
    def test_manual_bounds_round_trip_and_generate_exact_endpoint_ticks(self):
        preferences = trend_display.default_preferences()
        preferences["mode"] = "stacked"
        preferences["parameter"] = "P"
        preferences["visible_series"] = ["U", "P"]
        preferences["scales"]["1# Generator"] = {
            "P": trend_display.validate_scale("-2,5", "7,5", "2,5"),
        }

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TrendDisplayConfig.json")
            trend_display.save_preferences(path, preferences)
            loaded = trend_display.load_preferences(path)

        config = loaded["scales"]["1# Generator"]["P"]
        self.assertEqual(
            trend_display.scale_for_values([999.0], config),
            (-2.5, 7.5, 2.5),
        )
        ticks = trend_display.axis_ticks(-2.5, 7.5, 2.5)
        self.assertEqual(ticks, (-2.5, 0.0, 2.5, 5.0, 7.5))
        self.assertEqual(ticks[0], config["minimum"])
        self.assertEqual(ticks[-1], config["maximum"])

    def test_invalid_save_leaves_previous_preferences_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TrendDisplayConfig.json")
            original = trend_display.default_preferences()
            trend_display.save_preferences(path, original)
            with open(path, "rb") as handle:
                original_bytes = handle.read()

            invalid = trend_display.default_preferences()
            invalid["visible_series"] = ["U", "U"]
            with self.assertRaises(ValueError):
                trend_display.save_preferences(path, invalid)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), original_bytes)

    def test_corrupt_existing_config_is_not_reset_or_rewritten(self):
        corrupt = b'{"schema": "SCADA-TREND-DISPLAY-1", invalid'
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TrendDisplayConfig.json")
            with open(path, "wb") as handle:
                handle.write(corrupt)

            with self.assertRaises(ValueError):
                trend_display.load_preferences(path)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), corrupt)

    def test_equipment_scales_remain_isolated_after_persistence(self):
        preferences = trend_display.default_preferences()
        preferences["scales"] = {
            "1# Generator": {
                "U": trend_display.validate_scale(0, 20, 5),
                "f": {"mode": "auto"},
            },
            "2# Generator": {
                "U": trend_display.validate_scale(100, 200, 20),
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TrendDisplayConfig.json")
            trend_display.save_preferences(path, preferences)
            loaded = trend_display.load_preferences(path)

        first = loaded["scales"]["1# Generator"]["U"]
        second = loaded["scales"]["2# Generator"]["U"]
        self.assertEqual(trend_display.scale_for_values([], first), (0.0, 20.0, 5.0))
        self.assertEqual(trend_display.scale_for_values([], second), (100.0, 200.0, 20.0))


class TrendDisplayNumericTests(unittest.TestCase):
    def assertUsefulAutoScale(self, values):
        minimum, maximum, step = trend_display.scale_for_values(values)
        self.assertTrue(math.isfinite(minimum))
        self.assertTrue(math.isfinite(maximum))
        self.assertTrue(math.isfinite(step))
        self.assertLess(minimum, min(values))
        self.assertGreater(maximum, max(values))
        self.assertGreater(step, 0.0)
        ticks = trend_display.axis_ticks(minimum, maximum, step)
        self.assertEqual(ticks, tuple(sorted(set(ticks))))
        self.assertEqual(ticks[0], minimum)
        self.assertEqual(ticks[-1], maximum)
        self.assertLessEqual(len(ticks), 7)
        return ticks

    def test_auto_scale_handles_constant_zero_and_negative_values(self):
        self.assertUsefulAutoScale([42.0, 42.0, 42.0])
        zero_ticks = self.assertUsefulAutoScale([0.0, 0.0])
        self.assertTrue(all(math.isfinite(tick) for tick in zero_ticks))
        self.assertTrue(all(left < right for left, right in zip(zero_ticks, zero_ticks[1:])))
        self.assertUsefulAutoScale([-9.0, -6.0, -3.0])

    def test_auto_scale_ignores_missing_nonfinite_and_invalid_samples(self):
        valid_values = [-2.0, 4.0]
        expected = trend_display.scale_for_values(valid_values)
        actual = trend_display.scale_for_values(
            valid_values
            + [None, float("nan"), float("inf"), -float("inf"), "12"]
        )
        self.assertEqual(actual, expected)

    def test_small_value_ticks_have_distinct_readable_labels(self):
        ticks = self.assertUsefulAutoScale([1.2e-7, 1.5e-7, 1.8e-7])
        labels = [trend_display.format_tick(value, ticks[1] - ticks[0]) for value in ticks]
        self.assertEqual(len(labels), len(set(labels)))

    def test_nondivisible_manual_step_keeps_both_exact_bounds(self):
        ticks = trend_display.axis_ticks(-1.0, 1.0, 0.6)
        self.assertEqual(ticks[0], -1.0)
        self.assertEqual(ticks[-1], 1.0)
        self.assertEqual(ticks, tuple(sorted(set(ticks))))
        self.assertNotIn(-0.0, [tick for tick in ticks if math.copysign(1.0, tick) < 0])

    def test_offset_manual_ticks_retain_fractional_labels(self):
        ticks = trend_display.axis_ticks(0.25, 7.25, 1.0)
        self.assertEqual(
            ticks,
            (0.25, 1.25, 2.25, 3.25, 4.25, 5.25, 6.25, 7.25),
        )
        self.assertEqual(
            [trend_display.format_tick(value, 1.0) for value in ticks],
            [
                "0.25", "1.25", "2.25", "3.25",
                "4.25", "5.25", "6.25", "7.25",
            ],
        )

    def test_auto_scale_rejects_an_overflowing_extreme_range(self):
        maximum = float.fromhex("0x1.fffffffffffffp+1023")
        with self.assertRaises(ValueError):
            trend_display.scale_for_values([-maximum, maximum])

    def test_large_adjacent_ticks_format_differently(self):
        first = trend_display.format_tick(1_000_000_000_000.0, 1.0)
        second = trend_display.format_tick(1_000_000_000_001.0, 1.0)
        self.assertNotEqual(first, second)

    def test_invalid_scale_fields_and_preferences_are_rejected(self):
        invalid_scales = (
            (True, 10, 1),
            (0, float("nan"), 1),
            (0, 10, 0),
            (10, 0, 1),
            (0, 10, 11),
            (0, 10, 0.01),
            ("1e-400", 1, 0.1),
            (1e16, 1e16 + 100, 1),
        )
        for fields in invalid_scales:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                trend_display.validate_scale(*fields)

        invalid_preferences = trend_display.default_preferences()
        invalid_preferences["scales"] = {
            "Unit A": {"U": {"mode": "auto", "minimum": 0}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TrendDisplayConfig.json")
            with self.assertRaises(ValueError):
                trend_display.save_preferences(path, invalid_preferences)
            self.assertFalse(os.path.exists(path))

    def test_semantically_invalid_json_is_rejected(self):
        invalid = trend_display.default_preferences()
        invalid["mode"] = "percent"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TrendDisplayConfig.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(invalid, handle)
            with self.assertRaises(ValueError):
                trend_display.load_preferences(path)


class TrendCursorLookupTests(unittest.TestCase):
    def test_nearest_actual_sample_keeps_earlier_midpoint_ties(self):
        epochs = (1000, 1060, 1120)
        for target, expected in (
            (900, 0),
            (1000, 0),
            (1029.999, 0),
            (1030, 0),
            (1030.001, 1),
            (1090, 1),
            (1120, 2),
            (1200, 2),
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    trend_display.nearest_sample_index(epochs, target),
                    expected,
                )

    def test_empty_history_has_no_actual_sample(self):
        self.assertIsNone(trend_display.nearest_sample_index((), 1030.25))


if __name__ == "__main__":
    unittest.main()
