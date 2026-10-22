import 'package:flutter/material.dart';
import '../models/building.dart';
import '../services/device_service.dart';
import '../services/mongo_service.dart';
import '../services/similarity_service.dart';
import '../services/survey_repository.dart';
import 'floor_picker_screen.dart';

/// Main landing screen: Select which campus building to survey.
/// Provides live MongoDB Atlas cloud status, multi-device team stats,
/// surveyor profile, and redundancy filtering settings.
class BuildingPickerScreen extends StatefulWidget {
  final String backendBaseUrl;

  const BuildingPickerScreen({
    super.key,
    this.backendBaseUrl = 'http://10.205.26.28:5050',
  });

  @override
  State<BuildingPickerScreen> createState() => _BuildingPickerScreenState();
}

class _BuildingPickerScreenState extends State<BuildingPickerScreen> {
  final _repo = SurveyRepository();
  final _mongo = MongoService();

  Map<String, dynamic> _cloudStats = {
    'connected': false,
    'total_fingerprints': 0,
    'unique_devices': 0,
  };
  bool _isRefreshingStats = false;
  DeviceInfoData? _deviceInfo;

  @override
  void initState() {
    super.initState();
    _initializeRepoAndStats();
  }

  Future<void> _initializeRepoAndStats() async {
    await _repo.init();
    _deviceInfo = await DeviceService.getDeviceInfo();
    _refreshStats();
  }

  Future<void> _refreshStats() async {
    setState(() => _isRefreshingStats = true);
    final stats = await _mongo.getSurveyStats();
    if (mounted) {
      setState(() {
        _cloudStats = stats;
        _isRefreshingStats = false;
      });
    }
  }

