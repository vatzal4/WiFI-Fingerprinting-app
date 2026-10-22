"""
Core WiFi Fingerprint Matching & Data Loading Engine
=====================================================
Pure positioning matcher functions (clean_readings, rssi_distance, locate)
and MongoDB read-only loader (load_fingerprints).
"""

import os
import math
from typing import Dict, List, Tuple, Any, Optional

# Constants
SSID = "Student"
K = 3
MISSING_RSSI = -100
DEFAULT_BUILDING = "SOS"
UNITS_PER_METER = 18.7

import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Import database schema configuration from validate_data
try:
    from validate_data import CONFIG
except ImportError:
    try:
        from .validate_data import CONFIG
    except ImportError:
        CONFIG = {
            "COLLECTIONS": {
                "BUILDINGS": "buildings",
                "FLOORS": "floors",
                "FINGERPRINTS": "fingerprints",
                "READINGS": None,
            },
            "FIELDS": {
                "BUILDING_ID": "id",
                "BUILDING_NAME": "name",
                "FLOOR_ID": "id",
                "FLOOR_BUILDING_ID": "building_id",
                "FLOOR_BUILDING_NAME": "building_name",
                "FLOOR_NUMBER": "floor_number",
                "FLOOR_LABEL": "label",
                "FP_ID": "_id",
                "FP_FLOOR_ID": "floor_id",
                "FP_X": "x",
                "FP_Y": "y",
                "FP_NODE_ID": "node_id",
                "FP_CAPTURED_AT": "captured_at",
                "FP_READINGS": "readings",
                "READING_FP_ID": "fingerprint_id",
                "READING_BSSID": "bssid",
                "READING_SSID": "ssid",
                "READING_RSSI": "rssi",
            },
        }


# ==============================================================================
# Pure Functions (Unit-testable with no database calls)
# ==============================================================================

def filter_scan(
    scan: Dict[str, float],
    min_rssi_cutoff: Optional[float] = None,
    top_n: Optional[int] = None,
) -> Dict[str, float]:
    """Filters a {bssid: rssi} scan by min_rssi_cutoff and top_n strongest APs."""
    if not scan:
        return {}
    res = scan
    if min_rssi_cutoff is not None:
        res = {b: r for b, r in res.items() if r >= min_rssi_cutoff}
    if top_n is not None and len(res) > top_n:
        sorted_aps = sorted(res.items(), key=lambda item: item[1], reverse=True)[:top_n]
        res = dict(sorted_aps)
    return res


def clean_readings(
    readings: Any,
    ssid: str = SSID,
    min_rssi: float = MISSING_RSSI,
    min_rssi_cutoff: Optional[float] = None,
    top_n: Optional[int] = None,
) -> Dict[str, float]:
    """
    Keeps only readings whose ssid, after trimming whitespace, equals ssid (case-insensitive)
    and whose rssi > min_rssi.
    If min_rssi_cutoff is specified, ignores readings weaker than this (rssi < min_rssi_cutoff).
    If top_n is specified, keeps only the N strongest APs.
    Returns a dict {bssid: rssi}. If a bssid repeats, keeps the strongest (maximum) RSSI.
    """
    if not readings or not isinstance(readings, (list, tuple)):
        return {}

    target_ssid = ssid.strip() if ssid else ""
    cleaned: Dict[str, float] = {}

    for r in readings:
        if not isinstance(r, dict):
            continue

        raw_ssid = r.get("ssid")
        trimmed_ssid = str(raw_ssid).strip() if raw_ssid is not None else ""
        if trimmed_ssid.lower() != target_ssid.lower():
            continue

        raw_rssi = r.get("rssi")
        if raw_rssi is None:
            continue

        try:
            rssi = float(raw_rssi)
        except (ValueError, TypeError):
            continue

        # Keep only readings with rssi > min_rssi
        if rssi <= min_rssi:
            continue

        # Ignore readings weaker than min_rssi_cutoff
        if min_rssi_cutoff is not None and rssi < min_rssi_cutoff:
            continue

        raw_bssid = r.get("bssid")
        if not raw_bssid:
            continue
        bssid = str(raw_bssid).strip().lower()
        if not bssid:
            continue

        # If bssid repeats, keep the strongest (maximum RSSI)
        if bssid not in cleaned or rssi > cleaned[bssid]:
            cleaned[bssid] = rssi

    if top_n is not None and len(cleaned) > top_n:
        sorted_aps = sorted(cleaned.items(), key=lambda item: item[1], reverse=True)[:top_n]
        cleaned = dict(sorted_aps)

    return cleaned


