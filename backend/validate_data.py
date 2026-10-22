"""
WiFi Fingerprint Database Audit Script
=======================================
Audits a WiFi fingerprint database stored in MongoDB Atlas before building
indoor positioning on top of it. Strictly read-only: only uses find/aggregate/count.

Usage:
    python validate_data.py [--db-name NAME] [--svg-width 1000] [--svg-height 800]
                            [--max-gap 150] [--min-aps 4] [--ssid SSID] [--min-rssi -100]

Options:
    --db-name      MongoDB database name (default: from MONGODB_DB_NAME env or 'wifi_fingerprints')
    --svg-width    Maximum allowed SVG width for coordinate sanity (default: 1000)
    --svg-height   Maximum allowed SVG height for coordinate sanity (default: 800)
    --max-gap      Maximum allowed Euclidean distance before a point is isolated (default: 150)
    --min-aps      Minimum required AP count per fingerprint (default: 4)
    --ssid         Filter readings to exact SSID (whitespace trimmed); drop empty fingerprints
    --min-rssi     Minimum valid RSSI in dBm (default: -100); readings below this are flagged as bad
    --mock         Run validation on an in-memory synthetic database with 4 deliberate mistakes
    --mock-clean   Run validation on a clean in-memory synthetic database
    --uri          Override MONGODB_URI connection string (default: read from environment)
"""

import os
import sys
import math
import argparse
import statistics
from collections import Counter
from typing import Dict, List, Tuple, Any, Optional

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pymongo


# Support .env files if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
    # Check parent and backend directories for .env
    _backend_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_backend_dir, ".env"))
    load_dotenv(os.path.join(_backend_dir, "..", ".env"))
except ImportError:
    pass

# ==============================================================================
# CONFIG: Database Schema & Collection/Field Mappings
# ==============================================================================
CONFIG = {
    "COLLECTIONS": {
        "BUILDINGS": "buildings",
        "FLOORS": "floors",
        "FINGERPRINTS": "fingerprints",
        "READINGS": None,  # Set to "readings" if stored in a separate collection, None if embedded
    },
    "FIELDS": {
        # Buildings
        "BUILDING_ID": "id",
        "BUILDING_NAME": "name",
        # Floors
        "FLOOR_ID": "id",
        "FLOOR_BUILDING_ID": "building_id",
        "FLOOR_BUILDING_NAME": "building_name",
        "FLOOR_NUMBER": "floor_number",
        "FLOOR_LABEL": "label",
        # Fingerprints
        "FP_ID": "_id",
        "FP_FLOOR_ID": "floor_id",
        "FP_X": "x",
        "FP_Y": "y",
        "FP_NODE_ID": "node_id",
        "FP_CAPTURED_AT": "captured_at",
        "FP_READINGS": "readings",  # Embedded array of AP reading objects
        # Readings (embedded dict keys or separate collection columns)
        "READING_FP_ID": "fingerprint_id",  # Used only if readings in separate collection
        "READING_BSSID": "bssid",
        "READING_SSID": "ssid",
        "READING_RSSI": "rssi",
    },
}


# ==============================================================================
# Shared Helpers
# ==============================================================================

def rssi_distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    """
    Computes Euclidean RSSI distance between two scans.
    a and b are dicts {bssid: rssi}. Takes the union of their bssids.
    If an AP is missing from one scan, uses -100 for it.
    Returns sqrt(sum of squared differences / number of APs in the union).
    Returns math.inf if the union is empty.
    """
    union = set(a.keys()) | set(b.keys())
    if not union:
        return math.inf
    total_sq_diff = sum((a.get(bssid, -100.0) - b.get(bssid, -100.0)) ** 2 for bssid in union)
    return math.sqrt(total_sq_diff / len(union))


def xy_distance(p: Dict[str, Any], q: Dict[str, Any]) -> float:
    """Euclidean distance between two points' x/y coordinates."""
    return math.hypot(p["x"] - q["x"], p["y"] - q["y"])


# ==============================================================================
# Database Connection & Read-Only Data Loading
# ==============================================================================

