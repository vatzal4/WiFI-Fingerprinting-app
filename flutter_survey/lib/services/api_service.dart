import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import '../models/fingerprint.dart';

/// Structured response from an upload operation.
class UploadResult {
  final bool success;
  final bool isDuplicate;
  final String? errorMessage;
  final int? statusCode;

  const UploadResult({
    required this.success,
    this.isDuplicate = false,
    this.errorMessage,
    this.statusCode,
  });
}

/// Talks to the backend built in backend/app.py (Flask + SQLite).
/// Same API contract regardless of which client (Android native, Flutter,
/// anything else) is doing the surveying.
class ApiService {
  final String baseUrl; // e.g. "http://10.205.26.28:5050" or "http://127.0.0.1:5050"

  ApiService({required this.baseUrl});

  Future<UploadResult> uploadFingerprint(Fingerprint fp) async {
    try {
      final uri = Uri.parse('$baseUrl/api/fingerprints');
      final response = await http
          .post(
            uri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(fp.toJson()),
          )
          .timeout(const Duration(seconds: 6));

      if (response.statusCode >= 200 && response.statusCode < 300) {
        return const UploadResult(success: true);
      } else if (response.statusCode == 409) {
        return const UploadResult(
          success: false,
          isDuplicate: true,
          statusCode: 409,
          errorMessage:
              'Point already surveyed: A fingerprint already exists for this location.',
        );
      } else {
        return UploadResult(
          success: false,
          statusCode: response.statusCode,
          errorMessage:
              'Server returned error ${response.statusCode}: ${response.body}',
        );
      }
    } on SocketException catch (e) {
      return UploadResult(
        success: false,
        errorMessage:
            'Network unreachable: Cannot connect to $baseUrl (${e.message}). Check backend IP, firewall, or use USB ADB reverse.',
      );
    } on http.ClientException catch (e) {
      return UploadResult(
        success: false,
        errorMessage:
            'Connection failed: ${e.message} to $baseUrl. Check if backend app.py is running.',
      );
    } on TimeoutException {
      return UploadResult(
        success: false,
        errorMessage: 'Request timed out connecting to $baseUrl.',
      );
    } catch (e) {
      return UploadResult(
        success: false,
        errorMessage: 'Upload failed: $e',
      );
    }
  }

  Future<List<dynamic>> listFingerprints({
    int? floorId,
    String? buildingName,
    int? buildingId,
  }) async {
    try {
      final queryParams = <String>[];
      if (floorId != null) queryParams.add('floor_id=$floorId');
      if (buildingName != null) queryParams.add('building_name=$buildingName');
      if (buildingId != null) queryParams.add('building_id=$buildingId');

      final queryString =
          queryParams.isNotEmpty ? '?${queryParams.join('&')}' : '';
      final uri = Uri.parse('$baseUrl/api/fingerprints$queryString');
      final response = await http.get(uri).timeout(const Duration(seconds: 5));
      if (response.statusCode != 200) return [];
      return jsonDecode(response.body) as List<dynamic>;
    } catch (_) {
      return [];
    }
  }

  Future<List<dynamic>> listFloors({
    String? buildingName,
    int? buildingId,
  }) async {
    try {
      final queryParams = <String>[];
      if (buildingName != null) queryParams.add('building_name=$buildingName');
      if (buildingId != null) queryParams.add('building_id=$buildingId');

      final queryString =
          queryParams.isNotEmpty ? '?${queryParams.join('&')}' : '';
      final uri = Uri.parse('$baseUrl/api/floors$queryString');
      final response = await http.get(uri).timeout(const Duration(seconds: 5));
      if (response.statusCode != 200) return [];
      return jsonDecode(response.body) as List<dynamic>;
    } catch (_) {
      return [];
    }
  }

  Future<List<dynamic>> listBuildings() async {
    try {
      final uri = Uri.parse('$baseUrl/api/buildings');
      final response = await http.get(uri).timeout(const Duration(seconds: 5));
      if (response.statusCode != 200) return [];
      return jsonDecode(response.body) as List<dynamic>;
    } catch (_) {
      return [];
    }
  }

  Future<bool> isReachable() async {
    try {
      final response = await http
          .get(Uri.parse('$baseUrl/health'))
          .timeout(const Duration(seconds: 3));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }
}