def rssi_distance(
    a: Dict[str, float],
    b: Dict[str, float],
    missing_rssi: float = MISSING_RSSI,
) -> float:
    """
    Computes Euclidean RSSI distance between two scans.
    Union of the two bssid sets; a missing AP counts as missing_rssi (default: -100).
    Returns sqrt(sum of squared differences / size of union).
    Returns math.inf if the union is empty.
    """
    union = set(a.keys()) | set(b.keys())
    if not union:
        return math.inf

    total_sq_diff = sum(
        (a.get(bssid, float(missing_rssi)) - b.get(bssid, float(missing_rssi))) ** 2
        for bssid in union
    )
    return math.sqrt(total_sq_diff / len(union))


def locate(
    live_scan: Dict[str, float],
    fingerprints: List[Dict[str, Any]],
    k: int = K,
    missing_rssi: float = MISSING_RSSI,
    floor_mode: str = "vote",
    min_rssi_cutoff: Optional[float] = None,
    top_n: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Estimates indoor position using k-Nearest Neighbors with inverse-distance weighting.

    Arguments:
        live_scan: cleaned {bssid: rssi} dict.
        fingerprints: list of {id, floor_id, floor_number, x, y, readings: {bssid: rssi}}
                      (readings already cleaned).
        k: number of nearest neighbors to use (default: 3).
        missing_rssi: RSSI penalty for an AP missing from one scan (default: -100).
        floor_mode: "vote" (current behavior), "floor_first", or "strongest_ap".
        min_rssi_cutoff: ignore readings weaker than this (default: None).
        top_n: keep only the N strongest APs of each scan (default: None).

    Returns:
        {x, y, floor_id, floor_number, confidence, neighbors: [{id, distance}]}
        where confidence is the winning floor's share of total vote weight (0 to 1).
        Returns None if live_scan is empty or there are no fingerprints.
    """
    if not live_scan or not fingerprints:
        return None

    # Filter live scan if cutoff or top_n specified
    active_live = filter_scan(live_scan, min_rssi_cutoff, top_n)
    if not active_live:
        return None

    # Pre-filter fingerprints if cutoff or top_n specified
    active_fps = []
    for fp in fingerprints:
        rd = fp.get("readings", {})
        if min_rssi_cutoff is not None or top_n is not None:
            rd = filter_scan(rd, min_rssi_cutoff, top_n)
            if not rd:
                continue
        active_fps.append({
            "id": fp.get("id"),
            "floor_id": fp.get("floor_id"),
            "floor_number": fp.get("floor_number"),
            "x": float(fp.get("x", 0.0)),
            "y": float(fp.get("y", 0.0)),
            "readings": rd,
        })

    if not active_fps:
        return None

    # --------------------------------------------------------------------------
    # MODE 1: "vote" (default weighted voting across k nearest neighbors)
    # --------------------------------------------------------------------------
    if floor_mode == "vote":
        scored = []
        for fp in active_fps:
            dist = rssi_distance(active_live, fp["readings"], missing_rssi=missing_rssi)
            scored.append((dist, fp))

        scored.sort(key=lambda item: item[0])
        k_nearest = scored[:k]
        if not k_nearest:
            return None

        neighbors_info = []
        total_vote_weight = 0.0
        floor_votes: Dict[Tuple[Any, Any], float] = {}

        for dist, fp in k_nearest:
            weight = 1.0 / (dist + 1e-6)
            total_vote_weight += weight
            fl_key = (fp.get("floor_id"), fp.get("floor_number"))
            floor_votes[fl_key] = floor_votes.get(fl_key, 0.0) + weight

            neighbors_info.append({
                "id": fp.get("id"),
                "distance": dist,
                "floor_id": fp.get("floor_id"),
                "floor_number": fp.get("floor_number"),
                "x": fp["x"],
                "y": fp["y"],
                "weight": weight,
            })

        winning_fl_key = max(floor_votes.keys(), key=lambda fl: floor_votes[fl])
        winning_floor_id, winning_floor_number = winning_fl_key
        winning_floor_weight = floor_votes[winning_fl_key]

        confidence = (
            winning_floor_weight / total_vote_weight if total_vote_weight > 0 else 1.0
        )

        winning_neighbors = [
            n for n in neighbors_info
            if (n["floor_id"], n["floor_number"]) == winning_fl_key
        ]

        zero_dist_neighbors = [n for n in winning_neighbors if n["distance"] == 0.0]
        if zero_dist_neighbors:
            est_x = sum(n["x"] for n in zero_dist_neighbors) / len(zero_dist_neighbors)
            est_y = sum(n["y"] for n in zero_dist_neighbors) / len(zero_dist_neighbors)
        else:
            win_weight_sum = sum(n["weight"] for n in winning_neighbors)
            if win_weight_sum > 0:
                est_x = sum(n["x"] * n["weight"] for n in winning_neighbors) / win_weight_sum
                est_y = sum(n["y"] * n["weight"] for n in winning_neighbors) / win_weight_sum
            else:
                est_x = winning_neighbors[0]["x"]
                est_y = winning_neighbors[0]["y"]

        return {
            "x": est_x,
            "y": est_y,
            "floor_id": winning_floor_id,
            "floor_number": winning_floor_number,
            "confidence": confidence,
            "neighbors": [
                {"id": n["id"], "distance": n["distance"]}
                for n in neighbors_info
            ],
        }

    # --------------------------------------------------------------------------
    # MODE 2: "floor_first"
    # For each floor, take the mean distance of its k nearest fingerprints;
    # pick the floor with the smallest, then compute x/y only from that floor.
    # --------------------------------------------------------------------------
    elif floor_mode == "floor_first":
        floors_map: Dict[Tuple[Any, Any], List[Tuple[float, Dict[str, Any]]]] = {}
        for fp in active_fps:
            dist = rssi_distance(active_live, fp["readings"], missing_rssi=missing_rssi)
            fl_key = (fp.get("floor_id"), fp.get("floor_number"))
            floors_map.setdefault(fl_key, []).append((dist, fp))

        floor_summaries: Dict[Tuple[Any, Any], Tuple[float, List[Tuple[float, Dict[str, Any]]]]] = {}
        for fl_key, fp_list in floors_map.items():
            fp_list.sort(key=lambda item: item[0])
            k_fl = fp_list[:k]
            mean_dist = sum(d for d, _ in k_fl) / len(k_fl)
            floor_summaries[fl_key] = (mean_dist, k_fl)

        if not floor_summaries:
            return None

        winning_fl_key = min(floor_summaries.keys(), key=lambda fl: floor_summaries[fl][0])
        winning_floor_id, winning_floor_number = winning_fl_key
        win_mean_dist, win_k_nearest = floor_summaries[winning_fl_key]

        # Confidence: winning floor's inverse mean distance over total
        inv_weights = {fl: 1.0 / (d + 1e-6) for fl, (d, _) in floor_summaries.items()}
        total_inv = sum(inv_weights.values())
        confidence = (inv_weights[winning_fl_key] / total_inv) if total_inv > 0 else 1.0

        zero_dist = [fp for d, fp in win_k_nearest if d == 0.0]
        if zero_dist:
            est_x = sum(fp["x"] for fp in zero_dist) / len(zero_dist)
            est_y = sum(fp["y"] for fp in zero_dist) / len(zero_dist)
        else:
            weights = [1.0 / (d + 1e-6) for d, _ in win_k_nearest]
            w_sum = sum(weights)
            if w_sum > 0:
                est_x = sum(fp["x"] * w for (d, fp), w in zip(win_k_nearest, weights)) / w_sum
                est_y = sum(fp["y"] * w for (d, fp), w in zip(win_k_nearest, weights)) / w_sum
            else:
                est_x = win_k_nearest[0][1]["x"]
                est_y = win_k_nearest[0][1]["y"]

        return {
            "x": est_x,
            "y": est_y,
            "floor_id": winning_floor_id,
            "floor_number": winning_floor_number,
            "confidence": confidence,
            "neighbors": [
                {"id": fp["id"], "distance": d}
                for d, fp in win_k_nearest
            ],
        }

    # --------------------------------------------------------------------------
    # MODE 3: "strongest_ap"
    # Stored fingerprints' 3 strongest APs build bssid -> floor count map.
    # Live scan votes for a floor using its 3 strongest BSSIDs, then x/y from that floor.
    # --------------------------------------------------------------------------
    elif floor_mode == "strongest_ap":
        # Build bssid -> floor count map
        bssid_floor_map: Dict[str, Dict[Tuple[Any, Any], int]] = {}
        for fp in active_fps:
            fl_key = (fp.get("floor_id"), fp.get("floor_number"))
            top3_fp = sorted(fp["readings"].items(), key=lambda item: item[1], reverse=True)[:3]
            for bssid, _ in top3_fp:
                if bssid not in bssid_floor_map:
                    bssid_floor_map[bssid] = {}
                bssid_floor_map[bssid][fl_key] = bssid_floor_map[bssid].get(fl_key, 0) + 1

        # Live scan votes with its 3 strongest BSSIDs
        top3_live = sorted(active_live.items(), key=lambda item: item[1], reverse=True)[:3]
        floor_votes: Dict[Tuple[Any, Any], int] = {}
        for bssid, _ in top3_live:
            if bssid in bssid_floor_map:
                for fl_key, cnt in bssid_floor_map[bssid].items():
                    floor_votes[fl_key] = floor_votes.get(fl_key, 0) + cnt

        if floor_votes:
            winning_fl_key = max(floor_votes.keys(), key=lambda fl: floor_votes[fl])
            winning_floor_id, winning_floor_number = winning_fl_key
            tot_votes = sum(floor_votes.values())
            confidence = (floor_votes[winning_fl_key] / tot_votes) if tot_votes > 0 else 1.0
        else:
            # Fallback to vote mode if no AP overlap in top 3
            return locate(
                active_live,
                active_fps,
                k=k,
                missing_rssi=missing_rssi,
                floor_mode="vote",
            )

        # Compute x and y strictly from winning floor's k nearest neighbors
        fps_on_floor = [
            fp for fp in active_fps
            if (fp.get("floor_id"), fp.get("floor_number")) == winning_fl_key
        ]
        if not fps_on_floor:
            return None

        scored = [
            (rssi_distance(active_live, fp["readings"], missing_rssi=missing_rssi), fp)
            for fp in fps_on_floor
        ]
        scored.sort(key=lambda item: item[0])
        k_nearest = scored[:k]

        zero_dist = [fp for d, fp in k_nearest if d == 0.0]
        if zero_dist:
            est_x = sum(fp["x"] for fp in zero_dist) / len(zero_dist)
            est_y = sum(fp["y"] for fp in zero_dist) / len(zero_dist)
        else:
            weights = [1.0 / (d + 1e-6) for d, _ in k_nearest]
            w_sum = sum(weights)
            if w_sum > 0:
                est_x = sum(fp["x"] * w for (d, fp), w in zip(k_nearest, weights)) / w_sum
                est_y = sum(fp["y"] * w for (d, fp), w in zip(k_nearest, weights)) / w_sum
            else:
                est_x = k_nearest[0][1]["x"]
                est_y = k_nearest[0][1]["y"]

        return {
            "x": est_x,
            "y": est_y,
            "floor_id": winning_floor_id,
            "floor_number": winning_floor_number,
            "confidence": confidence,
            "neighbors": [
                {"id": fp["id"], "distance": d}
                for d, fp in k_nearest
            ],
        }

    else:
        raise ValueError(f"Unknown floor_mode: '{floor_mode}'. Expected 'vote', 'floor_first', or 'strongest_ap'.")


# ==============================================================================
# Read-Only Database Loader
# ==============================================================================

def get_read_only_mongo_db(uri: Optional[str] = None, db_name: Optional[str] = None):
    """
    Connects to MongoDB Atlas strictly read-only with secondaryPreferred preference.
    """
    import pymongo

    # Load environment variables if available
    try:
        from dotenv import load_dotenv
        _dir = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(_dir, ".env"))
        load_dotenv(os.path.join(_dir, "..", ".env"))
    except ImportError:
        pass

    target_uri = uri or os.environ.get("MONGODB_URI")
    if not target_uri:
        try:
            import database
            target_uri = getattr(database, "DEFAULT_MONGO_URI", None)
        except Exception:
            pass

    if not target_uri:
        raise ValueError("MONGODB_URI is not set and no default connection URI was found.")

    target_db_name = db_name or os.environ.get("MONGODB_DB_NAME", "wifi_fingerprints")

    client = pymongo.MongoClient(
        target_uri,
        serverSelectionTimeoutMS=5000,
        readPreference="secondaryPreferred",
    )
    return client[target_db_name]


def load_fingerprints(
    building_name: str = DEFAULT_BUILDING,
    db: Any = None,
    ssid: str = SSID,
    min_rssi: float = MISSING_RSSI,
    min_rssi_cutoff: Optional[float] = None,
    top_n: Optional[int] = None,
    config: Dict[str, Any] = CONFIG,
) -> List[Dict[str, Any]]:
    """
    Reads fingerprints from MongoDB (strictly read-only), keeps only fingerprints
    on floors belonging to the given building, applies clean_readings to each,
    drops fingerprints left with no readings, and returns the list in the shape
    locate() expects:
        list of {id, floor_id, floor_number, x, y, readings: {bssid: rssi}}
    """
    if db is None:
        db = get_read_only_mongo_db()

    cols = config["COLLECTIONS"]
    fields = config["FIELDS"]

    target_building = building_name.strip()

    # 1. Load buildings to identify matching building IDs
    buildings_cursor = db[cols["BUILDINGS"]].find()
    matching_building_ids = set()
    for b in buildings_cursor:
        b_name = str(b.get(fields["BUILDING_NAME"], "")).strip()
        b_id = b.get(fields["BUILDING_ID"], b.get("_id"))
        if b_name.lower() == target_building.lower():
            matching_building_ids.add(b_id)
            matching_building_ids.add(str(b_id))
            if "_id" in b:
                matching_building_ids.add(str(b["_id"]))

    # 2. Load floors and build lookup for target building
    floors_cursor = db[cols["FLOORS"]].find()
    floor_lookup = {}
    valid_floor_ids = set()

    for f in floors_cursor:
        f_id = f.get(fields["FLOOR_ID"], f.get("_id"))
        f_oid = str(f.get("_id")) if "_id" in f else str(f_id)
        b_id = f.get(fields["FLOOR_BUILDING_ID"])
        b_name = str(f.get(fields["FLOOR_BUILDING_NAME"], "")).strip()
        floor_num = f.get(fields["FLOOR_NUMBER"], 0)

        belongs_to_building = (
            (b_id is not None and (b_id in matching_building_ids or str(b_id) in matching_building_ids))
            or (b_name.lower() == target_building.lower())
        )

        if belongs_to_building:
            floor_data = {
                "floor_id": f_id,
                "floor_number": int(floor_num) if floor_num is not None else 0,
            }
            floor_lookup[f_id] = floor_data
            floor_lookup[str(f_id)] = floor_data
            floor_lookup[f_oid] = floor_data
            valid_floor_ids.add(f_id)
            valid_floor_ids.add(str(f_id))
            valid_floor_ids.add(f_oid)

    # 3. Load separate readings if configured
    separate_readings_map: Dict[Any, List[Dict[str, Any]]] = {}
    if cols.get("READINGS"):
        readings_cursor = db[cols["READINGS"]].find()
        for r in readings_cursor:
            fp_id = r.get(fields["READING_FP_ID"])
            if fp_id not in separate_readings_map:
                separate_readings_map[fp_id] = []
            separate_readings_map[fp_id].append(r)

    # 4. Load fingerprints and filter by building/floors
    fp_cursor = db[cols["FINGERPRINTS"]].find()
    fingerprints = []

    for doc in fp_cursor:
        fp_id = str(doc.get(fields["FP_ID"], ""))
        fl_id = doc.get(fields["FP_FLOOR_ID"])
        doc_building = str(doc.get("building_name", "")).strip()

        # Check if floor belongs to target building
        belongs = False
        fl_entry = floor_lookup.get(fl_id) or floor_lookup.get(str(fl_id))

        if fl_entry is not None:
            belongs = True
            floor_id = fl_entry["floor_id"]
            floor_number = fl_entry["floor_number"]
        elif not valid_floor_ids and doc_building.lower() == target_building.lower():
            # If floors collection didn't have entries, fallback to fingerprint document's building_name
            belongs = True
            floor_id = fl_id
            floor_number = int(doc.get(fields["FLOOR_NUMBER"], 0))
        elif doc_building.lower() == target_building.lower():
            belongs = True
            floor_id = fl_id
            floor_number = int(doc.get(fields["FLOOR_NUMBER"], 0))

        if not belongs:
            continue

        # Extract readings
        if cols.get("READINGS"):
            raw_readings = separate_readings_map.get(doc.get(fields["FP_ID"]), [])
            if not raw_readings:
                raw_readings = separate_readings_map.get(fp_id, [])
        else:
            raw_readings = doc.get(fields["FP_READINGS"], [])

        # Clean readings for target SSID and RSSI threshold
        cleaned = clean_readings(
            raw_readings,
            ssid=ssid,
            min_rssi=min_rssi,
            min_rssi_cutoff=min_rssi_cutoff,
            top_n=top_n,
        )

        # Drop fingerprints left with no readings
        if not cleaned:
            continue

        try:
            x_coord = float(doc.get(fields["FP_X"], 0.0))
            y_coord = float(doc.get(fields["FP_Y"], 0.0))
        except (ValueError, TypeError):
            continue

        fingerprints.append({
            "id": fp_id,
            "floor_id": floor_id,
            "floor_number": floor_number,
            "x": x_coord,
            "y": y_coord,
            "readings": cleaned,
        })

    return fingerprints
