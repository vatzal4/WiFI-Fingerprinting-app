"""
Unit and Integration Tests for WiFi Positioning Engine
======================================================
Tests pure functions in locator.py, leave-one-out evaluator,
and Flask /api/locate endpoint with mongomock.
"""

import math
import pytest
import mongomock
from flask import Flask

import app as flask_app
from locator import (
    clean_readings,
    rssi_distance,
    locate,
    load_fingerprints,
    SSID,
    K,
    MISSING_RSSI,
    DEFAULT_BUILDING,
    UNITS_PER_METER,
)
from evaluate_matching import (
    evaluate_leave_one_out,
    calculate_percentile,
    get_verdict,
    run_diagnostics,
    run_tuning,
)


# ==============================================================================
# Pure Function Tests (No database calls)
# ==============================================================================

def test_clean_readings_filters_ssid_rssi_and_duplicates():
    """
    clean_readings drops non-Student SSIDs, rssi <= -100,
    and keeps the strongest duplicate.
    """
    readings = [
        # Non-Student SSID should be dropped
        {"bssid": "00:11:22:33:44:01", "ssid": "Guest-WiFi", "rssi": -45},
        {"bssid": "00:11:22:33:44:02", "ssid": "eduroam", "rssi": -50},
        # rssi <= -100 should be dropped
        {"bssid": "00:11:22:33:44:03", "ssid": "Student", "rssi": -100},
        {"bssid": "00:11:22:33:44:04", "ssid": "Student", "rssi": -105},
        # Valid Student reading with extra whitespace around SSID
        {"bssid": "00:11:22:33:44:05", "ssid": "  Student  ", "rssi": -65},
        # Uppercase STUDENT (as stored in real campus DB)
        {"bssid": "00:11:22:33:44:06", "ssid": "STUDENT", "rssi": -68},
        # Duplicate BSSIDs for Student: should keep strongest (-55 > -75 > -85)
        {"bssid": "AA:BB:CC:DD:EE:01", "ssid": "Student", "rssi": -75},
        {"bssid": "aa:bb:cc:dd:ee:01", "ssid": "Student", "rssi": -55},
        {"bssid": "AA:BB:CC:DD:EE:01", "ssid": "Student", "rssi": -85},
    ]

    cleaned = clean_readings(readings, ssid="Student", min_rssi=-100)

    # 00:11:22:33:44:05, 00:11:22:33:44:06 (STUDENT uppercase), and aa:bb:cc:dd:ee:01 should remain
    assert len(cleaned) == 3
    assert "00:11:22:33:44:01" not in cleaned
    assert "00:11:22:33:44:02" not in cleaned
    assert "00:11:22:33:44:03" not in cleaned
    assert "00:11:22:33:44:04" not in cleaned

    assert cleaned["00:11:22:33:44:05"] == -65.0
    assert cleaned["00:11:22:33:44:06"] == -68.0
    # Duplicate BSSID kept the strongest
    assert cleaned["aa:bb:cc:dd:ee:01"] == -55.0


def test_rssi_distance_missing_aps_and_identical_scans():
    """
    rssi_distance treats missing APs as -100 and is 0 for identical scans.
    """
    scan_a = {"ap1": -60.0, "ap2": -70.0}
    scan_b = {"ap1": -60.0, "ap2": -70.0}

    # Identical scans must return 0.0
    assert rssi_distance(scan_a, scan_b) == 0.0

    # Test missing AP penalty: missing AP in scan_d counted as -100
    scan_c = {"ap1": -60.0, "ap2": -70.0}
    scan_d = {"ap1": -60.0}
    # Union is {"ap1", "ap2"}. Size = 2.
    # diff for ap1 = (-60 - -60) = 0
    # diff for ap2 = (-70 - -100) = 30 -> squared = 900
    # distance = sqrt(900 / 2) = sqrt(450) ~= 21.2132
    expected_dist = math.sqrt(900.0 / 2.0)
    assert math.isclose(rssi_distance(scan_c, scan_d), expected_dist, rel_tol=1e-5)

    # Empty union returns infinity
    assert math.isinf(rssi_distance({}, {}))


