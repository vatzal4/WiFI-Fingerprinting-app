import 'package:shared_preferences/shared_preferences.dart';
import '../models/fingerprint.dart';
import 'api_service.dart';
import 'device_service.dart';
import 'mongo_service.dart';
import 'similarity_service.dart';

/// Storage backend mode.
enum StorageMode {
  mongoAtlas,
  flaskBackend,
}

/// Result of a fingerprint upload or save operation.
class SurveyUploadResult {
  final bool success;
  final bool isRedundant;
  final bool wasMerged;
  final String? fingerprintId;
  final String message;
  final SimilarityEvaluation? evaluation;

  const SurveyUploadResult({
    required this.success,
    this.isRedundant = false,
    this.wasMerged = false,
    this.fingerprintId,
    required this.message,
    this.evaluation,
  });
}

/// Unified survey repository that manages uploads, fetching existing surveyed points,
/// device identification, and switching between direct MongoDB Atlas cloud and Flask backend.
class SurveyRepository {
  static const String prefKeyStorageMode = 'survey_storage_mode';
  static const String prefKeyFlaskUrl = 'survey_flask_backend_url';

  static final SurveyRepository _instance = SurveyRepository._internal();
  factory SurveyRepository() => _instance;
  SurveyRepository._internal();

  final MongoService _mongo = MongoService();
  late ApiService _api;
  StorageMode _currentMode = StorageMode.mongoAtlas;

  StorageMode get currentMode => _currentMode;
  MongoService get mongo => _mongo;

  /// Initializes the repository with saved user preferences.
  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    final modeIndex = prefs.getInt(prefKeyStorageMode) ?? 0;
    _currentMode = StorageMode.values[modeIndex.clamp(0, StorageMode.values.length - 1)];

    final flaskUrl = prefs.getString(prefKeyFlaskUrl) ?? 'http://10.205.26.28:5050';
    _api = ApiService(baseUrl: flaskUrl);

    if (_currentMode == StorageMode.mongoAtlas) {
      // Connect to MongoDB Atlas in background
      _mongo.connect();
    }
  }

  /// Sets the active storage mode (Direct MongoDB Atlas vs Flask Backend).
  Future<void> setStorageMode(StorageMode mode) async {
    _currentMode = mode;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(prefKeyStorageMode, mode.index);

    if (mode == StorageMode.mongoAtlas) {
      await _mongo.connect();
    }
  }

  /// Sets the Flask backend URL.
  Future<void> setFlaskUrl(String url) async {
    _api = ApiService(baseUrl: url.trim());
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(prefKeyFlaskUrl, url.trim());
  }

  /// Gets the Flask backend URL.
  static Future<String> getFlaskUrl() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(prefKeyFlaskUrl) ?? 'http://10.205.26.28:5050';
  }

  /// Captures and stores a fingerprint from the current device.
  Future<SurveyUploadResult> saveFingerprint({
    required int floorId,
    int? floorNumber,
    String? floorLabel,
    String? buildingName,
    required double x,
    required double y,
    String? nodeId,
    required List<ApReading> readings,
    RedundancyPolicy? policy,
  }) async {
    final deviceInfo = await DeviceService.getDeviceInfo();

    final fingerprint = Fingerprint(
      floorId: floorId,
      floorNumber: floorNumber,
      floorLabel: floorLabel,
      buildingName: buildingName,
      x: x,
      y: y,
      nodeId: nodeId,
      deviceInfo: deviceInfo.toMap(),
      readings: readings,
      contributors: [
        {
          'captured_at': DateTime.now().toUtc().toIso8601String(),
          'device': deviceInfo.toMap(),
          'ap_count': readings.length,
        }
      ],
    );

    if (_currentMode == StorageMode.mongoAtlas) {
      final mongoResult = await _mongo.saveFingerprint(
        fingerprint,
        policy: policy,
      );

      return SurveyUploadResult(
        success: mongoResult.success,
        isRedundant: mongoResult.isRedundant,
        wasMerged: mongoResult.wasMerged,
        fingerprintId: mongoResult.fingerprintId,
        message: mongoResult.message,
        evaluation: mongoResult.evaluation,
      );
    } else {
      // Flask Backend mode
      final apiResult = await _api.uploadFingerprint(fingerprint);
      if (apiResult.success) {
        return SurveyUploadResult(
          success: true,
          message: 'Saved to backend API with ${readings.length} APs.',
        );
      } else if (apiResult.isDuplicate) {
        return SurveyUploadResult(
          success: false,
          isRedundant: true,
          message: apiResult.errorMessage ?? 'Duplicate point already exists.',
        );
      } else {
        return SurveyUploadResult(
          success: false,
          message: apiResult.errorMessage ?? 'Upload failed.',
        );
      }
    }
  }

  /// Retrieves all surveyed points on a given floor.
  Future<List<Fingerprint>> getSurveyedPoints(
    int floorId, {
    String? buildingName,
  }) async {
    if (_currentMode == StorageMode.mongoAtlas) {
      return await _mongo.getFingerprintsForFloor(
        floorId,
        buildingName: buildingName,
      );
    } else {
      final list = await _api.listFingerprints(
        floorId: floorId,
        buildingName: buildingName,
      );
      return list
          .map((item) => Fingerprint.fromMap(item as Map<String, dynamic>))
          .toList();
    }
  }

  /// Gets the count of surveyed points on a given floor.
  Future<int> getPointCount(int floorId, {String? buildingName}) async {
    if (_currentMode == StorageMode.mongoAtlas) {
      return await _mongo.getFingerprintCount(
        floorId: floorId,
        buildingName: buildingName,
      );
    } else {
      final points = await getSurveyedPoints(floorId, buildingName: buildingName);
      return points.length;
    }
  }

  /// Deletes a surveyed point.
  Future<bool> deletePoint(String id) async {
    if (_currentMode == StorageMode.mongoAtlas) {
      return await _mongo.deleteFingerprint(id);
    }
    return false;
  }
}
