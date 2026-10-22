import 'dart:math';
import '../models/fingerprint.dart';

/// Redundancy handling mode when a similar fingerprint is found.
enum RedundancyPolicy {
  /// Reject insertion and alert surveyor (strict deduplication).
  reject,

  /// Merge and average RSSI values with the existing fingerprint to refine radio map.
  averageAndMerge,

  /// Overwrite the existing fingerprint with the new scan.
  overwrite,
}

/// Detailed evaluation of similarity between two fingerprints.
class SimilarityEvaluation {
  final bool isRedundant;
  final Fingerprint? matchedFingerprint;
  final double spatialDistance;
  final double jaccardOverlap;
  final double rssiSimilarity;
  final double overallSimilarity;
  final String message;

  const SimilarityEvaluation({
    required this.isRedundant,
    this.matchedFingerprint,
    required this.spatialDistance,
    required this.jaccardOverlap,
    required this.rssiSimilarity,
    required this.overallSimilarity,
    required this.message,
  });

  static const notRedundant = SimilarityEvaluation(
    isRedundant: false,
    spatialDistance: double.infinity,
    jaccardOverlap: 0.0,
    rssiSimilarity: 0.0,
    overallSimilarity: 0.0,
    message: 'New unique fingerprint location.',
  );
}

/// Core algorithms for detecting identical/similar WiFi fingerprints
/// and reducing redundancy in the database.
class SimilarityService {
  /// Default spatial distance threshold in SVG coordinates (points closer than this are considered candidate duplicates).
  static const double defaultSpatialThreshold = 20.0;

  /// Default radio similarity threshold (0.0 to 1.0, where 0.80 = 80% similarity).
  static const double defaultRadioThreshold = 0.75;

  /// Calculates 2D Euclidean distance between two points in SVG coordinate space.
  static double calculateSpatialDistance(
    double x1,
    double y1,
    double x2,
    double y2,
  ) {
    final dx = x1 - x2;
    final dy = y1 - y2;
    return sqrt(dx * dx + dy * dy);
  }

  /// Calculates radio similarity between two sets of AP readings.
  /// Returns a record with (jaccard, rssiSimilarity, overallScore).
  static ({double jaccard, double rssiSim, double overall})
      compareRadioSignatures(
    List<ApReading> a,
    List<ApReading> b,
  ) {
    if (a.isEmpty && b.isEmpty) {
      return (jaccard: 1.0, rssiSim: 1.0, overall: 1.0);
    }
    if (a.isEmpty || b.isEmpty) {
      return (jaccard: 0.0, rssiSim: 0.0, overall: 0.0);
    }

    final mapA = {for (var r in a) r.bssid.toLowerCase(): r.rssi};
    final mapB = {for (var r in b) r.bssid.toLowerCase(): r.rssi};

    final setA = mapA.keys.toSet();
    final setB = mapB.keys.toSet();

    final intersection = setA.intersection(setB);
    final union = setA.union(setB);

    if (union.isEmpty) {
      return (jaccard: 0.0, rssiSim: 0.0, overall: 0.0);
    }

    final jaccard = intersection.length / union.length;

    if (intersection.isEmpty) {
      return (jaccard: 0.0, rssiSim: 0.0, overall: 0.0);
    }

    // Mean Absolute Error over common BSSIDs
    double totalError = 0.0;
    for (final bssid in intersection) {
      final diff = (mapA[bssid]! - mapB[bssid]!).abs();
      totalError += diff;
    }
    final mae = totalError / intersection.length;

    // Normalize MAE: a difference of 0 dBm = 1.0; difference of >= 25 dBm = 0.0
    final rssiSim = max(0.0, 1.0 - (mae / 25.0));

    // Weighted overall score: 45% BSSID presence overlap + 55% signal level match
    final overall = (0.45 * jaccard) + (0.55 * rssiSim);

    return (jaccard: jaccard, rssiSim: rssiSim, overall: overall);
  }