def connect_to_database(uri: str, db_name: str, timeout_ms: int = 5000):
    """
    Connects to MongoDB Atlas in strictly read-only mode with timeout.
    Exits with code 1 and a clear error message upon failure or timeout.
    """
    if not uri:
        print(
            "Error: MONGODB_URI environment variable is not set.\n"
            "Please set MONGODB_URI in your environment or in a .env file, or pass --uri.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        client = pymongo.MongoClient(
            uri,
            serverSelectionTimeoutMS=timeout_ms,
            readPreference="secondaryPreferred",
        )
        # Verify connection by issuing a ping command
        client.admin.command("ping")
        return client[db_name]
    except Exception as exc:
        print(
            f"Error: Failed to connect to MongoDB Atlas database '{db_name}'.\n"
            f"Details: {exc}\n"
            "Please check that your MONGODB_URI connection string is correct and that "
            "your current IP address is added to the MongoDB Atlas IP Access List (allowlist).",
            file=sys.stderr,
        )
        sys.exit(1)


def load_dataset(db, config: dict = CONFIG) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Loads buildings, floors, and fingerprints strictly read-only into memory as plain dicts.
    If a bssid appears twice in one fingerprint, keeps the strongest rssi (maximum rssi).
    """
    cols = config["COLLECTIONS"]
    fields = config["FIELDS"]

    # 1. Load buildings
    buildings_cursor = db[cols["BUILDINGS"]].find()
    building_map = {}
    for b in buildings_cursor:
        b_id = b.get(fields["BUILDING_ID"], b.get("_id"))
        b_name = b.get(fields["BUILDING_NAME"], "Unknown")
        building_map[b_id] = b_name
        building_map[str(b.get("_id"))] = b_name

    # 2. Load floors
    floors_cursor = db[cols["FLOORS"]].find()
    floors = []
    floor_lookup = {}
    for f in floors_cursor:
        f_id = f.get(fields["FLOOR_ID"], f.get("_id"))
        f_oid = str(f.get("_id"))
        b_id = f.get(fields["FLOOR_BUILDING_ID"])
        b_name = f.get(fields["FLOOR_BUILDING_NAME"]) or building_map.get(b_id) or "Building"
        floor_num = f.get(fields["FLOOR_NUMBER"], 0)
        label = f.get(fields["FLOOR_LABEL"]) or f"Floor {floor_num}"

        floor_entry = {
            "id": f_id,
            "_id": f_oid,
            "building_id": b_id,
            "building_name": b_name,
            "floor_number": floor_num,
            "label": label,
            "display_name": f"{b_name} / floor {floor_num}",
        }
        floors.append(floor_entry)
        floor_lookup[f_id] = floor_entry
        floor_lookup[f_oid] = floor_entry
        floor_lookup[str(f_id)] = floor_entry

    # 3. Load separate readings if configured
    separate_readings_map: Dict[Any, List[Dict[str, Any]]] = {}
    if cols.get("READINGS"):
        readings_cursor = db[cols["READINGS"]].find()
        for r in readings_cursor:
            fp_id = r.get(fields["READING_FP_ID"])
            if fp_id not in separate_readings_map:
                separate_readings_map[fp_id] = []
            separate_readings_map[fp_id].append(r)

    # 4. Load fingerprints
    fp_cursor = db[cols["FINGERPRINTS"]].find()
    fingerprints = []
    for doc in fp_cursor:
        fp_id = str(doc.get(fields["FP_ID"], ""))
        fl_id = doc.get(fields["FP_FLOOR_ID"])

        # Determine readings list
        if cols.get("READINGS"):
            raw_readings = separate_readings_map.get(doc.get(fields["FP_ID"]), [])
            if not raw_readings:
                raw_readings = separate_readings_map.get(fp_id, [])
        else:
            raw_readings = doc.get(fields["FP_READINGS"], [])

        # Process readings: keep strongest RSSI for duplicate BSSIDs
        clean_readings: Dict[str, int] = {}
        for r in raw_readings:
            bssid = str(r.get(fields["READING_BSSID"], "")).strip().lower()
            rssi_val = r.get(fields["READING_RSSI"])
            if not bssid or rssi_val is None:
                continue
            try:
                rssi = int(rssi_val)
            except (ValueError, TypeError):
                continue

            # If duplicate BSSID, keep strongest (maximum) RSSI
            if bssid not in clean_readings or rssi > clean_readings[bssid]:
                clean_readings[bssid] = rssi

        fingerprints.append({
            "id": fp_id,
            "floor_id": fl_id,
            "x": float(doc.get(fields["FP_X"], 0.0)),
            "y": float(doc.get(fields["FP_Y"], 0.0)),
            "node_id": doc.get(fields["FP_NODE_ID"]),
            "captured_at": doc.get(fields["FP_CAPTURED_AT"]),
            "readings": clean_readings,
            "raw_readings": raw_readings,
        })

    # If any floor_id in fingerprints is missing from registered floors, register it dynamically
    known_keys = set(floor_lookup.keys())
    for fp in fingerprints:
        fid = fp["floor_id"]
        if fid not in known_keys and str(fid) not in known_keys:
            unregistered = {
                "id": fid,
                "_id": str(fid),
                "building_id": None,
                "building_name": "Unknown",
                "floor_number": fid,
                "label": f"Floor {fid}",
                "display_name": f"Unknown / floor {fid}",
            }
            floors.append(unregistered)
            floor_lookup[fid] = unregistered
            floor_lookup[str(fid)] = unregistered
            known_keys.add(fid)
            known_keys.add(str(fid))

    return fingerprints, floors


def filter_by_ssid(
    fingerprints: List[Dict[str, Any]], target_ssid: str
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Filters readings across all fingerprints by exact SSID after trimming whitespace.
    Drops every reading whose trimmed SSID != trimmed target_ssid.
    Drops fingerprints left with zero readings.
    Returns (kept_fingerprints, dropped_count).
    """
    target = target_ssid.strip()
    kept: List[Dict[str, Any]] = []
    dropped_count = 0
    fields = CONFIG["FIELDS"]

    for p in fingerprints:
        filtered_clean: Dict[str, int] = {}
        filtered_raw: List[Dict[str, Any]] = []

        for r in p.get("raw_readings", []):
            ssid_val = str(r.get(fields["READING_SSID"], "")).strip()
            if ssid_val == target:
                filtered_raw.append(r)
                bssid = str(r.get(fields["READING_BSSID"], "")).strip().lower()
                rssi_val = r.get(fields["READING_RSSI"])
                if bssid and rssi_val is not None:
                    try:
                        rssi = int(rssi_val)
                        if bssid not in filtered_clean or rssi > filtered_clean[bssid]:
                            filtered_clean[bssid] = rssi
                    except (ValueError, TypeError):
                        pass

        if not filtered_clean:
            dropped_count += 1
        else:
            p["readings"] = filtered_clean
            p["raw_readings"] = filtered_raw
            kept.append(p)

    return kept, dropped_count