  void _showConnectionErrorDialog(String error) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        icon: const Icon(Icons.cloud_off, color: Colors.redAccent, size: 36),
        title: const Text('Atlas Connection Offline'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Could not reach MongoDB Atlas:\n$error',
              style: const TextStyle(fontSize: 13),
            ),
            const SizedBox(height: 14),
            const Text(
              'Atlas Network Checklist:',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
            ),
            const SizedBox(height: 6),
            const Text(
              '• Allow 0.0.0.0/0: Open your MongoDB Atlas Console -> Security -> Network Access -> Add IP Address -> "Allow Access from Anywhere" (0.0.0.0/0).\n'
              '• Device Internet: Ensure Wi-Fi or mobile data is connected.\n'
              '• Settings: Test or reset connection in Settings.',
              style: TextStyle(fontSize: 12, height: 1.4),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Dismiss'),
          ),
          FilledButton.icon(
            onPressed: () {
              Navigator.of(ctx).pop();
              _showSettingsDialog();
            },
            icon: const Icon(Icons.settings, size: 16),
            label: const Text('Open Settings'),
          ),
        ],
      ),
    );
  }

  Future<void> _showSettingsDialog() async {
    final currentUri = await MongoService.getConfiguredUri();
    final currentFlaskUrl = await SurveyRepository.getFlaskUrl();
    final currentPolicy = await MongoService.getRedundancyPolicy();
    final currentSpatialThresh = await MongoService.getSpatialThreshold();
    final currentSurveyorName = await DeviceService.getSurveyorName();
    final deviceInfo = await DeviceService.getDeviceInfo();

    final uriController = TextEditingController(text: currentUri);
    final flaskController = TextEditingController(text: currentFlaskUrl);
    final surveyorController = TextEditingController(text: currentSurveyorName);

    StorageMode activeMode = _repo.currentMode;
    RedundancyPolicy activePolicy = currentPolicy;
    double spatialThresh = currentSpatialThresh;
    bool isTestingMongo = false;
    String? testResult;

    if (!mounted) return;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Row(
            children: [
              Icon(Icons.tune, size: 24),
              SizedBox(width: 8),
              Text('Survey & Cloud Settings'),
            ],
          ),
          content: SizedBox(
            width: double.maxFinite,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  // --- SECTION 1: STORAGE MODE ---
                  const Text(
                    'Storage Mode',
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                  ),
                  const SizedBox(height: 6),
                  SegmentedButton<StorageMode>(
                    segments: const [
                      ButtonSegment(
                        value: StorageMode.mongoAtlas,
                        icon: Icon(Icons.cloud_queue, size: 16),
                        label: Text('MongoDB Atlas'),
                      ),
                      ButtonSegment(
                        value: StorageMode.flaskBackend,
                        icon: Icon(Icons.dns_outlined, size: 16),
                        label: Text('Flask API'),
                      ),
                    ],
                    selected: {activeMode},
                    onSelectionChanged: (newSelection) {
                      setDialogState(() {
                        activeMode = newSelection.first;
                      });
                    },
                  ),
                  const SizedBox(height: 16),

                  // --- SECTION 2: SURVEYOR IDENTITY ---
                  const Text(
                    'Surveyor Profile (This Device)',
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                  ),
                  const SizedBox(height: 6),
                  TextField(
                    controller: surveyorController,
                    decoration: InputDecoration(
                      labelText: 'Surveyor / Device Name',
                      hintText: 'e.g., Alice - Galaxy S22',
                      prefixIcon: const Icon(Icons.person_outline),
                      border: const OutlineInputBorder(),
                      helperText: 'Device: ${deviceInfo.model}',
                    ),
                  ),
                  const SizedBox(height: 16),

                  // --- SECTION 3: MONGODB ATLAS SETTINGS ---
                  if (activeMode == StorageMode.mongoAtlas) ...[
                    const Text(
                      'MongoDB Atlas Connection',
                      style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: uriController,
                      maxLines: 2,
                      style: const TextStyle(fontSize: 12),
                      decoration: const InputDecoration(
                        labelText: 'Connection URI',
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.link),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        FilledButton.tonalIcon(
                          onPressed: isTestingMongo
                              ? null
                              : () async {
                                  setDialogState(() {
                                    isTestingMongo = true;
                                    testResult = null;
                                  });
                                  await _mongo.setConfiguredUri(uriController.text.trim());
                                  final ok = await _mongo.connect();
                                  setDialogState(() {
                                    isTestingMongo = false;
                                    testResult = ok
                                        ? 'Connection successful!'
                                        : 'Failed: ${_mongo.lastErrorMessage}';
                                  });
                                },
                          icon: isTestingMongo
                              ? const SizedBox(
                                  width: 14,
                                  height: 14,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.check_circle_outline, size: 16),
                          label: const Text('Test Connection'),
                        ),
                        const SizedBox(width: 8),
                        TextButton(
                          onPressed: () {
                            setDialogState(() {
                              uriController.text = MongoService.defaultUri;
                            });
                          },
                          child: const Text('Reset Default'),
                        ),
                      ],
                    ),
                    if (testResult != null) ...[
                      const SizedBox(height: 6),
                      Text(
                        testResult!,
                        style: TextStyle(
                          fontSize: 11,
                          color: testResult!.contains('successful') ? Colors.green : Colors.red,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                    const SizedBox(height: 16),

                    // --- SECTION 4: REDUNDANCY FILTERING ---
                    const Text(
                      'Redundancy & Duplicate Filter',
                      style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Filters out repeated fingerprints from multiple devices to avoid dataset clutter.',
                      style: TextStyle(fontSize: 11, color: Colors.grey),
                    ),
                    const SizedBox(height: 8),
                    DropdownButtonFormField<RedundancyPolicy>(
                      initialValue: activePolicy,
                      decoration: const InputDecoration(
                        labelText: 'Action on Duplicate',
                        border: OutlineInputBorder(),
                        contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      ),
                      items: const [
                        DropdownMenuItem(
                          value: RedundancyPolicy.reject,
                          child: Text('Reject Duplicates (Strict)', style: TextStyle(fontSize: 13)),
                        ),
                        DropdownMenuItem(
                          value: RedundancyPolicy.averageAndMerge,
                          child: Text('Average & Refine (Multi-device)', style: TextStyle(fontSize: 13)),
                        ),
                        DropdownMenuItem(
                          value: RedundancyPolicy.overwrite,
                          child: Text('Overwrite Existing Point', style: TextStyle(fontSize: 13)),
                        ),
                      ],
                      onChanged: (val) {
                        if (val != null) {
                          setDialogState(() => activePolicy = val);
                        }
                      },
                    ),
                    const SizedBox(height: 12),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Text('Duplicate Radius:', style: TextStyle(fontSize: 12)),
                        Text('${spatialThresh.toStringAsFixed(0)} px',
                            style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
                      ],
                    ),
                    Slider(
                      value: spatialThresh,
                      min: 5.0,
                      max: 50.0,
                      divisions: 9,
                      label: '${spatialThresh.toInt()} px',
                      onChanged: (val) {
                        setDialogState(() => spatialThresh = val);
                      },
                    ),
                  ] else ...[
                    // Flask API URL field
                    const Text(
                      'Flask API URL',
                      style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                    ),
                    const SizedBox(height: 6),
                    TextField(
                      controller: flaskController,
                      decoration: const InputDecoration(
                        border: OutlineInputBorder(),
                        hintText: 'http://10.205.26.28:5050',
                        prefixIcon: Icon(Icons.dns_outlined),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () async {
                final newName = surveyorController.text.trim();
                if (newName.isNotEmpty) {
                  await DeviceService.setSurveyorName(newName);
                }

                await _repo.setStorageMode(activeMode);
                await MongoService.setRedundancyPolicy(activePolicy);
                await MongoService.setSpatialThreshold(spatialThresh);

                if (activeMode == StorageMode.mongoAtlas) {
                  await _mongo.setConfiguredUri(uriController.text.trim());
                } else {
                  await _repo.setFlaskUrl(flaskController.text.trim());
                }

                Navigator.of(ctx).pop();
                _refreshStats();
              },
              child: const Text('Save Settings'),
            ),
          ],
        ),
      ),
    );

    _refreshStats();
  }

  void _onSelectBuilding(BuildingConfig building) {
    if (!building.hasFloorPlans || building.floors.isEmpty) {
      showDialog(
        context: context,
        builder: (ctx) => AlertDialog(
          icon: const Icon(Icons.info_outline, size: 36, color: Colors.amber),
          title: Text('${building.name} Floor Plans'),
          content: Text(
            'Floor plans for ${building.name} (${building.code}) have not been uploaded yet.\n\n'
            'Fingerprint survey is currently available for SOT and SOS.',
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Got It'),
            ),
          ],
        ),
      );
      return;
    }

    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => FloorPickerScreen(
          building: building,
          backendBaseUrl: widget.backendBaseUrl,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final buildings = CampusData.buildings;
    final isConnected = _cloudStats['connected'] == true;
    final totalFps = _cloudStats['total_fingerprints'] ?? 0;
    final uniqueDevices = _cloudStats['unique_devices'] ?? 0;

    return Scaffold(
      appBar: AppBar(
        title: const Text('WiFi Fingerprint Survey'),
        actions: [
          IconButton(
            tooltip: 'Survey & Cloud Settings',
            icon: const Icon(Icons.settings_outlined),
            onPressed: _showSettingsDialog,
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        children: [
          // Header Banner with MongoDB Atlas Cloud Indicator
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              gradient: LinearGradient(
                colors: [
                  theme.colorScheme.primary,
                  theme.colorScheme.secondary,
                ],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              borderRadius: BorderRadius.circular(20),
              boxShadow: [
                BoxShadow(
                  color: theme.colorScheme.primary.withValues(alpha: 0.3),
                  blurRadius: 10,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.cloud_sync, color: Colors.white, size: 28),
                    const SizedBox(width: 10),
                    const Text(
                      'MongoDB Atlas Cloud',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 19,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const Spacer(),
                    InkWell(
                      onTap: () {
                        if (!isConnected && _cloudStats['error'] != null) {
                          _showConnectionErrorDialog(_cloudStats['error'].toString());
                        } else {
                          _refreshStats();
                        }
                      },
                      borderRadius: BorderRadius.circular(12),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 5,
                        ),
                        decoration: BoxDecoration(
                          color: Colors.black38,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Container(
                              width: 8,
                              height: 8,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: _isRefreshingStats
                                    ? Colors.amber
                                    : (isConnected ? Colors.greenAccent : Colors.redAccent),
                              ),
                            ),
                            const SizedBox(width: 6),
                            Text(
                              _isRefreshingStats
                                  ? 'Syncing'
                                  : (isConnected ? 'Atlas Live' : 'Offline'),
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),

                // Multi-device Cloud Metrics Row
                Row(
                  children: [
                    Expanded(
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'TOTAL FINGERPRINTS',
                              style: TextStyle(color: Colors.white70, fontSize: 10, fontWeight: FontWeight.bold),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '$totalFps',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 18,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'ACTIVE DEVICES',
                              style: TextStyle(color: Colors.white70, fontSize: 10, fontWeight: FontWeight.bold),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '$uniqueDevices',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 18,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),

                const SizedBox(height: 12),
                Text(
                  'Multi-device crowd-surveying enabled with smart duplicate filtering. Share APK with surveyors to capture points simultaneously.',
                  style: TextStyle(
                    color: Colors.white.withValues(alpha: 0.9),
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
                if (_deviceInfo != null) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Icon(Icons.phone_android, size: 14, color: Colors.white70),
                      const SizedBox(width: 5),
                      Text(
                        'This Device: ${_deviceInfo!.deviceName} (${_deviceInfo!.model})',
                        style: const TextStyle(
                          color: Colors.white70,
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 20),

          // Section Title
          Text(
            'SELECT A BUILDING TO SURVEY',
            style: theme.textTheme.labelMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
              fontWeight: FontWeight.bold,
              letterSpacing: 1.1,
            ),
          ),
          const SizedBox(height: 12),

          // Building Cards
          ...buildings.map((b) {
            final isAvailable = b.hasFloorPlans && b.floors.isNotEmpty;
            return Card(
              margin: const EdgeInsets.only(bottom: 14),
              elevation: 2,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: BorderSide(
                  color: isAvailable
                      ? theme.colorScheme.outlineVariant.withValues(alpha: 0.6)
                      : Colors.amber.withValues(alpha: 0.4),
                  width: isAvailable ? 1.0 : 1.5,
                ),
              ),
              child: InkWell(
                borderRadius: BorderRadius.circular(16),
                onTap: () => _onSelectBuilding(b),
                child: Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Row(
                    children: [
                      // Building Icon
                      Container(
                        width: 52,
                        height: 52,
                        decoration: BoxDecoration(
                          color: isAvailable
                              ? theme.colorScheme.primaryContainer
                              : Colors.amber.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Icon(
                          b.icon,
                          size: 28,
                          color: isAvailable
                              ? theme.colorScheme.primary
                              : Colors.amber.shade800,
                        ),
                      ),
                      const SizedBox(width: 16),

                      // Building Details
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text(
                                  b.code,
                                  style: theme.textTheme.titleMedium?.copyWith(
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 8,
                                    vertical: 2,
                                  ),
                                  decoration: BoxDecoration(
                                    color: isAvailable
                                        ? Colors.green.withValues(alpha: 0.15)
                                        : Colors.amber.withValues(alpha: 0.15),
                                    borderRadius: BorderRadius.circular(6),
                                  ),
                                  child: Text(
                                    isAvailable ? '${b.floors.length} Floors' : 'Pending',
                                    style: TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.bold,
                                      color: isAvailable ? Colors.green.shade800 : Colors.amber.shade900,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 4),
                            Text(
                              b.name,
                              style: theme.textTheme.bodyMedium?.copyWith(
                                fontWeight: FontWeight.w500,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              b.description,
                              style: theme.textTheme.bodySmall?.copyWith(
                                color: theme.colorScheme.onSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ),

                      Icon(
                        isAvailable ? Icons.arrow_forward_ios : Icons.lock_outline,
                        size: 16,
                        color: theme.colorScheme.outline,
                      ),
                    ],
                  ),
                ),
              ),
            );
          }),
        ],
      ),
    );
  }
}
