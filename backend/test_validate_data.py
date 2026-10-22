"""
Tests for validate_data.py
===========================
Tests the helpers, individual audit checks, data loader, and full pipeline
using mongomock in-memory database.
"""

import io
import math
import sys
import unittest
import mongomock

import validate_data
from validate_data import (
    rssi_distance,
    xy_distance,
    build_synthetic_database,
    load_dataset,
    run_all_checks,
)


class TestValidateData(unittest.TestCase):
    def test_rssi_distance_identical(self):
        scan_a = {"ap1": -60, "ap2": -70}
        scan_b = {"ap1": -60, "ap2": -70}
        self.assertEqual(rssi_distance(scan_a, scan_b), 0.0)

    def test_rssi_distance_empty_union(self):
        self.assertTrue(math.isinf(rssi_distance({}, {})))

    def test_rssi_distance_missing_ap_penalty(self):
        scan_a = {"ap1": -50, "ap2": -70}
        scan_b = {"ap1": -50}
        expected = math.sqrt(900.0 / 2.0)
        self.assertAlmostEqual(rssi_distance(scan_a, scan_b), expected, places=4)

    def test_xy_distance(self):
        p = {"x": 10.0, "y": 20.0}
        q = {"x": 13.0, "y": 24.0}
        self.assertAlmostEqual(xy_distance(p, q), 5.0, places=6)

    def test_duplicate_bssid_keeps_strongest_rssi(self):
        client = mongomock.MongoClient()
        db = client["test_db"]
        db.fingerprints.insert_one({
            "_id": "fp_test",
            "floor_id": 1,
            "x": 100.0,
            "y": 150.0,
            "readings": [
                {"bssid": "aa:bb:cc:dd:ee:ff", "rssi": -85},
                {"bssid": "aa:bb:cc:dd:ee:ff", "rssi": -45},  # Stronger!
                {"bssid": "aa:bb:cc:dd:ee:ff", "rssi": -70},
            ],
        })
        fps, _ = load_dataset(db)
        self.assertEqual(len(fps), 1)
        self.assertEqual(fps[0]["readings"]["aa:bb:cc:dd:ee:ff"], -45)

    def test_clean_synthetic_passes_all_checks(self):
        db = build_synthetic_database(with_mistakes=False)
        fps, floors = load_dataset(db)
        self.assertEqual(len(fps), 40)

        captured_out = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured_out
            fails, warns = run_all_checks(
                fps, floors, svg_width=1000.0, svg_height=800.0, max_gap=150.0, min_aps=4
            )
        finally:
            sys.stdout = old_stdout

        self.assertEqual(fails, 0)
        self.assertEqual(warns, 0)
        output = captured_out.getvalue()
        self.assertIn("Summary: 0 FAIL, 0 WARN", output)
        self.assertIn("Data looks good. Safe to move on to the matching step.", output)

    def test_synthetic_with_mistakes_flags_four_checks(self):
        db = build_synthetic_database(with_mistakes=True)
        fps, floors = load_dataset(db)

        captured_out = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured_out
            fails, warns = run_all_checks(
                fps, floors, svg_width=1000.0, svg_height=800.0, max_gap=150.0, min_aps=4
            )
        finally:
            sys.stdout = old_stdout

        self.assertEqual(fails, 2)
        self.assertEqual(warns, 2)
        output = captured_out.getvalue()
        self.assertIn("Summary: 2 FAIL, 2 WARN", output)
        self.assertIn("Fix the FAIL items before building positioning.", output)

        # Confirm all 4 checks flagged
        self.assertIn("[FAIL] 1 point(s) outside SVG bounds", output)  # Check 3
        self.assertIn("have fewer than 4 APs", output)  # Check 2
        self.assertIn("bad RSSI reading(s)", output)  # Check 4
        self.assertIn("suspicious point(s)", output)  # Check 7

    def test_filter_by_ssid(self):
        fps = [
            {
                "id": "fp_1",
                "floor_id": 1,
                "x": 10.0,
                "y": 10.0,
                "readings": {"ap1": -60, "ap2": -70},
                "raw_readings": [
                    {"bssid": "ap1", "ssid": " Student ", "rssi": -60},
                    {"bssid": "ap2", "ssid": "Guest", "rssi": -70},
                ],
            },
            {
                "id": "fp_2",
                "floor_id": 1,
                "x": 20.0,
                "y": 20.0,
                "readings": {"ap2": -75},
                "raw_readings": [
                    {"bssid": "ap2", "ssid": "Guest", "rssi": -75},
                ],
            },
        ]
        kept, dropped = validate_data.filter_by_ssid(fps, "Student")
        self.assertEqual(dropped, 1)  # fp_2 had no Student readings
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["id"], "fp_1")
        self.assertEqual(kept[0]["readings"], {"ap1": -60})

    def test_min_rssi_check_4(self):
        fps = [
            {
                "id": "fp_1",
                "floor_id": 1,
                "x": 10.0,
                "y": 10.0,
                "readings": {"ap1": -102},
                "raw_readings": [{"bssid": "ap1", "rssi": -102}],
            }
        ]
        # With default -100, -102 is bad -> FAIL
        fails_default, _ = validate_data.check_4_rssi_sanity(fps, min_rssi=-100)
        self.assertEqual(fails_default, 1)

        # With min_rssi=-105, -102 is valid -> PASS
        fails_relaxed, _ = validate_data.check_4_rssi_sanity(fps, min_rssi=-105)
        self.assertEqual(fails_relaxed, 0)

        # rssi >= 0 is always bad
        fps_positive = [
            {
                "id": "fp_pos",
                "floor_id": 1,
                "x": 10.0,
                "y": 10.0,
                "readings": {"ap1": 5},
                "raw_readings": [{"bssid": "ap1", "rssi": 5}],
            }
        ]
        fails_pos, _ = validate_data.check_4_rssi_sanity(fps_positive, min_rssi=-105)
        self.assertEqual(fails_pos, 1)


if __name__ == "__main__":
    unittest.main()