  /// Evaluates an incoming fingerprint against all existing fingerprints on the same floor.
  /// Identifies if it is redundant based on spatial proximity and WiFi signature similarity.
  static SimilarityEvaluation evaluate({
    required Fingerprint candidate,
    required List<Fingerprint> existingOnFloor,
    double spatialThreshold = defaultSpatialThreshold,
    double radioThreshold = defaultRadioThreshold,
  }) {
    if (existingOnFloor.isEmpty) {
      return SimilarityEvaluation.notRedundant;
    }

    Fingerprint? mostRedundantPoint;
    double minDistance = double.infinity;
    double bestOverallSim = 0.0;
    double bestJaccard = 0.0;
    double bestRssiSim = 0.0;
    bool isRedundant = false;
    String decisionReason = '';

    for (final existing in existingOnFloor) {
      // Don't compare with itself if re-evaluating
      if (existing.id != null && existing.id == candidate.id) continue;

      final dist = calculateSpatialDistance(
        candidate.x,
        candidate.y,
        existing.x,
        existing.y,
      );

      final radio = compareRadioSignatures(
        candidate.readings,
        existing.readings,
      );

      // Check Criteria 1: Exact or extremely close spatial match (<= spatialThreshold)
      if (dist <= spatialThreshold) {
        isRedundant = true;
        if (dist < minDistance) {
          minDistance = dist;
          mostRedundantPoint = existing;
          bestOverallSim = radio.overall;
          bestJaccard = radio.jaccard;
          bestRssiSim = radio.rssiSim;
          decisionReason = dist < 2.0
              ? 'Exact surveyed point match (d=${dist.toStringAsFixed(1)}px, signal match ${(radio.overall * 100).toStringAsFixed(0)}%)'
              : 'Within duplicate proximity radius (d=${dist.toStringAsFixed(1)}px <= ${spatialThreshold.toStringAsFixed(0)}px, signal match ${(radio.overall * 100).toStringAsFixed(0)}%)';
        }
      }
      // Check Criteria 2: Within 2x proximity and very high radio similarity
      else if (dist <= spatialThreshold * 2.0 && radio.overall >= radioThreshold) {
        if (!isRedundant || radio.overall > bestOverallSim) {
          isRedundant = true;
          minDistance = dist;
          mostRedundantPoint = existing;
          bestOverallSim = radio.overall;
          bestJaccard = radio.jaccard;
          bestRssiSim = radio.rssiSim;
          decisionReason =
              'High radio signature match (${(radio.overall * 100).toStringAsFixed(0)}% >= ${(radioThreshold * 100).toStringAsFixed(0)}%) within nearby zone (d=${dist.toStringAsFixed(1)}px)';
        }
      }
    }

    if (!isRedundant || mostRedundantPoint == null) {
      return SimilarityEvaluation.notRedundant;
    }

    return SimilarityEvaluation(
      isRedundant: true,
      matchedFingerprint: mostRedundantPoint,
      spatialDistance: minDistance,
      jaccardOverlap: bestJaccard,
      rssiSimilarity: bestRssiSim,
      overallSimilarity: bestOverallSim,
      message: decisionReason,
    );
  }

  /// Merges two scans into a refined, averaged set of AP readings.
  /// Useful when multiple devices survey the same point.
  static List<ApReading> mergeAndAverageReadings({
    required List<ApReading> existingReadings,
    required List<ApReading> newReadings,
    required int currentSampleCount,
  }) {
    final existingMap = {for (var r in existingReadings) r.bssid.toLowerCase(): r};
    final newMap = {for (var r in newReadings) r.bssid.toLowerCase(): r};

    final allBssids = {...existingMap.keys, ...newMap.keys};
    final mergedList = <ApReading>[];

    final n = max(1, currentSampleCount);

    for (final bssid in allBssids) {
      final oldAp = existingMap[bssid];
      final newAp = newMap[bssid];

      if (oldAp != null && newAp != null) {
        // Weighted running average
        final averagedRssi = ((oldAp.rssi * n + newAp.rssi) / (n + 1)).round();
        mergedList.add(ApReading(
          bssid: oldAp.bssid,
          ssid: oldAp.ssid.isNotEmpty && oldAp.ssid != '(hidden)'
              ? oldAp.ssid
              : newAp.ssid,
          rssi: averagedRssi,
        ));
      } else if (oldAp != null) {
        mergedList.add(oldAp);
      } else if (newAp != null) {
        mergedList.add(newAp);
      }
    }

    // Sort strongest AP first
    mergedList.sort((a, b) => b.rssi.compareTo(a.rssi));
    return mergedList;
  }
}