# ==============================================================================
# Audit Checks (1 to 9)
# ==============================================================================

def check_1_coverage_per_floor(
    floors: List[Dict[str, Any]], points_by_floor: Dict[Any, List[Dict[str, Any]]]
) -> Tuple[int, int]:
    """1. Coverage per floor: for each floor, count fingerprints. 0=FAIL, <10=WARN, else PASS."""
    print("1. Coverage per floor:")
    fails, warns = 0, 0
    if not floors:
        print("   [WARN] No floors registered in the database.")
        return 0, 1

    for fl in floors:
        fid = fl["id"]
        foid = fl["_id"]
        pts = points_by_floor.get(fid, [])
        if not pts and foid != fid:
            pts = points_by_floor.get(foid, [])
        if not pts:
            pts = points_by_floor.get(str(fid), [])

        count = len(pts)
        label = fl["display_name"]
        if count == 0:
            print(f"   [FAIL] {label}: 0 fingerprints recorded")
            fails += 1
        elif count < 10:
            print(f"   [WARN] {label}: {count} fingerprints recorded (fewer than 10)")
            warns += 1
        else:
            print(f"   [PASS] {label}: {count} fingerprints recorded")

    return fails, warns


def check_2_aps_per_point(fingerprints: List[Dict[str, Any]], min_aps: int) -> Tuple[int, int]:
    """2. APs per point: print min, median, mean and max AP counts. List points with < min-aps."""
    print("2. APs per point:")
    ap_counts = [len(p["readings"]) for p in fingerprints]
    if not ap_counts:
        print("   [FAIL] No AP readings available across any points.")
        return 1, 0

    min_cnt = min(ap_counts)
    med_cnt = statistics.median(ap_counts)
    mean_cnt = statistics.mean(ap_counts)
    max_cnt = max(ap_counts)

    offending = [p["id"] for p in fingerprints if len(p["readings"]) < min_aps]
    stats_str = f"Min: {min_cnt}, Median: {med_cnt:.1f}, Mean: {mean_cnt:.1f}, Max: {max_cnt} APs per point."

    if not offending:
        print(f"   [PASS] {stats_str} All points have at least {min_aps} APs.")
        return 0, 0

    pct = (len(offending) / len(fingerprints)) * 100.0
    ids_preview = offending[:15]
    id_str = ", ".join(ids_preview)
    if len(offending) > 15:
        id_str += f" ... (+{len(offending) - 15} more)"

    if pct > 20.0:
        print(f"   [FAIL] {stats_str} {len(offending)}/{len(fingerprints)} points ({pct:.1f}%) have fewer than {min_aps} APs: {id_str}")
        return 1, 0
    else:
        print(f"   [WARN] {stats_str} {len(offending)}/{len(fingerprints)} points ({pct:.1f}%) have fewer than {min_aps} APs: {id_str}")
        return 0, 1