def test_locate_exact_stored_position():
    """
    locate returns the exact stored position when the live scan equals a stored fingerprint.
    """
    fingerprints = [
        {
            "id": "fp_target",
            "floor_id": "floor_0",
            "floor_number": 0,
            "x": 350.5,
            "y": 420.0,
            "readings": {"ap1": -55.0, "ap2": -65.0, "ap3": -75.0},
        },
        {
            "id": "fp_other1",
            "floor_id": "floor_0",
            "floor_number": 0,
            "x": 400.0,
            "y": 500.0,
            "readings": {"ap1": -80.0, "ap2": -85.0, "ap3": -90.0},
        },
        {
            "id": "fp_other2",
            "floor_id": "floor_1",
            "floor_number": 1,
            "x": 300.0,
            "y": 200.0,
            "readings": {"ap1": -85.0, "ap2": -90.0, "ap3": -95.0},
        },
    ]

    live_scan = {"ap1": -55.0, "ap2": -65.0, "ap3": -75.0}
    res = locate(live_scan, fingerprints, k=3)

    assert res is not None
    assert res["floor_id"] == "floor_0"
    assert res["floor_number"] == 0
    assert res["x"] == 350.5
    assert res["y"] == 420.0
    assert res["confidence"] == pytest.approx(1.0, rel=1e-3)
    assert res["neighbors"][0]["id"] == "fp_target"
    assert res["neighbors"][0]["distance"] == 0.0


def test_locate_never_mixes_floors():
    """
    locate never mixes floors: with neighbors on two floors,
    the returned x/y comes only from the winning floor.
    """
    # Two close neighbors on Floor 0, one close neighbor on Floor 1
    # Floor 0 has 2 neighbors => total Floor 0 weight dominates
    fingerprints = [
        {
            "id": "fp_fl0_a",
            "floor_id": "fl_0",
            "floor_number": 0,
            "x": 100.0,
            "y": 100.0,
            "readings": {"ap1": -60.0, "ap2": -70.0},
        },
        {
            "id": "fp_fl0_b",
            "floor_id": "fl_0",
            "floor_number": 0,
            "x": 120.0,
            "y": 120.0,
            "readings": {"ap1": -62.0, "ap2": -72.0},
        },
        {
            "id": "fp_fl1_c",
            "floor_id": "fl_1",
            "floor_number": 1,
            "x": 900.0,
            "y": 900.0,  # Far away coordinate on another floor
            "readings": {"ap1": -64.0, "ap2": -71.0},
        },
    ]

    live_scan = {"ap1": -61.0, "ap2": -71.0}
    res = locate(live_scan, fingerprints, k=3)

    assert res is not None
    assert res["floor_id"] == "fl_0"
    assert res["floor_number"] == 0

    # Ensure returned x/y is strictly bounded inside Floor 0 neighbors range [100, 120]
    # and is completely unaffected by Floor 1's (900.0, 900.0)
    assert 100.0 <= res["x"] <= 120.0
    assert 100.0 <= res["y"] <= 120.0
    assert res["x"] < 200.0
    assert res["y"] < 200.0


def test_locate_returns_none_for_empty_scan():
    """
    locate returns None for an empty scan or when no fingerprints are provided.
    """
    dummy_fp = [
        {
            "id": "fp1",
            "floor_id": 0,
            "floor_number": 0,
            "x": 50.0,
            "y": 50.0,
            "readings": {"ap1": -60.0},
        }
    ]

    assert locate({}, dummy_fp) is None
    assert locate({"ap1": -60.0}, []) is None
    assert locate({}, []) is None


# ==============================================================================
# In-Memory Database Loader Tests (using mongomock)
# ==============================================================================

