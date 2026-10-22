"""
Leave-One-Out Positioning Accuracy Evaluation & Diagnostics Script
===================================================================
Evaluates WiFi fingerprint k-NN matching accuracy using leave-one-out cross-validation.
Includes diagnostic tools (--diagnose), hyperparameter grid search tuning (--tune),
and floor-known accuracy evaluation (--floor-known).

Usage:
    python evaluate_matching.py [--building SOS] [--ssid Student] [--k 3]
                                [--units-per-meter 18.7] [--exclude-radius 0]
                                [--sweep] [--diagnose] [--tune] [--floor-known]
                                [--min-rssi-cutoff CUTOFF] [--top-n N]
                                [--missing-rssi VAL] [--floor-mode MODE]

All defaults come from constants in locator.py. Strictly read-only on MongoDB.
"""

import os
import sys
import math
import argparse
import itertools
import statistics
from collections import Counter
from typing import Dict, List, Tuple, Any, Optional

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure script directory is in sys.path for seamless imports
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Import locator functions and constants
from locator import (
    load_fingerprints,
    locate,
    clean_readings,
    filter_scan,
    rssi_distance,
    DEFAULT_BUILDING,
    SSID,
    K,
    MISSING_RSSI,
    UNITS_PER_METER,
)


def calculate_percentile(sorted_values: List[float], percentile: float) -> float:
    """
    Computes percentile (e.g. 0.90 for 90th percentile) using linear interpolation.
    sorted_values must be non-empty and sorted in ascending order.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]

    idx = percentile * (len(sorted_values) - 1)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return sorted_values[lower]
    weight = idx - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def summarize_errors(errors: List[float]) -> Dict[str, float]:
    """Returns mean, median, and 90th percentile for a list of error values."""
    if not errors:
        return {"mean": 0.0, "median": 0.0, "p90": 0.0, "count": 0}
    sorted_errs = sorted(errors)
    return {
        "mean": statistics.mean(sorted_errs),
        "median": statistics.median(sorted_errs),
        "p90": calculate_percentile(sorted_errs, 0.90),
        "count": len(sorted_errs),
    }


def evaluate_leave_one_out(
    fingerprints: List[Dict[str, Any]],
    k: int = K,
    units_per_meter: float = UNITS_PER_METER,
    exclude_radius: float = 0.0,
    missing_rssi: float = MISSING_RSSI,
    floor_mode: str = "vote",
    min_rssi_cutoff: Optional[float] = None,
    top_n: Optional[int] = None,
    floor_known: bool = False,
) -> Dict[str, Any]:
    """
    Runs leave-one-out evaluation for all fingerprints in the dataset.

    For every fingerprint:
      1. Remove it from the list.
      2. If floor_known is True, restrict candidates strictly to the true floor.
      3. If exclude_radius > 0, also remove same-floor neighbors within exclude_radius.
      4. Run locate() using its own readings.
      5. Compare estimated position/floor to ground truth.

    Returns dict containing evaluation metrics, per-floor breakdowns, and point records.
    """
    total_points = len(fingerprints)
    if total_points == 0:
        return {
            "total_points": 0,
            "floor_accuracy": 0.0,
            "floor_correct_count": 0,
            "overall_units": summarize_errors([]),
            "overall_meters": summarize_errors([]),
            "per_floor": {},
            "worst_points": [],
            "point_records": [],
            "confusion_matrix": {},
        }

    floor_totals: Dict[str, int] = {}
    floor_corrects: Dict[str, int] = {}
    floor_errors_units: Dict[str, List[float]] = {}
    floor_errors_meters: Dict[str, List[float]] = {}

    all_errors_units: List[float] = []
    all_errors_meters: List[float] = []
    point_records: List[Dict[str, Any]] = []
    confusion_matrix: Dict[str, Dict[str, int]] = {}

    for i, test_fp in enumerate(fingerprints):
        true_x = test_fp["x"]
        true_y = test_fp["y"]
        true_floor_id = test_fp["floor_id"]
        true_floor_num = test_fp.get("floor_number", 0)
        floor_label = f"Floor {true_floor_num}"

        floor_totals[floor_label] = floor_totals.get(floor_label, 0) + 1

        # Build candidate neighbor pool
        candidates = []
        for j, other_fp in enumerate(fingerprints):
            if j == i:
                continue

            other_fl_num = other_fp.get("floor_number", 0)
            other_fl_id = other_fp.get("floor_id")
            same_floor = (other_fl_id == true_floor_id or other_fl_num == true_floor_num)

            # If floor is known, only keep candidates on the same floor
            if floor_known and not same_floor:
                continue

            # If exclude_radius > 0, exclude same-floor neighbors within radius
            if exclude_radius > 0 and same_floor:
                dist_xy = math.hypot(other_fp["x"] - true_x, other_fp["y"] - true_y)
                if dist_xy <= exclude_radius:
                    continue

            candidates.append(other_fp)

        # Run locator
        result = locate(
            live_scan=test_fp["readings"],
            fingerprints=candidates,
            k=k,
            missing_rssi=missing_rssi,
            floor_mode=floor_mode,
            min_rssi_cutoff=min_rssi_cutoff,
            top_n=top_n,
        )

        if result is None:
            floor_correct = False
            est_x, est_y = None, None
            est_floor_label = "None"
            error_units = math.inf
            error_meters = math.inf
        else:
            est_x = result["x"]
            est_y = result["y"]
            est_floor_num = result.get("floor_number", 0)
            est_floor_id = result.get("floor_id")
            est_floor_label = f"Floor {est_floor_num}"

            if floor_known:
                floor_correct = True
            else:
                floor_correct = (
                    est_floor_num == true_floor_num or est_floor_id == true_floor_id
                )

            error_units = math.hypot(est_x - true_x, est_y - true_y)
            error_meters = error_units / units_per_meter

        # Update confusion matrix
        confusion_matrix.setdefault(floor_label, {})
        confusion_matrix[floor_label][est_floor_label] = (
            confusion_matrix[floor_label].get(est_floor_label, 0) + 1
        )

        if floor_correct:
            floor_corrects[floor_label] = floor_corrects.get(floor_label, 0) + 1
            all_errors_units.append(error_units)
            all_errors_meters.append(error_meters)
            floor_errors_units.setdefault(floor_label, []).append(error_units)
            floor_errors_meters.setdefault(floor_label, []).append(error_meters)

        point_records.append({
            "id": test_fp["id"],
            "floor": floor_label,
            "true_x": true_x,
            "true_y": true_y,
            "est_x": est_x,
            "est_y": est_y,
            "est_floor": est_floor_label,
            "floor_correct": floor_correct,
            "error_units": error_units,
            "error_meters": error_meters,
        })

    # Overall floor accuracy
    total_floor_correct = sum(floor_corrects.values())
    overall_floor_accuracy = (total_floor_correct / total_points) * 100.0 if total_points > 0 else 0.0

    # Per-floor stats
    per_floor_stats = {}
    for fl_name in sorted(floor_totals.keys()):
        tot = floor_totals[fl_name]
        cor = floor_corrects.get(fl_name, 0)
        acc = (cor / tot) * 100.0 if tot > 0 else 0.0
        per_floor_stats[fl_name] = {
            "total": tot,
            "correct": cor,
            "accuracy": acc,
            "units": summarize_errors(floor_errors_units.get(fl_name, [])),
            "meters": summarize_errors(floor_errors_meters.get(fl_name, [])),
        }

    # Find 5 worst points (floor mistakes ranked as worst errors, then highest error_meters)
    def worst_sort_key(item: Dict[str, Any]):
        return (not item["floor_correct"], item["error_meters"])

    worst_5 = sorted(point_records, key=worst_sort_key, reverse=True)[:5]

    return {
        "total_points": total_points,
        "floor_accuracy": overall_floor_accuracy,
        "floor_correct_count": total_floor_correct,
        "overall_units": summarize_errors(all_errors_units),
        "overall_meters": summarize_errors(all_errors_meters),
        "per_floor": per_floor_stats,
        "worst_points": worst_5,
        "point_records": point_records,
        "confusion_matrix": confusion_matrix,
    }


def get_verdict(floor_accuracy: float, median_error_meters: float) -> Tuple[str, str]:
    """Generates the required one-line verdict and recommendation hint."""
    is_good = (floor_accuracy >= 95.0) and (median_error_meters <= 5.0)

    if is_good:
        status = "GOOD"
        hint = "Positioning model meets production accuracy targets."
    else:
        status = "NEEDS WORK"
        if floor_accuracy < 95.0 and median_error_meters > 5.0:
            hint = "Improve survey density across floors and add filtering for volatile or transient access points."
        elif floor_accuracy < 95.0:
            hint = "Floor classification accuracy is below 95%. Consider surveying points near stairwells/elevators or adjusting min_rssi."
        else:
            hint = "Median position error exceeds 5.0 m. Consider increasing survey grid density or fine-tuning k."

    verdict_line = (
        f"Verdict: {status} - Floor accuracy {floor_accuracy:.1f}% "
        f"({'PASS >= 95%' if floor_accuracy >= 95.0 else 'FAIL < 95%'}), "
        f"median error {median_error_meters:.2f} m "
        f"({'PASS <= 5 m' if median_error_meters <= 5.0 else 'FAIL > 5 m'}). {hint}"
    )
    return status, verdict_line


# ==============================================================================
# Diagnostics (--diagnose)
# ==============================================================================

def run_diagnostics(
    fingerprints: List[Dict[str, Any]],
    units_per_meter: float = UNITS_PER_METER,
    missing_rssi: float = MISSING_RSSI,
):
    """
    Executes full diagnostic suite:
      a) Hub check (k=1 leave-one-out most-chosen points; flag > 3x average)
      b) Stale-scan check (distance < 3 dB but > 3 m apart or on different floors)
      c) Fingerprints with fewer than 6 Student APs
      d) Floor confusion matrix
    """
    n = len(fingerprints)
    print("=" * 80)
    print(f"WiFi Matching Diagnostics (Dataset: {n} fingerprints)")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # a) Hub check: k=1 LOO
    # --------------------------------------------------------------------------
    print("\na) Hub Check (k=1 Leave-One-Out Nearest Neighbors):")
    chosen_counter: Counter = Counter()

    for i, test_fp in enumerate(fingerprints):
        best_dist = math.inf
        best_id = None
        for j, other_fp in enumerate(fingerprints):
            if j == i:
                continue
            d = rssi_distance(test_fp["readings"], other_fp["readings"], missing_rssi=missing_rssi)
            if d < best_dist:
                best_dist = d
                best_id = other_fp["id"]
        if best_id:
            chosen_counter[best_id] += 1

    avg_chosen = n / n if n > 0 else 1.0  # Exactly 1.0 average
    hub_threshold = 3.0 * avg_chosen

    fp_by_id = {fp["id"]: fp for fp in fingerprints}
    top_10_hubs = chosen_counter.most_common(10)

    print(f"   Average times chosen: {avg_chosen:.2f} | Warning threshold (>3x): > {hub_threshold:.1f} times")
    print(f"   {'#':<3} {'Fingerprint ID':<26} {'Floor':<8} {'(X, Y)':<16} {'Chosen':<8} {'APs':<5} {'Mean RSSI':<11} {'Max RSSI':<10} {'Status'}")
    print("   " + "-" * 102)

    for idx, (fp_id, count) in enumerate(top_10_hubs, 1):
        fp = fp_by_id[fp_id]
        fl_num = fp.get("floor_number", 0)
        xy_str = f"({fp['x']:.1f}, {fp['y']:.1f})"
        ap_count = len(fp["readings"])
        rssi_vals = list(fp["readings"].values())
        mean_rssi = statistics.mean(rssi_vals) if rssi_vals else -100.0
        max_rssi = max(rssi_vals) if rssi_vals else -100.0

        flag = "[HUB WARNING: > 3x avg]" if count > hub_threshold else "[Normal]"
        print(f"   {idx:<3} {fp_id:<26} Floor {fl_num:<2} {xy_str:<16} {count:<8} {ap_count:<5} {mean_rssi:>6.1f} dBm  {max_rssi:>5.0f} dBm   {flag}")

    # --------------------------------------------------------------------------
    # b) Stale-scan check: distance < 3 dB but > 3 m apart or different floors
    # --------------------------------------------------------------------------
    print("\nb) Stale-Scan Check (RSSI distance < 3 dB at different locations):")
    stale_pairs = []

    for i in range(n):
        fp_i = fingerprints[i]
        for j in range(i + 1, n):
            fp_j = fingerprints[j]

            d_rssi = rssi_distance(fp_i["readings"], fp_j["readings"], missing_rssi=missing_rssi)
            if d_rssi < 3.0:
                diff_floor = (fp_i.get("floor_number", 0) != fp_j.get("floor_number", 0))
                dist_xy = math.hypot(fp_i["x"] - fp_j["x"], fp_i["y"] - fp_j["y"])
                dist_m = dist_xy / units_per_meter

                if diff_floor or dist_m > 3.0:
                    stale_pairs.append((fp_i, fp_j, dist_m, d_rssi, diff_floor))

    if not stale_pairs:
        print("   [PASS] No stale or duplicate scans detected across different locations (<3 dB).")
    else:
        print(f"   [WARN] Found {len(stale_pairs)} suspicious pair(s) with RSSI distance < 3 dB (>3 m apart or different floors):")
        print("   (Near-identical scans at different locations usually indicate cached/stale Android WiFi scans)")
        print(f"   {'Pair':<5} {'FP 1 (ID / Floor / Pos)':<42} {'FP 2 (ID / Floor / Pos)':<42} {'Phys Dist':<12} {'RSSI Dist'}")
        print("   " + "-" * 115)
        for idx, (p1, p2, dm, dr, diff_fl) in enumerate(stale_pairs, 1):
            p1_str = f"{p1['id'][:18]}.. (Fl {p1.get('floor_number',0)}, {p1['x']:.0f},{p1['y']:.0f})"
            p2_str = f"{p2['id'][:18]}.. (Fl {p2.get('floor_number',0)}, {p2['x']:.0f},{p2['y']:.0f})"
            dist_desc = f"{dm:.1f} m" + (" [DIFF FLOOR]" if diff_fl else "")
            print(f"   {idx:<5} {p1_str:<42} {p2_str:<42} {dist_desc:<12} {dr:.2f} dB")

    # --------------------------------------------------------------------------
    # c) Count and list fingerprints with fewer than 6 Student APs
    # --------------------------------------------------------------------------
    print("\nc) Student AP Count Check (< 6 APs):")
    few_aps = [fp for fp in fingerprints if len(fp["readings"]) < 6]
    if not few_aps:
        print("   [PASS] All fingerprints have at least 6 Student APs.")
    else:
        print(f"   [WARN] {len(few_aps)}/{n} fingerprints have fewer than 6 Student APs:")
        print(f"   {'#':<3} {'Fingerprint ID':<26} {'Floor':<10} {'Position (X, Y)':<18} {'Student APs'}")
        print("   " + "-" * 68)
        for idx, fp in enumerate(few_aps, 1):
            fl_num = fp.get("floor_number", 0)
            xy_str = f"({fp['x']:.1f}, {fp['y']:.1f})"
            print(f"   {idx:<3} {fp['id']:<26} Floor {fl_num:<4} {xy_str:<18} {len(fp['readings']):<5}")

    # --------------------------------------------------------------------------
    # d) Floor Confusion Matrix
    # --------------------------------------------------------------------------
    print("\nd) Floor Confusion Matrix (k=3 Leave-One-Out):")
    loo_res = evaluate_leave_one_out(fingerprints, k=3, units_per_meter=units_per_meter, missing_rssi=missing_rssi)
    c_matrix = loo_res["confusion_matrix"]

    floors = sorted(c_matrix.keys())
    all_pred_floors = sorted(set(fl for row in c_matrix.values() for fl in row.keys()))

    # Print header
    header = f"   {'True \\ Pred':<14} | " + " | ".join(f"{fl:>9}" for fl in all_pred_floors)
    print(header)
    print("   " + "-" * len(header))
    for t_fl in floors:
        row_str = f"   {t_fl:<14} | "
        row_str += " | ".join(f"{c_matrix[t_fl].get(p_fl, 0):>9}" for p_fl in all_pred_floors)
        print(row_str)

    print("=" * 80)


# ==============================================================================
# Tuning Grid Search (--tune)
# ==============================================================================

def run_tuning(
    fingerprints: List[Dict[str, Any]],
    units_per_meter: float = UNITS_PER_METER,
):
    """
    Grid search over:
      - min_rssi_cutoff: [None, -90, -85, -80]
      - top_n: [None, 6, 8, 10]
      - missing_rssi: [-100, -95, -90]
      - floor_mode: ["vote", "floor_first", "strongest_ap"]
      - k: [2, 3, 4]
    Evaluates both plain LOO and --exclude-radius 100 for all 432 combinations.
    Prints baseline row and top 10 ranked by floor accuracy, then median error in meters.
    """
    n = len(fingerprints)
    print("=" * 105)
    print(f"WiFi Positioning Hyperparameter Tuning (Grid Search: 432 Combinations, Dataset: {n} points)")
    print("=" * 105)

    # 1. Run Baseline
    print("Evaluating Baseline Configuration...")
    base_plain = evaluate_leave_one_out(
        fingerprints,
        k=3,
        units_per_meter=units_per_meter,
        exclude_radius=0.0,
        missing_rssi=-100,
        floor_mode="vote",
        min_rssi_cutoff=None,
        top_n=None,
    )
    base_excl = evaluate_leave_one_out(
        fingerprints,
        k=3,
        units_per_meter=units_per_meter,
        exclude_radius=100.0,
        missing_rssi=-100,
        floor_mode="vote",
        min_rssi_cutoff=None,
        top_n=None,
    )

    cutoffs = [None, -90, -85, -80]
    top_ns = [None, 6, 8, 10]
    missing_rssis = [-100, -95, -90]
    floor_modes = ["vote", "floor_first", "strongest_ap"]
    ks = [2, 3, 4]

    # Precompute pairwise spatial distances and floor matching
    xy_coords = [(fp["x"], fp["y"]) for fp in fingerprints]
    floor_nums = [fp.get("floor_number", 0) for fp in fingerprints]
    is_same_floor = [
        [floor_nums[i] == floor_nums[j] for j in range(n)]
        for i in range(n)
    ]
    spatial_dist = [
        [math.hypot(xy_coords[i][0] - xy_coords[j][0], xy_coords[i][1] - xy_coords[j][1]) for j in range(n)]
        for i in range(n)
    ]

    total_combos = len(cutoffs) * len(top_ns) * len(missing_rssis) * len(floor_modes) * len(ks)
    print(f"Running grid search over {total_combos} combinations (optimized vectorized evaluation)...")

    results_grid = []

    # Iterate over preprocessing configs: (cutoff, top_n, missing_rssi) -> 48 unique configs
    for cutoff, tn, mr in itertools.product(cutoffs, top_ns, missing_rssis):
        # Pre-filter scans
        filtered_fps = []
        for fp in fingerprints:
            f_scan = filter_scan(fp["readings"], min_rssi_cutoff=cutoff, top_n=tn)
            filtered_fps.append({
                "id": fp["id"],
                "floor_id": fp.get("floor_id"),
                "floor_number": fp.get("floor_number", 0),
                "x": fp["x"],
                "y": fp["y"],
                "readings": f_scan,
            })

        # Precompute distance matrix
        d_mat = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = rssi_distance(filtered_fps[i]["readings"], filtered_fps[j]["readings"], missing_rssi=mr)
                d_mat[i][j] = d
                d_mat[j][i] = d

        # Evaluate (floor_mode, k)
        for fm in floor_modes:
            for k_val in ks:
                # Plain LOO (exclude_radius = 0)
                res_plain = _evaluate_fast_grid(
                    filtered_fps, d_mat, is_same_floor, spatial_dist,
                    k=k_val, floor_mode=fm, missing_rssi=mr,
                    units_per_meter=units_per_meter, exclude_radius=0.0
                )
                # Exclude-100 LOO (exclude_radius = 100)
                res_excl = _evaluate_fast_grid(
                    filtered_fps, d_mat, is_same_floor, spatial_dist,
                    k=k_val, floor_mode=fm, missing_rssi=mr,
                    units_per_meter=units_per_meter, exclude_radius=100.0
                )

                results_grid.append({
                    "min_rssi_cutoff": cutoff,
                    "top_n": tn,
                    "missing_rssi": mr,
                    "floor_mode": fm,
                    "k": k_val,
                    "plain_acc": res_plain["accuracy"],
                    "plain_med": res_plain["median"],
                    "plain_mean": res_plain["mean"],
                    "excl_acc": res_excl["accuracy"],
                    "excl_med": res_excl["median"],
                    "excl_mean": res_excl["mean"],
                })

    # Rank by Exclude-100 floor accuracy, then lowest median error in meters
    ranked = sorted(
        results_grid,
        key=lambda item: (-item["excl_acc"], item["excl_med"], -item["plain_acc"], item["plain_med"])
    )

    # Print Baseline Row
    print("-" * 105)
    print("BASELINE ROW:")
    b_cutoff = "None"
    b_topn = "None"
    b_mr = "-100"
    b_mode = "vote"
    b_k = 3
    b_p_acc = base_plain["floor_accuracy"]
    b_p_med = base_plain["overall_meters"]["median"]
    b_e_acc = base_excl["floor_accuracy"]
    b_e_med = base_excl["overall_meters"]["median"]

    print(f"   [Baseline] Cutoff: {b_cutoff:<4} | TopN: {b_topn:<4} | Missing: {b_mr:<4} | Mode: {b_mode:<12} | k: {b_k} "
          f"-> Excl100 Acc: {b_e_acc:>5.1f}% | Excl100 Med: {b_e_med:>5.2f} m "
          f"| Plain Acc: {b_p_acc:>5.1f}% | Plain Med: {b_p_med:>5.2f} m")

    print("-" * 105)
    print("TOP 10 COMBINATIONS (Ranked by Exclude-100 Floor Accuracy, then Lowest Median Error):")
    print(f"{'Rank':<5} | {'Cutoff':<6} | {'TopN':<4} | {'Missing':<7} | {'Mode':<12} | {'k':<2} | {'Excl Acc':<8} | {'Excl Med':<8} | {'Plain Acc':<9} | {'Plain Med'}")
    print("-" * 105)

    for idx, r in enumerate(ranked[:10], 1):
        c_str = str(r["min_rssi_cutoff"]) if r["min_rssi_cutoff"] is not None else "None"
        tn_str = str(r["top_n"]) if r["top_n"] is not None else "None"
        mr_str = str(int(r["missing_rssi"]))
        mode_str = r["floor_mode"]
        k_str = str(r["k"])

        print(f"{idx:<5} | {c_str:<6} | {tn_str:<4} | {mr_str:<7} | {mode_str:<12} | {k_str:<2} | {r['excl_acc']:>7.1f}% | {r['excl_med']:>6.2f} m | {r['plain_acc']:>8.1f}% | {r['plain_med']:>6.2f} m")

    print("=" * 105)
    print("Note: With only about 50-80 points, differences under 2% are noise and simpler settings are preferred.")
    print("=" * 105)


def _evaluate_fast_grid(
    filtered_fps: List[Dict[str, Any]],
    d_mat: List[List[float]],
    is_same_floor: List[List[bool]],
    spatial_dist: List[List[float]],
    k: int,
    floor_mode: str,
    missing_rssi: float,
    units_per_meter: float,
    exclude_radius: float,
) -> Dict[str, float]:
    """Fast in-memory LOO evaluator using precomputed distance matrices for grid tuning."""
    n = len(filtered_fps)
    correct_count = 0
    errors_meters = []

    for i in range(n):
        test_fp = filtered_fps[i]
        true_fl = test_fp["floor_number"]
        true_x, true_y = test_fp["x"], test_fp["y"]

        # Candidates
        cand_indices = []
        for j in range(n):
            if j == i:
                continue
            if exclude_radius > 0 and is_same_floor[i][j]:
                if spatial_dist[i][j] <= exclude_radius:
                    continue
            cand_indices.append(j)

        if not cand_indices:
            continue

        cands = [filtered_fps[j] for j in cand_indices]

        # Use locator with pre-cleaned data
        res = locate(
            live_scan=test_fp["readings"],
            fingerprints=cands,
            k=k,
            missing_rssi=missing_rssi,
            floor_mode=floor_mode,
        )

        if res is not None:
            pred_fl = res.get("floor_number", 0)
            if pred_fl == true_fl:
                correct_count += 1
                err_m = math.hypot(res["x"] - true_x, res["y"] - true_y) / units_per_meter
                errors_meters.append(err_m)

    acc = (correct_count / n) * 100.0 if n > 0 else 0.0
    med = statistics.median(errors_meters) if errors_meters else math.inf
    mean_val = statistics.mean(errors_meters) if errors_meters else math.inf
    return {"accuracy": acc, "median": med, "mean": mean_val}


# ==============================================================================
# Reporting Functions
# ==============================================================================

def print_single_evaluation_report(
    results: Dict[str, Any],
    k: int,
    units_per_meter: float,
    exclude_radius: float,
    floor_known: bool = False,
):
    """Prints standard detailed report for a single evaluation run."""
    print("=" * 72)
    title = "WiFi Indoor Position Matching Accuracy Report"
    if floor_known:
        title += " (FLOOR KNOWN MODE)"
    print(title)
    print("=" * 72)
    print(f"Points tested        : {results['total_points']}")
    print(f"Neighbors k          : {k}")
    print(f"Exclude radius       : {exclude_radius:.1f} SVG units ({exclude_radius / units_per_meter:.2f} m)")
    print(f"Scale                : {units_per_meter} SVG units/meter")
    if floor_known:
        print("Floor Knowledge      : Ground-truth floor is known (candidates restricted to true floor)")
    print(f"Overall floor accuracy: {results['floor_accuracy']:.1f}% ({results['floor_correct_count']}/{results['total_points']})")
    print("-" * 72)

    # Per-floor accuracy
    if not floor_known:
        print("Floor Accuracy Breakdown:")
        for fl_name, fl_data in results["per_floor"].items():
            print(f"   {fl_name:<10}: {fl_data['accuracy']:>5.1f}% ({fl_data['correct']}/{fl_data['total']} points)")
        print("-" * 72)

    print("Position Error (computed on correct-floor points only):")
    ou = results["overall_units"]
    om = results["overall_meters"]
    print(f"   Overall  : Mean: {ou['mean']:.1f} units ({om['mean']:.2f} m) | Median: {ou['median']:.1f} units ({om['median']:.2f} m) | 90th percentile: {ou['p90']:.1f} units ({om['p90']:.2f} m)")

    for fl_name, fl_data in results["per_floor"].items():
        fu = fl_data["units"]
        fm = fl_data["meters"]
        if fu["count"] > 0:
            print(f"   {fl_name:<9}: Mean: {fu['mean']:.1f} units ({fm['mean']:.2f} m) | Median: {fu['median']:.1f} units ({fm['median']:.2f} m) | 90th percentile: {fu['p90']:.1f} units ({fm['p90']:.2f} m)")
        else:
            print(f"   {fl_name:<9}: No points available to evaluate position error.")

    print("-" * 72)
    print("Top 5 Worst Points:")
    print(f"{'#':<3} {'ID':<26} {'Floor':<10} {'True (X, Y)':<18} {'Est (X, Y)':<18} {'Error (m)':<10} {'Floor OK'}")
    print("-" * 92)
    for idx, p in enumerate(results["worst_points"], 1):
        true_xy_str = f"({p['true_x']:.1f}, {p['true_y']:.1f})"
        if p["est_x"] is not None and p["est_y"] is not None:
            est_xy_str = f"({p['est_x']:.1f}, {p['est_y']:.1f})"
        else:
            est_xy_str = "None"
        err_m_str = f"{p['error_meters']:.2f} m" if not math.isinf(p["error_meters"]) else "inf"
        floor_ok_str = "YES" if p["floor_correct"] else f"NO ({p['est_floor']})"
        print(f"{idx:<3} {p['id']:<26} {p['floor']:<10} {true_xy_str:<18} {est_xy_str:<18} {err_m_str:<10} {floor_ok_str}")

    print("=" * 72)
    _, verdict_line = get_verdict(results["floor_accuracy"], om["median"])
    print(verdict_line)
    print("=" * 72)


def run_sweep(
    fingerprints: List[Dict[str, Any]],
    units_per_meter: float,
    exclude_radius: float,
    missing_rssi: float = MISSING_RSSI,
    floor_mode: str = "vote",
    min_rssi_cutoff: Optional[float] = None,
    top_n: Optional[int] = None,
):
    """Runs k-sweep for k = 1 through 6 and prints comparison table."""
    print("=" * 72)
    print("WiFi Fingerprint Parameter Sweep (k = 1 through 6)")
    print(f"Dataset: {len(fingerprints)} points | Exclude radius: {exclude_radius:.1f} units")
    print("=" * 72)
    print(f"{'k':<4} | {'Floor Acc (%)':<15} | {'Median Error (m)':<18} | {'Mean Error (m)':<16}")
    print("-" * 62)

    sweep_results = []
    for k_val in range(1, 7):
        res = evaluate_leave_one_out(
            fingerprints=fingerprints,
            k=k_val,
            units_per_meter=units_per_meter,
            exclude_radius=exclude_radius,
            missing_rssi=missing_rssi,
            floor_mode=floor_mode,
            min_rssi_cutoff=min_rssi_cutoff,
            top_n=top_n,
        )
        med_m = res["overall_meters"]["median"]
        mean_m = res["overall_meters"]["mean"]
        acc = res["floor_accuracy"]

        sweep_results.append({
            "k": k_val,
            "accuracy": acc,
            "median_meters": med_m,
            "mean_meters": mean_m,
            "results": res,
        })
        print(f"{k_val:<4} | {acc:>13.1f}% | {med_m:>16.2f} m | {mean_m:>14.2f} m")

    print("-" * 62)

    # Pick best k: highest floor accuracy, then lowest median error
    best = min(
        sweep_results,
        key=lambda item: (-item["accuracy"], item["median_meters"])
    )

    print(
        f"\nRecommended Best k: {best['k']} "
        f"(Floor Accuracy: {best['accuracy']:.1f}%, Median Error: {best['median_meters']:.2f} m)"
    )

    _, verdict_line = get_verdict(best["accuracy"], best["median_meters"])
    print(verdict_line)
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Leave-one-out positioning accuracy evaluation and diagnostics for WiFi fingerprints."
    )
    parser.add_argument("--building", default=DEFAULT_BUILDING, help=f"Building name (default: {DEFAULT_BUILDING})")
    parser.add_argument("--ssid", default=SSID, help=f"WiFi SSID filter (default: {SSID})")
    parser.add_argument("--k", type=int, default=K, help=f"Number of k nearest neighbors (default: {K})")
    parser.add_argument("--units-per-meter", type=float, default=UNITS_PER_METER, help=f"SVG units per meter (default: {UNITS_PER_METER})")
    parser.add_argument("--exclude-radius", type=float, default=0.0, help="Spatial radius in SVG units to exclude neighbors on same floor (default: 0.0)")
    parser.add_argument("--sweep", action="store_true", help="Run sweep over k = 1 through 6")
    parser.add_argument("--diagnose", action="store_true", help="Run diagnostic analysis (hub check, stale scans, few APs, confusion matrix)")
    parser.add_argument("--tune", action="store_true", help="Run hyperparameter grid search tuning")
    parser.add_argument("--floor-known", action="store_true", help="Assume true floor is known and measure positioning error on that floor")
    parser.add_argument("--min-rssi-cutoff", type=float, default=None, help="Ignore readings weaker than this RSSI (default: None)")
    parser.add_argument("--top-n", type=int, default=None, help="Keep only top N strongest APs per scan (default: None)")
    parser.add_argument("--missing-rssi", type=float, default=MISSING_RSSI, help=f"Missing AP RSSI penalty (default: {MISSING_RSSI})")
    parser.add_argument("--floor-mode", default="vote", choices=["vote", "floor_first", "strongest_ap"], help="Floor selection mode (default: vote)")
    parser.add_argument("--uri", default=None, help="MongoDB connection URI override (default: from env)")
    parser.add_argument("--db-name", default=None, help="MongoDB database name override")

    args = parser.parse_args()

    # Load dataset strictly read-only
    print(f"Loading fingerprints for building '{args.building}' (SSID='{args.ssid}')...")
    try:
        from locator import get_read_only_mongo_db
        db = get_read_only_mongo_db(uri=args.uri, db_name=args.db_name)
    except Exception as e:
        print(f"Database connection notice: {e}", file=sys.stderr)
        db = None

    fingerprints = load_fingerprints(
        building_name=args.building,
        db=db,
        ssid=args.ssid,
        min_rssi=MISSING_RSSI,
        min_rssi_cutoff=args.min_rssi_cutoff,
        top_n=args.top_n,
    )

    if not fingerprints:
        print(f"Error: No fingerprints found for building '{args.building}' with SSID '{args.ssid}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Successfully loaded {len(fingerprints)} fingerprints.")

    if args.diagnose:
        run_diagnostics(
            fingerprints=fingerprints,
            units_per_meter=args.units_per_meter,
            missing_rssi=args.missing_rssi,
        )
    elif args.tune:
        run_tuning(
            fingerprints=fingerprints,
            units_per_meter=args.units_per_meter,
        )
    elif args.sweep:
        run_sweep(
            fingerprints=fingerprints,
            units_per_meter=args.units_per_meter,
            exclude_radius=args.exclude_radius,
            missing_rssi=args.missing_rssi,
            floor_mode=args.floor_mode,
            min_rssi_cutoff=args.min_rssi_cutoff,
            top_n=args.top_n,
        )
    else:
        results = evaluate_leave_one_out(
            fingerprints=fingerprints,
            k=args.k,
            units_per_meter=args.units_per_meter,
            exclude_radius=args.exclude_radius,
            missing_rssi=args.missing_rssi,
            floor_mode=args.floor_mode,
            min_rssi_cutoff=args.min_rssi_cutoff,
            top_n=args.top_n,
            floor_known=args.floor_known,
        )
        print_single_evaluation_report(
            results=results,
            k=args.k,
            units_per_meter=args.units_per_meter,
            exclude_radius=args.exclude_radius,
            floor_known=args.floor_known,
        )


if __name__ == "__main__":
    main()