def check_3_coordinates_bounds(
    fingerprints: List[Dict[str, Any]], svg_width: float, svg_height: float
) -> Tuple[int, int]:
    """3. Coordinates inside floor plan: outside if x not in [0, svg_width] or y not in [0, svg_height]."""
    print("3. Coordinates inside the floor plan:")
    outside = [
        p for p in fingerprints
        if p["x"] < 0 or p["x"] > svg_width or p["y"] < 0 or p["y"] > svg_height
    ]

    if not outside:
        min_x = min(p["x"] for p in fingerprints)
        max_x = max(p["x"] for p in fingerprints)
        min_y = min(p["y"] for p in fingerprints)
        max_y = max(p["y"] for p in fingerprints)
        print(
            f"   [PASS] All coordinates inside SVG bounds [0, {svg_width:.0f}] x [0, {svg_height:.0f}] "
            f"(X range: [{min_x:.1f}, {max_x:.1f}], Y range: [{min_y:.1f}, {max_y:.1f}])."
        )
        return 0, 0

    ids_preview = [p["id"] for p in outside[:15]]
    id_str = ", ".join(ids_preview)
    if len(outside) > 15:
        id_str += f" ... (+{len(outside) - 15} more)"

    print(
        f"   [FAIL] {len(outside)} point(s) outside SVG bounds [0, {svg_width:.0f}] x [0, {svg_height:.0f}]: {id_str}. "
        "The tap-to-SVG coordinate scaling is the likely cause."
    )
    return 1, 0


def check_4_rssi_sanity(fingerprints: List[Dict[str, Any]], min_rssi: int = -100) -> Tuple[int, int]:
    """4. RSSI sanity: a reading with rssi >= 0 or rssi < min_rssi is bad."""
    print("4. RSSI sanity:")
    all_rssis: List[int] = []
    bad_readings: List[Tuple[str, int]] = []

    for p in fingerprints:
        # Check raw readings first if available to catch all surveyor errors
        readings_source = p.get("raw_readings") or [{"rssi": v} for v in p["readings"].values()]
        for r in readings_source:
            rssi_val = r.get("rssi")
            if rssi_val is None:
                continue
            try:
                rssi = int(rssi_val)
            except (ValueError, TypeError):
                bad_readings.append((p["id"], rssi_val))
                continue

            all_rssis.append(rssi)
            if rssi >= 0 or rssi < min_rssi:
                bad_readings.append((p["id"], rssi))

    if not bad_readings:
        min_r = min(all_rssis) if all_rssis else "N/A"
        max_r = max(all_rssis) if all_rssis else "N/A"
        print(f"   [PASS] All RSSI readings are within valid range [{min_rssi}, 0) dBm (min: {min_r} dBm, max: {max_r} dBm).")
        return 0, 0

    example_id, example_val = bad_readings[0]
    print(
        f"   [FAIL] Found {len(bad_readings)} bad RSSI reading(s) (>= 0 or < {min_rssi} dBm). "
        f"Example: point {example_id} had RSSI = {example_val} dBm."
    )
    return 1, 0