def test_load_fingerprints_with_mongomock():
    """Tests load_fingerprints loader function with in-memory mongomock database."""
    client = mongomock.MongoClient()
    db = client["wifi_fingerprints"]

    # Buildings
    db.buildings.insert_many([
        {"id": 1, "name": "SOS"},
        {"id": 2, "name": "SOT"},
    ])

    # Floors
    db.floors.insert_many([
        {"id": 10, "building_id": 1, "building_name": "SOS", "floor_number": 0},
        {"id": 11, "building_id": 1, "building_name": "SOS", "floor_number": 1},
        {"id": 20, "building_id": 2, "building_name": "SOT", "floor_number": 0},
    ])

    # Fingerprints
    db.fingerprints.insert_many([
        # Valid SOS floor 0
        {
            "_id": "fp_sos_0_1",
            "floor_id": 10,
            "x": 150.0,
            "y": 250.0,
            "readings": [
                {"bssid": "ap_01", "ssid": "Student", "rssi": -60},
                {"bssid": "ap_02", "ssid": "Guest", "rssi": -50},
            ],
        },
        # SOS floor 1
        {
            "_id": "fp_sos_1_1",
            "floor_id": 11,
            "x": 180.0,
            "y": 280.0,
            "readings": [
                {"bssid": "ap_01", "ssid": "Student", "rssi": -70},
            ],
        },
        # SOS floor with only non-Student readings (must be dropped)
        {
            "_id": "fp_sos_dropped",
            "floor_id": 10,
            "x": 200.0,
            "y": 300.0,
            "readings": [
                {"bssid": "ap_03", "ssid": "Staff", "rssi": -60},
            ],
        },
        # SOT fingerprint (must be ignored when querying SOS)
        {
            "_id": "fp_sot_0_1",
            "floor_id": 20,
            "x": 500.0,
            "y": 500.0,
            "readings": [
                {"bssid": "ap_99", "ssid": "Student", "rssi": -55},
            ],
        },
    ])

    loaded = load_fingerprints(building_name="SOS", db=db, ssid="Student")

    assert len(loaded) == 2
    ids = {fp["id"] for fp in loaded}
    assert "fp_sos_0_1" in ids
    assert "fp_sos_1_1" in ids
    assert "fp_sos_dropped" not in ids
    assert "fp_sot_0_1" not in ids

    # Check structure
    fp0 = next(fp for fp in loaded if fp["id"] == "fp_sos_0_1")
    assert fp0["floor_id"] == 10
    assert fp0["floor_number"] == 0
    assert fp0["x"] == 150.0
    assert fp0["y"] == 250.0
    assert fp0["readings"] == {"ap_01": -60.0}


# ==============================================================================
# Endpoint Integration Tests (Flask test_client + mongomock)
# ==============================================================================

@pytest.fixture
def mock_flask_app(monkeypatch):
    """Configures Flask app with an in-memory mongomock database."""
    client = mongomock.MongoClient()
    mock_db = client["wifi_fingerprints"]

    # Buildings & Floors
    mock_db.buildings.insert_one({"id": 1, "name": "SOS"})
    mock_db.floors.insert_many([
        {"id": 1, "building_id": 1, "building_name": "SOS", "floor_number": 0},
        {"id": 2, "building_id": 1, "building_name": "SOS", "floor_number": 1},
    ])

    # Fingerprints for SOS
    mock_db.fingerprints.insert_many([
        {
            "_id": "fp_sos_1",
            "floor_id": 1,
            "x": 100.0,
            "y": 200.0,
            "readings": [
                {"bssid": "ap:01", "ssid": "Student", "rssi": -55},
                {"bssid": "ap:02", "ssid": "Student", "rssi": -65},
            ],
        },
        {
            "_id": "fp_sos_2",
            "floor_id": 1,
            "x": 110.0,
            "y": 210.0,
            "readings": [
                {"bssid": "ap:01", "ssid": "Student", "rssi": -58},
                {"bssid": "ap:02", "ssid": "Student", "rssi": -68},
            ],
        },
        {
            "_id": "fp_sos_3",
            "floor_id": 2,
            "x": 500.0,
            "y": 600.0,
            "readings": [
                {"bssid": "ap:01", "ssid": "Student", "rssi": -85},
                {"bssid": "ap:02", "ssid": "Student", "rssi": -90},
            ],
        },
    ])

    # Monkeypatch db.get_mongo_db in app
    monkeypatch.setattr(flask_app.db, "get_mongo_db", lambda: mock_db)
    flask_app.clear_fingerprints_cache()

    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def test_endpoint_locate_200_success(mock_flask_app):
    """Test POST /api/locate returns 200 with valid estimated coordinates."""
    payload = {
        "building": "SOS",
        "readings": [
            {"bssid": "ap:01", "ssid": "Student", "rssi": -56},
            {"bssid": "ap:02", "ssid": "Student", "rssi": -66},
            {"bssid": "ap:99", "ssid": "Guest", "rssi": -40},
        ],
    }
    response = mock_flask_app.post("/api/locate", json=payload)
    assert response.status_code == 200
    data = response.get_json()

    assert "x" in data
    assert "y" in data
    assert "floor_id" in data
    assert "floor_number" in data
    assert "confidence" in data
    assert "neighbors_used" in data

    assert data["floor_number"] == 0
    assert 100.0 <= data["x"] <= 110.0
    assert 200.0 <= data["y"] <= 210.0
    assert data["neighbors_used"] == 3


