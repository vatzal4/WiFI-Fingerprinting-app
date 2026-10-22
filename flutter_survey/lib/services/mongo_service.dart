import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:mongo_dart/mongo_dart.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/fingerprint.dart';
import 'device_service.dart';
import 'similarity_service.dart';

/// Connection state of MongoDB Atlas.
enum MongoConnectionState {
  disconnected,
  connecting,
  connected,
  error,
}

/// Result of attempting to save a fingerprint.
class FingerprintSaveResult {
  final bool success;
  final bool isRedundant;
  final bool wasMerged;
  final String? fingerprintId;
  final SimilarityEvaluation? evaluation;
  final String message;

  const FingerprintSaveResult({
    required this.success,
    this.isRedundant = false,
    this.wasMerged = false,
    this.fingerprintId,
    this.evaluation,
    required this.message,
  });
}

/// Direct MongoDB Atlas cloud database service.
/// Connects mobile devices across campus to MongoDB Atlas
/// without requiring a local Flask server to be running.
class MongoService {
  /// Standard direct replica set URI. Bypasses Android's lack of DNS SRV support.
  static const String defaultUri =
      'mongodb://24bt04d238_db_user:BuHPFJoodpXiiWwu@ac-osx3baj-shard-00-00.9vfenvq.mongodb.net:27017,ac-osx3baj-shard-00-01.9vfenvq.mongodb.net:27017,ac-osx3baj-shard-00-02.9vfenvq.mongodb.net:27017/wifi_fingerprints?ssl=true&replicaSet=atlas-olg164-shard-0&authSource=admin&appName=Wifi-Fingerprint-Cluster';

  static const String prefKeyMongoUri = 'survey_mongo_connection_uri';
  static const String prefKeyRedundancyPolicy = 'survey_redundancy_policy';
  static const String prefKeySpatialThreshold = 'survey_spatial_threshold';
  static const String prefKeyRadioThreshold = 'survey_radio_threshold';

  static final MongoService _instance = MongoService._internal();
  factory MongoService() => _instance;
  MongoService._internal();

  Db? _db;
  final ValueNotifier<MongoConnectionState> connectionState =
      ValueNotifier(MongoConnectionState.disconnected);
  String? _lastErrorMessage;
  String? get lastErrorMessage => _lastErrorMessage;

  DbCollection get _fingerprintsCol => _db!.collection('fingerprints');

  /// Resolves any mongodb+srv:// URI into explicit shard replica hosts
  /// to ensure reliable socket connection on all Android devices.
  static String resolveToDirectUri(String uri) {
    if (!uri.startsWith('mongodb+srv://')) {
      return uri;
    }

    final uriWithoutScheme = uri.substring('mongodb+srv://'.length);
    final atIndex = uriWithoutScheme.indexOf('@');
    if (atIndex == -1) return uri;

    final authPart = uriWithoutScheme.substring(0, atIndex);
    final restPart = uriWithoutScheme.substring(atIndex + 1);

    final slashIndex = restPart.indexOf('/');
    final questionIndex = restPart.indexOf('?');

    String hostPart;
    String dbPart = 'wifi_fingerprints';

    if (slashIndex != -1) {
      hostPart = restPart.substring(0, slashIndex);
      final afterSlash = restPart.substring(slashIndex + 1);
      final qIndex = afterSlash.indexOf('?');
      if (qIndex != -1) {
        final db = afterSlash.substring(0, qIndex).trim();
        if (db.isNotEmpty) dbPart = db;
      } else {
        final db = afterSlash.trim();
        if (db.isNotEmpty) dbPart = db;
      }
    } else if (questionIndex != -1) {
      hostPart = restPart.substring(0, questionIndex);
    } else {
      hostPart = restPart;
    }

    if (hostPart.contains('wifi-fingerprint-cluste.9vfenvq.mongodb.net')) {
      return 'mongodb://$authPart@ac-osx3baj-shard-00-00.9vfenvq.mongodb.net:27017,'
          'ac-osx3baj-shard-00-01.9vfenvq.mongodb.net:27017,'
          'ac-osx3baj-shard-00-02.9vfenvq.mongodb.net:27017/'
          '$dbPart?ssl=true&replicaSet=atlas-olg164-shard-0&authSource=admin&appName=Wifi-Fingerprint-Cluster';
    }

    return uri;
  }