def check_5_duplicate_locations(points_by_floor: Dict[Any, List[Dict[str, Any]]]) -> Tuple[int, int]:
    """5. Duplicate locations: count pairs on the same floor with xy_distance < 1.0."""
    print("5. Duplicate locations:")
    dup_pairs = 0
    for fid, pts in points_by_floor.items():
        n = len(pts)
        for i in range(n):
            p = pts[i]
            for j in range(i + 1, n):
                q = pts[j]
                if xy_distance(p, q) < 1.0:
                    dup_pairs += 1

    if dup_pairs == 0:
        print("   [PASS] No duplicate locations found (0 pairs with xy_distance < 1.0 on the same floor).")
        return 0, 0

    print(f"   [WARN] Found {dup_pairs} duplicate location pair(s) with xy_distance < 1.0 (fine if intentional re-scans).")
    return 0, 1


def check_6_coverage_gaps(
    points_by_floor: Dict[Any, List[Dict[str, Any]]], max_gap: float
) -> Tuple[int, int]:
    """6. Coverage gaps: distance to nearest neighbor on the same floor > max_gap is isolated."""
    print("6. Coverage gaps:")
    isolated_points: List[Tuple[str, float]] = []

    for fid, pts in points_by_floor.items():
        if len(pts) == 1:
            isolated_points.append((pts[0]["id"], math.inf))
            continue

        for p in pts:
            min_dist = min(xy_distance(p, q) for q in pts if q is not p)
            if min_dist > max_gap:
                isolated_points.append((p["id"], min_dist))

    if not isolated_points:
        print(f"   [PASS] No coverage gaps detected (all points within {max_gap:.0f} units of another point on the same floor).")
        return 0, 0

    worst_id, worst_dist = max(isolated_points, key=lambda item: item[1])
    worst_str = "inf" if math.isinf(worst_dist) else f"{worst_dist:.1f}"
    print(
        f"   [WARN] Found {len(isolated_points)} isolated point(s) with distance to nearest neighbor > {max_gap:.0f} units. "
        f"Worst: point {worst_id} (distance: {worst_str} units)."
    )
    return 0, 1


def check_7_neighbor_consistency(points_by_floor: Dict[Any, List[Dict[str, Any]]]) -> Tuple[int, int]:
    """
    7. Neighbor consistency: nearest point by xy on same floor, compute rssi_distance.
    Suspicious if distance > max(med * 2, 8). Needs at least 5 points.
    """
    print("7. Neighbor consistency:")
    pair_records: List[Tuple[str, float]] = []

    for fid, pts in points_by_floor.items():
        if len(pts) < 2:
            continue
        for p in pts:
            nearest_neighbor = min((q for q in pts if q is not p), key=lambda q: xy_distance(p, q))
            d_rssi = rssi_distance(p["readings"], nearest_neighbor["readings"])
            pair_records.append((p["id"], d_rssi))

    if len(pair_records) < 5:
        print(f"   [WARN] Not enough points to evaluate neighbor consistency ({len(pair_records)} points with neighbors, need at least 5).")
        return 0, 1

    dists = [d for _, d in pair_records]
    med = statistics.median(dists)
    thresh = max(med * 2.0, 8.0)
    suspicious = [(pid, d) for pid, d in pair_records if d > thresh]

    if not suspicious:
        print(f"   [PASS] Typical neighbor difference: {med:.1f} dB. No suspicious points found (all <= {thresh:.1f} dB).")
        return 0, 0

    ids_preview = [f"{pid} ({d:.1f} dB)" for pid, d in suspicious[:15]]
    id_str = ", ".join(ids_preview)
    if len(suspicious) > 15:
        id_str += f" ... (+{len(suspicious) - 15} more)"

    print(
        f"   [WARN] Typical neighbor difference: {med:.1f} dB. Found {len(suspicious)} suspicious point(s) "
        f"with neighbor RSSI distance > {thresh:.1f} dB: {id_str}. "
        "Consider re-scanning them or checking if the x/y coordinate was tapped in the right place."
    )
    return 0, 1