def test_endpoint_locate_422_no_student_aps(mock_flask_app):
    """Test POST /api/locate returns 422 when scan contains no valid Student APs."""
    payload = {
        "building": "SOS",
        "readings": [
            {"bssid": "ap:01", "ssid": "Guest", "rssi": -55},
            {"bssid": "ap:02", "ssid": "Student", "rssi": -105},  # too weak
        ],
    }
    response = mock_flask_app.post("/api/locate", json=payload)
    assert response.status_code == 422
    data = response.get_json()
    assert data == {"error": "no Student access points in scan"}


def test_endpoint_locate_404_unknown_building(mock_flask_app):
    """Test POST /api/locate returns 404 for unknown building or building without fingerprints."""
    payload = {
        "building": "NonExistentBuilding",
        "readings": [
            {"bssid": "ap:01", "ssid": "Student", "rssi": -60},
        ],
    }
    response = mock_flask_app.post("/api/locate", json=payload)
    assert response.status_code == 404
    data = response.get_json()
    assert "error" in data


def test_endpoint_locate_400_malformed_body(mock_flask_app):
    """Test POST /api/locate returns 400 for malformed or missing body parameters."""
    # Missing readings
    resp1 = mock_flask_app.post("/api/locate", json={"building": "SOS"})
    assert resp1.status_code == 400

    # Missing building
    resp2 = mock_flask_app.post("/api/locate", json={"readings": [{"bssid": "ap1", "rssi": -60}]})
    assert resp2.status_code == 400

    # Non-json request
    resp3 = mock_flask_app.post("/api/locate", data="plain text body")
    assert resp3.status_code == 400


# ==============================================================================
# Leave-One-Out Cross-Validation Tests
# ==============================================================================

def test_evaluate_leave_one_out_and_percentile():
    """Tests evaluation metrics calculation and percentile interpolation."""
    # Synthetic grid dataset: 4 points on Floor 0, 4 points on Floor 1
    fps = [
        # Floor 0
        {"id": "f0_1", "floor_id": "0", "floor_number": 0, "x": 100.0, "y": 100.0, "readings": {"ap1": -50.0, "ap2": -60.0}},
        {"id": "f0_2", "floor_id": "0", "floor_number": 0, "x": 120.0, "y": 100.0, "readings": {"ap1": -52.0, "ap2": -62.0}},
        {"id": "f0_3", "floor_id": "0", "floor_number": 0, "x": 100.0, "y": 120.0, "readings": {"ap1": -51.0, "ap2": -61.0}},
        {"id": "f0_4", "floor_id": "0", "floor_number": 0, "x": 120.0, "y": 120.0, "readings": {"ap1": -53.0, "ap2": -63.0}},
        # Floor 1 (attenuated by 25 dB)
        {"id": "f1_1", "floor_id": "1", "floor_number": 1, "x": 100.0, "y": 100.0, "readings": {"ap1": -75.0, "ap2": -85.0}},
        {"id": "f1_2", "floor_id": "1", "floor_number": 1, "x": 120.0, "y": 100.0, "readings": {"ap1": -77.0, "ap2": -87.0}},
        {"id": "f1_3", "floor_id": "1", "floor_number": 1, "x": 100.0, "y": 120.0, "readings": {"ap1": -76.0, "ap2": -86.0}},
        {"id": "f1_4", "floor_id": "1", "floor_number": 1, "x": 120.0, "y": 120.0, "readings": {"ap1": -78.0, "ap2": -88.0}},
    ]

    results = evaluate_leave_one_out(fps, k=3, units_per_meter=18.7, exclude_radius=0.0)

    assert results["total_points"] == 8
    # Radio separation between floor 0 and 1 is 25 dB, so floor accuracy should be 100%
    assert results["floor_accuracy"] == 100.0
    assert len(results["worst_points"]) == 5

    # Check verdict helper
    status_good, verdict_good = get_verdict(96.0, 3.2)
    assert status_good == "GOOD"
    assert "GOOD" in verdict_good

    status_bad, verdict_bad = get_verdict(90.0, 6.5)
    assert status_bad == "NEEDS WORK"
    assert "NEEDS WORK" in verdict_bad

    # Check percentile calculation
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    p90 = calculate_percentile(values, 0.90)
    assert p90 == pytest.approx(9.1, rel=1e-3)