  /// Gets the currently configured MongoDB Atlas URI.
  static Future<String> getConfiguredUri() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(prefKeyMongoUri);
    if (saved == null || saved.isEmpty) {
      return defaultUri;
    }
    // Automatically convert if user previously had stored the srv string
    if (saved.startsWith('mongodb+srv://')) {
      final direct = resolveToDirectUri(saved);
      await prefs.setString(prefKeyMongoUri, direct);
      return direct;
    }
    return saved;
  }

  /// Sets and saves a new MongoDB Atlas URI.
  Future<void> setConfiguredUri(String newUri) async {
    final prefs = await SharedPreferences.getInstance();
    final resolved = resolveToDirectUri(newUri.trim());
    await prefs.setString(prefKeyMongoUri, resolved);
    await disconnect();
    await connect();
  }

  /// Gets current redundancy policy.
  static Future<RedundancyPolicy> getRedundancyPolicy() async {
    final prefs = await SharedPreferences.getInstance();
    final index = prefs.getInt(prefKeyRedundancyPolicy);
    if (index != null && index >= 0 && index < RedundancyPolicy.values.length) {
      return RedundancyPolicy.values[index];
    }
    return RedundancyPolicy.reject;
  }

  /// Sets redundancy policy.
  static Future<void> setRedundancyPolicy(RedundancyPolicy policy) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(prefKeyRedundancyPolicy, policy.index);
  }

  /// Gets spatial threshold (in SVG pixels).
  static Future<double> getSpatialThreshold() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getDouble(prefKeySpatialThreshold) ??
        SimilarityService.defaultSpatialThreshold;
  }

  /// Sets spatial threshold.
  static Future<void> setSpatialThreshold(double threshold) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(prefKeySpatialThreshold, threshold);
  }

  /// Connects to MongoDB Atlas.
  Future<bool> connect() async {
    if (_db != null && _db!.isConnected) {
      connectionState.value = MongoConnectionState.connected;
      return true;
    }

    try {
      connectionState.value = MongoConnectionState.connecting;
      final rawUri = await getConfiguredUri();
      final uri = resolveToDirectUri(rawUri);

      _db = await Db.create(uri);
      await _db!.open().timeout(const Duration(seconds: 15));

      // Ensure indexes for fast lookups and deduplication queries
      try {
        await _fingerprintsCol.createIndex(keys: {
          'building_name': 1,
          'floor_id': 1,
          'x': 1,
          'y': 1,
        });
        await _fingerprintsCol.createIndex(keys: {
          'captured_at': -1,
        });
      } catch (e) {
        debugPrint('Index creation note: $e');
      }

      // Register this device's presence in MongoDB Atlas
      try {
        final dev = await DeviceService.getDeviceInfo();
        final devCol = _db!.collection('devices');
        await devCol.createIndex(keys: {'device_id': 1}, unique: true);
        await devCol.modernUpdate(
          where.eq('device_id', dev.deviceId),
          modify
              .set('device_id', dev.deviceId)
              .set('device_name', dev.deviceName)
              .set('model', dev.model)
              .set('platform', dev.platform)
              .set('last_seen', DateTime.now().toUtc().toIso8601String()),
          upsert: true,
        );
      } catch (e) {
        debugPrint('Device registration note: $e');
      }

      connectionState.value = MongoConnectionState.connected;
      _lastErrorMessage = null;
      return true;
    } catch (e) {
      _lastErrorMessage = e.toString();
      connectionState.value = MongoConnectionState.error;
      debugPrint('MongoDB Atlas connection error: $e');
      return false;
    }
  }

  /// Closes database connection.
  Future<void> disconnect() async {
    try {
      if (_db != null && _db!.isConnected) {
        await _db!.close();
      }
    } catch (_) {}
    _db = null;
    connectionState.value = MongoConnectionState.disconnected;
  }

  /// Ensures database connection is open before running queries.
  Future<bool> ensureConnected() async {
    if (_db != null && _db!.isConnected) return true;
    return await connect();
  }

  /// Saves a new surveyed fingerprint, automatically filtering out same/similar fingerprints.
  Future<FingerprintSaveResult> saveFingerprint(
    Fingerprint fingerprint, {
    RedundancyPolicy? policy,
    double? spatialThreshold,
    double? radioThreshold,
  }) async {
    final isConn = await ensureConnected();
    if (!isConn) {
      return FingerprintSaveResult(
        success: false,
        message: 'Could not connect to MongoDB Atlas: $_lastErrorMessage',
      );
    }

    final activePolicy = policy ?? await getRedundancyPolicy();
    final activeSpatialThresh =
        spatialThreshold ?? await getSpatialThreshold();
    final activeRadioThresh =
        radioThreshold ?? SimilarityService.defaultRadioThreshold;

    try {
      // 1. Fetch existing fingerprints on this floor to check for duplicates/similarity
      final existingPoints = await getFingerprintsForFloor(
        fingerprint.floorId,
        buildingName: fingerprint.buildingName,
      );

      // 2. Evaluate similarity and redundancy
      final eval = SimilarityService.evaluate(
        candidate: fingerprint,
        existingOnFloor: existingPoints,
        spatialThreshold: activeSpatialThresh,
        radioThreshold: activeRadioThresh,
      );

      // 3. Handle redundant match according to policy
      if (eval.isRedundant && eval.matchedFingerprint != null) {
        final existingMatch = eval.matchedFingerprint!;

        if (activePolicy == RedundancyPolicy.reject) {
          return FingerprintSaveResult(
            success: false,
            isRedundant: true,
            fingerprintId: existingMatch.id,
            evaluation: eval,
            message:
                'Filtered out redundancy: Point already surveyed at (${existingMatch.x.toStringAsFixed(1)}, ${existingMatch.y.toStringAsFixed(1)}). ${eval.message}',
          );
        } else if (activePolicy == RedundancyPolicy.averageAndMerge) {
          final mergedReadings = SimilarityService.mergeAndAverageReadings(
            existingReadings: existingMatch.readings,
            newReadings: fingerprint.readings,
            currentSampleCount: existingMatch.sampleCount,
          );

          final updatedContributors = [
            ...?existingMatch.contributors,
            {
              'captured_at': fingerprint.capturedAt.toIso8601String(),
              'device': fingerprint.deviceInfo ?? {'device_name': 'Unknown Device'},
              'ap_count': fingerprint.readings.length,
            }
          ];

          final query = existingMatch.id != null
              ? where.id(ObjectId.parse(existingMatch.id!))
              : where
                  .eq('floor_id', existingMatch.floorId)
                  .eq('x', existingMatch.x)
                  .eq('y', existingMatch.y);

          final updateDoc = modify
              .set('readings', mergedReadings.map((r) => r.toJson()).toList())
              .inc('sample_count', 1)
              .set('contributors', updatedContributors)
              .set('last_updated_at', DateTime.now().toUtc().toIso8601String());

          await _fingerprintsCol.updateOne(query, updateDoc);

          return FingerprintSaveResult(
            success: true,
            isRedundant: true,
            wasMerged: true,
            fingerprintId: existingMatch.id,
            evaluation: eval,
            message:
                'Refined existing fingerprint: Merged scan into point (${existingMatch.x.toStringAsFixed(1)}, ${existingMatch.y.toStringAsFixed(1)}). Total samples: ${existingMatch.sampleCount + 1}.',
          );
        } else if (activePolicy == RedundancyPolicy.overwrite) {
          final query = existingMatch.id != null
              ? where.id(ObjectId.parse(existingMatch.id!))
              : where
                  .eq('floor_id', existingMatch.floorId)
                  .eq('x', existingMatch.x)
                  .eq('y', existingMatch.y);

          final doc = fingerprint.toJson();
          doc.remove('_id');
          doc['last_updated_at'] = DateTime.now().toUtc().toIso8601String();

          await _fingerprintsCol.replaceOne(query, doc);

          return FingerprintSaveResult(
            success: true,
            isRedundant: true,
            fingerprintId: existingMatch.id,
            evaluation: eval,
            message:
                'Overwrote existing point (${existingMatch.x.toStringAsFixed(1)}, ${existingMatch.y.toStringAsFixed(1)}) with new scan.',
          );
        }
      }

      // 4. Not redundant: Insert fresh new fingerprint
      final docToInsert = fingerprint.toJson();
      docToInsert.remove('_id');

      final insertResult = await _fingerprintsCol.insertOne(docToInsert);
      final newId = insertResult.id?.toString();

      return FingerprintSaveResult(
        success: true,
        isRedundant: false,
        fingerprintId: newId,
        evaluation: eval,
        message: 'Saved new fingerprint with ${fingerprint.readings.length} APs.',
      );
    } catch (e) {
      debugPrint('Error saving fingerprint to MongoDB Atlas: $e');
      return FingerprintSaveResult(
        success: false,
        message: 'Failed to save to MongoDB Atlas: $e',
      );
    }
  }

  /// Fetches all surveyed points for a specific floor.
  Future<List<Fingerprint>> getFingerprintsForFloor(
    int floorId, {
    String? buildingName,
  }) async {
    final isConn = await ensureConnected();
    if (!isConn) return [];

    try {
      var query = where.eq('floor_id', floorId);
      if (buildingName != null && buildingName.isNotEmpty) {
        query = query.eq('building_name', buildingName);
      }

      final docs = await _fingerprintsCol.find(query).toList();
      return docs.map((d) => Fingerprint.fromMap(d)).toList();
    } catch (e) {
      debugPrint('Error querying fingerprints for floor: $e');
      return [];
    }
  }

  /// Gets the count of surveyed points on a floor or in total.
  Future<int> getFingerprintCount({int? floorId, String? buildingName}) async {
    final isConn = await ensureConnected();
    if (!isConn) return 0;

    try {
      var query = where;
      if (floorId != null) query = query.eq('floor_id', floorId);
      if (buildingName != null) query = query.eq('building_name', buildingName);
      return await _fingerprintsCol.count(query);
    } catch (e) {
      return 0;
    }
  }

  /// Deletes a surveyed point by ID.
  Future<bool> deleteFingerprint(String id) async {
    final isConn = await ensureConnected();
    if (!isConn) return false;

    try {
      final res = await _fingerprintsCol.deleteOne(where.id(ObjectId.parse(id)));
      return res.isSuccess;
    } catch (e) {
      debugPrint('Error deleting fingerprint: $e');
      return false;
    }
  }

  /// Gets overall statistics from MongoDB Atlas across all devices.
  Future<Map<String, dynamic>> getSurveyStats() async {
    final isConn = await ensureConnected();
    if (!isConn) {
      return {
        'connected': false,
        'total_fingerprints': 0,
        'unique_devices': 0,
        'error': _lastErrorMessage,
      };
    }

    try {
      final total = await _fingerprintsCol.count();
      final devCol = _db!.collection('devices');
      int deviceCount = 0;
      try {
        deviceCount = await devCol.count();
      } catch (_) {}

      if (deviceCount == 0) {
        final distinctDevices =
            await _fingerprintsCol.distinct('device_info.device_id');
        deviceCount = (distinctDevices['values'] as List?)?.length ?? 0;
      }
      if (deviceCount == 0) {
        deviceCount = 1; // Current connected device
      }

      return {
        'connected': true,
        'total_fingerprints': total,
        'unique_devices': deviceCount,
      };
    } catch (e) {
      return {
        'connected': false,
        'total_fingerprints': 0,
        'unique_devices': 0,
        'error': e.toString(),
      };
    }
  }
}