def check_8_floor_separation(
    points_by_floor: Dict[Any, List[Dict[str, Any]]], max_gap: float
) -> Tuple[int, int]:
    """
    8. Floor separation: for each point and each other floor, find nearest point by xy.
    Skip if farther than max_gap. Too similar if rssi_distance < 5.
    """
    print("8. Floor separation:")
    floors_with_data = [fid for fid, pts in points_by_floor.items() if len(pts) > 0]
    if len(floors_with_data) <= 1:
        print("   [WARN] Only one floor has data; skipping cross-floor separation check.")
        return 0, 1

    total_comparisons = 0
    too_similar_count = 0

    for fid in floors_with_data:
        for p in points_by_floor[fid]:
            for other_fid in floors_with_data:
                if other_fid == fid:
                    continue
                pts_other = points_by_floor[other_fid]
                nearest_other = min(pts_other, key=lambda q: xy_distance(p, q))
                if xy_distance(p, nearest_other) > max_gap:
                    continue

                total_comparisons += 1
                d_rssi = rssi_distance(p["readings"], nearest_other["readings"])
                if d_rssi < 5.0:
                    too_similar_count += 1

    if total_comparisons == 0:
        print(f"   [WARN] No comparable cross-floor pairs found within max-gap ({max_gap:.0f} units).")
        return 0, 1

    if too_similar_count == 0:
        print(f"   [PASS] Cross-floor separation verified: 0/{total_comparisons} cross-floor pairs within {max_gap:.0f} units have RSSI distance < 5 dB.")
        return 0, 0

    print(
        f"   [WARN] {too_similar_count}/{total_comparisons} cross-floor pairs look nearly identical (RSSI distance < 5 dB). "
        "Floor mix-ups are likely unless a barometer or stairwell check-in is added."
    )
    return 0, 1


def check_9_ap_reliability(fingerprints: List[Dict[str, Any]]) -> Tuple[int, int]:
    """9. AP reliability: count unique bssids and how many appear at exactly one point."""
    print("9. AP reliability:")
    bssid_counts: Counter = Counter()
    for p in fingerprints:
        for bssid in p["readings"].keys():
            bssid_counts[bssid] += 1

    unique_bssids = len(bssid_counts)
    if unique_bssids == 0:
        print("   [WARN] No AP BSSIDs detected across the surveyed points.")
        return 0, 1

    single_point_count = sum(1 for c in bssid_counts.values() if c == 1)
    pct = (single_point_count / unique_bssids) * 100.0
    summary_str = f"Found {unique_bssids} unique BSSIDs; {single_point_count} appear at exactly one point ({pct:.1f}%)."

    if single_point_count > (unique_bssids / 2.0):
        print(f"   [WARN] {summary_str} More than half appear at only one point (likely phones, mobile hotspots, or transient neighbors).")
        return 0, 1

    print(f"   [PASS] {summary_str}")
    return 0, 0


# ==============================================================================
# Runner & Synthetic In-Memory Mock Database
# ==============================================================================