# ==============================================================================
# New Matcher Options & Diagnostics Tests
# ==============================================================================

def test_floor_modes_never_mix_floors():
    """
    All floor modes ('vote', 'floor_first', 'strongest_ap') must never mix floors:
    the returned x/y must come strictly from the winning floor.
    """
    # Two close neighbors on Floor 0 [100, 120], one neighbor on Floor 1 [900, 900]
    fingerprints = [
        {
            "id": "fp_fl0_a",
            "floor_id": "fl_0",
            "floor_number": 0,
            "x": 100.0,
            "y": 100.0,
            "readings": {"ap1": -50.0, "ap2": -60.0, "ap3": -65.0},
        },
        {
            "id": "fp_fl0_b",
            "floor_id": "fl_0",
            "floor_number": 0,
            "x": 120.0,
            "y": 120.0,
            "readings": {"ap1": -52.0, "ap2": -62.0, "ap3": -66.0},
        },
        {
            "id": "fp_fl1_c",
            "floor_id": "fl_1",
            "floor_number": 1,
            "x": 900.0,
            "y": 900.0,
            "readings": {"ap1": -70.0, "ap2": -75.0, "ap3": -80.0},
        },
    ]

    live_scan = {"ap1": -51.0, "ap2": -61.0, "ap3": -65.0}

    for mode in ["vote", "floor_first", "strongest_ap"]:
        res = locate(live_scan, fingerprints, k=3, floor_mode=mode)
        assert res is not None, f"Mode {mode} returned None"
        assert res["floor_id"] == "fl_0", f"Mode {mode} chose wrong floor"
        assert res["floor_number"] == 0, f"Mode {mode} chose wrong floor number"
        # Position must be strictly in Floor 0 range [100, 120] and not pulled toward 900
        assert 100.0 <= res["x"] <= 120.0, f"Mode {mode} x-coord mixed floors: {res['x']}"
        assert 100.0 <= res["y"] <= 120.0, f"Mode {mode} y-coord mixed floors: {res['y']}"


def test_clean_readings_min_rssi_cutoff_and_top_n():
    """
    clean_readings properly applies min_rssi_cutoff and top_n filtering.
    """
    readings = [
        {"bssid": "ap1", "ssid": "Student", "rssi": -50},
        {"bssid": "ap2", "ssid": "Student", "rssi": -65},
        {"bssid": "ap3", "ssid": "Student", "rssi": -75},
        {"bssid": "ap4", "ssid": "Student", "rssi": -85},
        {"bssid": "ap5", "ssid": "Student", "rssi": -92},
    ]

    # Test min_rssi_cutoff: ignore readings weaker than -80
    c1 = clean_readings(readings, ssid="Student", min_rssi_cutoff=-80.0)
    assert len(c1) == 3
    assert set(c1.keys()) == {"ap1", "ap2", "ap3"}
    assert "ap4" not in c1  # -85 < -80
    assert "ap5" not in c1  # -92 < -80

    # Test top_n: keep only 2 strongest
    c2 = clean_readings(readings, ssid="Student", top_n=2)
    assert len(c2) == 2
    assert set(c2.keys()) == {"ap1", "ap2"}
    assert c2["ap1"] == -50.0
    assert c2["ap2"] == -65.0