def run_all_checks(
    fingerprints: List[Dict[str, Any]],
    floors: List[Dict[str, Any]],
    svg_width: float,
    svg_height: float,
    max_gap: float,
    min_aps: int,
    min_rssi: int = -100,
) -> Tuple[int, int]:
    """Runs all 9 checks and prints standard headings and summary."""
    # Group points by floor_id
    points_by_floor: Dict[Any, List[Dict[str, Any]]] = {}
    for p in fingerprints:
        fid = p["floor_id"]
        if fid not in points_by_floor:
            points_by_floor[fid] = []
        points_by_floor[fid].append(p)

    total_fails = 0
    total_warns = 0

    c1_f, c1_w = check_1_coverage_per_floor(floors, points_by_floor)
    c2_f, c2_w = check_2_aps_per_point(fingerprints, min_aps)
    c3_f, c3_w = check_3_coordinates_bounds(fingerprints, svg_width, svg_height)
    c4_f, c4_w = check_4_rssi_sanity(fingerprints, min_rssi=min_rssi)
    c5_f, c5_w = check_5_duplicate_locations(points_by_floor)
    c6_f, c6_w = check_6_coverage_gaps(points_by_floor, max_gap)
    c7_f, c7_w = check_7_neighbor_consistency(points_by_floor)
    c8_f, c8_w = check_8_floor_separation(points_by_floor, max_gap)
    c9_f, c9_w = check_9_ap_reliability(fingerprints)

    total_fails = c1_f + c2_f + c3_f + c4_f + c5_f + c6_f + c7_f + c8_f + c9_f
    total_warns = c1_w + c2_w + c3_w + c4_w + c5_w + c6_w + c7_w + c8_w + c9_w

    print(f"\nSummary: {total_fails} FAIL, {total_warns} WARN")
    if total_fails > 0:
        print("Fix the FAIL items before building positioning.")
    elif total_warns > 0:
        print("Review the WARN items; the data is usable but could be improved.")
    else:
        print("Data looks good. Safe to move on to the matching step.")

    return total_fails, total_warns


def build_synthetic_database(with_mistakes: bool = True):
    """
    Builds an in-memory synthetic database using mongomock with:
    - 2 floors in building 'SOT'
    - 20 points per floor arranged on a clean grid (4 columns x 5 rows)
    - 6 simulated APs whose RSSI falls off with distance (-12 dB on floor 1 + small noise)
    - If with_mistakes=True, adds four deliberate mistakes:
        1. a point with x outside the SVG bounds (check 3 FAIL)
        2. a point with only 1 AP (check 2 WARN)
        3. a reading with rssi = +10 (check 4 FAIL)
        4. a point at one location whose scan was taken far away, clashing with neighbors (check 7 WARN)
    """
    import random
    import mongomock

    client = mongomock.MongoClient()
    db = client["wifi_fingerprints"]

    # 1. Buildings & floors
    db.buildings.insert_many([
        {"id": 1, "name": "SOT", "created_at": "2026-10-14T00:00:00Z"},
    ])
    db.floors.insert_many([
        {"id": 1, "building_id": 1, "building_name": "SOT", "floor_number": 0, "label": "Floor 0 -- Ground floor"},
        {"id": 2, "building_id": 1, "building_name": "SOT", "floor_number": 1, "label": "Floor 1 -- First floor"},
    ])

    # 6 simulated AP positions
    ap_list = [
        ("00:11:22:33:44:01", "Campus-AP-1", 500.0, 150.0),
        ("00:11:22:33:44:02", "Campus-AP-2", 950.0, 150.0),
        ("00:11:22:33:44:03", "Campus-AP-3", 500.0, 550.0),
        ("00:11:22:33:44:04", "Campus-AP-4", 950.0, 550.0),
        ("00:11:22:33:44:05", "Campus-AP-5", 750.0, 350.0),
        ("00:11:22:33:44:06", "Campus-AP-6", 750.0, 200.0),
    ]

    rng = random.Random(42)

    def generate_readings(x: float, y: float, floor_penalty: float = 0.0):
        readings = []
        for bssid, ssid, ax, ay in ap_list:
            dist = math.hypot(x - ax, y - ay)
            # Log-distance path loss model
            rssi = -35.0 - 20.0 * math.log10(max(1.0, dist / 20.0)) - floor_penalty + rng.uniform(-0.5, 0.5)
            readings.append({"bssid": bssid, "ssid": ssid, "rssi": int(round(rssi))})
        return readings

    # 20 points per floor on a 4-col x 5-row grid
    # dx = 100 units, dy = 70 units (both well below default max-gap 150)
    grid_x = [600.0, 700.0, 800.0, 900.0]
    grid_y = [200.0, 270.0, 340.0, 410.0, 480.0]

    fps = []
    # Floor 0
    for x in grid_x:
        for y in grid_y:
            fps.append({
                "floor_id": 1,
                "building_name": "SOT",
                "x": x,
                "y": y,
                "readings": generate_readings(x, y, floor_penalty=0.0),
            })

    # Floor 1 (-12 dB attenuation)
    for x in grid_x:
        for y in grid_y:
            fps.append({
                "floor_id": 2,
                "building_name": "SOT",
                "x": x,
                "y": y,
                "readings": generate_readings(x, y, floor_penalty=12.0),
            })

    if with_mistakes:
        # Deliberate Mistake 1: a point with x outside SVG bounds [0, 1000]
        # Shift the point at (900, 200) on Floor 0 to x=1020.
        # Its nearest neighbor is (900, 270) -> distance = hypot(120, 70) = 138.9 <= 150 (not isolated).
        fps[15]["x"] = 1020.0
        fps[15]["readings"] = generate_readings(1020.0, 200.0, floor_penalty=0.0)

        # Deliberate Mistake 2: a point with only 1 AP (check 2)
        fps[1]["readings"] = fps[1]["readings"][:1]

        # Deliberate Mistake 3: a reading with rssi = +10 (check 4)
        fps[25]["readings"][0]["rssi"] = 10

        # Deliberate Mistake 4: a point at (700, 410) whose scan was taken far away at (100, 100)
        # Clashes with spatial neighbors on Floor 0 (check 7)
        fps[8]["readings"] = generate_readings(100.0, 100.0, floor_penalty=0.0)

    db.fingerprints.insert_many(fps)
    return db



# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Audit WiFi fingerprint database stored in MongoDB Atlas (strictly read-only)."
    )
    default_db_name = os.environ.get("MONGODB_DB_NAME", "wifi_fingerprints")
    parser.add_argument("--db-name", default=default_db_name, help=f"MongoDB database name (default: {default_db_name})")
    parser.add_argument("--svg-width", type=float, default=1000.0, help="Maximum allowed SVG width (default: 1000)")
    parser.add_argument("--svg-height", type=float, default=800.0, help="Maximum allowed SVG height (default: 800)")
    parser.add_argument("--max-gap", type=float, default=150.0, help="Maximum allowed gap between points (default: 150)")
    parser.add_argument("--min-aps", type=int, default=4, help="Minimum APs required per point (default: 4)")
    parser.add_argument("--ssid", default=None, help="Filter readings to exact SSID (whitespace trimmed); drop empty fingerprints")
    parser.add_argument("--min-rssi", type=int, default=-100, help="Minimum valid RSSI in dBm (default: -100)")
    parser.add_argument("--uri", default=None, help="MongoDB connection URI (default: from MONGODB_URI env)")
    parser.add_argument("--mock", action="store_true", help="Run against synthetic test database with 4 deliberate mistakes")
    parser.add_argument("--mock-clean", action="store_true", help="Run against clean synthetic test database")

    args = parser.parse_args()

    # Determine database source
    if args.mock:
        db = build_synthetic_database(with_mistakes=True)
    elif args.mock_clean:
        db = build_synthetic_database(with_mistakes=False)
    else:
        uri = args.uri or os.environ.get("MONGODB_URI")
        db = connect_to_database(uri=uri, db_name=args.db_name, timeout_ms=5000)

    fingerprints, floors = load_dataset(db, config=CONFIG)

    if args.ssid is not None:
        target_ssid = args.ssid.strip()
        print(f"Filtering to SSID: {target_ssid}")
        fingerprints, dropped_count = filter_by_ssid(fingerprints, target_ssid)
        print(f"Dropped {dropped_count} fingerprint(s) with zero readings.")

    if not fingerprints:
        print("No data to validate.")
        sys.exit(1)

    # Number of unique floors represented in the loaded dataset
    distinct_floor_ids = set(p["floor_id"] for p in fingerprints)
    num_floors = len(distinct_floor_ids)
    print(f"Loaded {len(fingerprints)} fingerprints across {num_floors} floor(s)\n")

    fails, warns = run_all_checks(
        fingerprints=fingerprints,
        floors=floors,
        svg_width=args.svg_width,
        svg_height=args.svg_height,
        max_gap=args.max_gap,
        min_aps=args.min_aps,
        min_rssi=args.min_rssi,
    )

    sys.exit(1 if fails > 0 else 0)


if __name__ == "__main__":
    main()