def test_locate_with_custom_matcher_options():
    """
    locate respects min_rssi_cutoff, top_n, and custom missing_rssi.
    """
    fps = [
        {
            "id": "fp1",
            "floor_id": 0,
            "floor_number": 0,
            "x": 50.0,
            "y": 60.0,
            "readings": {"ap1": -55.0, "ap2": -65.0, "ap3": -75.0, "ap4": -88.0},
        },
        {
            "id": "fp2",
            "floor_id": 0,
            "floor_number": 0,
            "x": 80.0,
            "y": 90.0,
            "readings": {"ap1": -70.0, "ap2": -78.0, "ap3": -82.0, "ap4": -95.0},
        },
    ]

    live = {"ap1": -56.0, "ap2": -66.0, "ap3": -76.0, "ap4": -89.0}

    # Cutoff at -80 drops ap4 from both live and stored
    res = locate(
        live,
        fps,
        k=2,
        missing_rssi=-95.0,
        min_rssi_cutoff=-80.0,
        top_n=2,
    )

    assert res is not None
    assert res["floor_number"] == 0
    assert 50.0 <= res["x"] <= 80.0


def test_evaluate_floor_known_mode():
    """
    evaluate_leave_one_out with floor_known=True restricts candidates to true floor.
    """
    fps = [
        {"id": "fp_0_1", "floor_id": "0", "floor_number": 0, "x": 100.0, "y": 100.0, "readings": {"ap1": -50.0}},
        {"id": "fp_0_2", "floor_id": "0", "floor_number": 0, "x": 110.0, "y": 110.0, "readings": {"ap1": -52.0}},
        {"id": "fp_1_1", "floor_id": "1", "floor_number": 1, "x": 200.0, "y": 200.0, "readings": {"ap1": -51.0}},
        {"id": "fp_1_2", "floor_id": "1", "floor_number": 1, "x": 210.0, "y": 210.0, "readings": {"ap1": -53.0}},
    ]

    res = evaluate_leave_one_out(fps, k=1, units_per_meter=18.7, floor_known=True)
    assert res["total_points"] == 4
    # In floor-known mode, floor classification is strictly 100%
    assert res["floor_accuracy"] == 100.0
    assert res["overall_meters"]["mean"] > 0.0


def test_run_diagnostics_and_tuning(capsys):
    """
    Ensures run_diagnostics and run_tuning execute completely without errors.
    """
    fps = [
        {"id": "fp1", "floor_id": "0", "floor_number": 0, "x": 100.0, "y": 100.0, "readings": {"ap1": -50.0, "ap2": -60.0, "ap3": -70.0}},
        {"id": "fp2", "floor_id": "0", "floor_number": 0, "x": 120.0, "y": 100.0, "readings": {"ap1": -52.0, "ap2": -62.0, "ap3": -72.0}},
        {"id": "fp3", "floor_id": "1", "floor_number": 1, "x": 100.0, "y": 100.0, "readings": {"ap1": -75.0, "ap2": -85.0, "ap3": -90.0}},
        {"id": "fp4", "floor_id": "1", "floor_number": 1, "x": 120.0, "y": 100.0, "readings": {"ap1": -77.0, "ap2": -87.0, "ap3": -92.0}},
    ]

    # Run diagnostics
    run_diagnostics(fps, units_per_meter=18.7, missing_rssi=-100.0)
    out_diag = capsys.readouterr().out
    assert "Hub Check" in out_diag
    assert "Stale-Scan Check" in out_diag
    assert "Student AP Count Check" in out_diag
    assert "Floor Confusion Matrix" in out_diag

    # Run tuning
    run_tuning(fps, units_per_meter=18.7)
    out_tune = capsys.readouterr().out
    assert "BASELINE ROW:" in out_tune
    assert "TOP 10 COMBINATIONS" in out_tune
    assert "differences under 2% are noise" in out_tune
