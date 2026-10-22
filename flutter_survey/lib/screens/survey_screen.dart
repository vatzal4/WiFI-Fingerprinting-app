import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import '../models/fingerprint.dart';
import '../services/device_service.dart';
import '../services/similarity_service.dart';
import '../services/survey_repository.dart';
import '../services/wifi_scanner_service.dart';

class SurveyScreen extends StatefulWidget {
  final String svgAssetPath; // e.g. "assets/floorplans/sot/floor-0-ground.svg"
  final int floorId;
  final String? backendBaseUrl; // Optional fallback
  final double svgWidth; // native SVG viewBox width
  final double svgHeight; // native SVG viewBox height
  final String? buildingName; // e.g. "SOT", "SOS"
  final String? floorLabel; // e.g. "Floor 0 -- Ground floor"

  const SurveyScreen({
    super.key,
    required this.svgAssetPath,
    required this.floorId,
    this.backendBaseUrl,
    this.buildingName,
    this.floorLabel,
    this.svgWidth = 1750.0,
    this.svgHeight = 899.0,
  });

  @override
  State<SurveyScreen> createState() => _SurveyScreenState();
}

class _SurveyScreenState extends State<SurveyScreen>
    with SingleTickerProviderStateMixin {
  final _wifiScanner = WifiScannerService();
  final _repo = SurveyRepository();

  final TransformationController _transformationController =
      TransformationController();
  late final AnimationController _animationController;
  Animation<Matrix4>? _animation;

  static const double _minScale = 0.5;
  static const double _maxScale = 8.0;

  Offset? _tappedSvgPoint; // Selected point in SVG viewBox coordinates
  bool _isCapturing = false;
  bool _isLoadingPoints = true;
  bool _showExistingPoints = true;
  int _sessionSavedCount = 0;
  Size _viewportSize = Size.zero;

  List<Fingerprint> _existingPoints = [];
  String? _surveyorName;

  @override
  void initState() {
    super.initState();
    _wifiScanner.requestPermission();
    _loadSurveyorInfo();
    _loadExistingPoints();

    _animationController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 250),
    )..addListener(() {
        if (_animation != null) {
          _transformationController.value = _animation!.value;
        }
      });
  }

  @override
  void dispose() {
    _animationController.dispose();
    _transformationController.dispose();
    super.dispose();
  }

  Future<void> _loadSurveyorInfo() async {
    final name = await DeviceService.getSurveyorName();
    if (mounted) setState(() => _surveyorName = name);
  }

  Future<void> _loadExistingPoints() async {
    setState(() => _isLoadingPoints = true);
    try {
      final points = await _repo.getSurveyedPoints(
        widget.floorId,
        buildingName: widget.buildingName,
      );
      if (mounted) {
        setState(() {
          _existingPoints = points;
          _isLoadingPoints = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isLoadingPoints = false);
    }
  }

  void _animateToMatrix(Matrix4 targetMatrix) {
    _animationController.reset();
    _animation = Matrix4Tween(
      begin: _transformationController.value,
      end: targetMatrix,
    ).animate(CurvedAnimation(
      parent: _animationController,
      curve: Curves.easeOutCubic,
    ));
    _animationController.forward();
  }

  void _zoom(double factor) {
    if (_viewportSize == Size.zero) return;

    final currentMatrix = _transformationController.value;
    final currentScale = currentMatrix.getMaxScaleOnAxis();
    final targetScale = (currentScale * factor).clamp(_minScale, _maxScale);
    if ((targetScale - currentScale).abs() < 0.001) return;

    final effectiveFactor = targetScale / currentScale;
    final focalPoint =
        Offset(_viewportSize.width / 2.0, _viewportSize.height / 2.0);

    final translationToFocal =
        Matrix4.translationValues(focalPoint.dx, focalPoint.dy, 0.0);
    final scaleMatrix =
        Matrix4.diagonal3Values(effectiveFactor, effectiveFactor, 1.0);
    final translationBack =
        Matrix4.translationValues(-focalPoint.dx, -focalPoint.dy, 0.0);

    final Matrix4 newMatrix =
        translationToFocal * scaleMatrix * translationBack * currentMatrix;

    _animateToMatrix(newMatrix);
  }

  void _resetZoom() {
    _animateToMatrix(Matrix4.identity());
  }

  void _focusOnPoint(Offset pt, double fittedWidth, double fittedHeight) {
    if (_viewportSize == Size.zero) return;

    final pinX = (pt.dx / widget.svgWidth) * fittedWidth;
    final pinY = (pt.dy / widget.svgHeight) * fittedHeight;

    final childOffsetX = (_viewportSize.width - fittedWidth) / 2.0;
    final childOffsetY = (_viewportSize.height - fittedHeight) / 2.0;

    final untransformedPinX = childOffsetX + pinX;
    final untransformedPinY = childOffsetY + pinY;

    final currentScale = _transformationController.value.getMaxScaleOnAxis();
    final targetScale = currentScale < 2.0 ? 2.5 : currentScale;

    final tx = _viewportSize.width / 2.0 - targetScale * untransformedPinX;
    final ty = _viewportSize.height / 2.0 - targetScale * untransformedPinY;

    final targetMatrix = Matrix4.translationValues(tx, ty, 0.0) *
        Matrix4.diagonal3Values(targetScale, targetScale, 1.0);

    _animateToMatrix(targetMatrix);
  }

  void _onTapFloorPlan(Offset localPos, Size canvasSize) {
    final clampedX = localPos.dx.clamp(0.0, canvasSize.width);
    final clampedY = localPos.dy.clamp(0.0, canvasSize.height);

    final svgX = (clampedX / canvasSize.width) * widget.svgWidth;
    final svgY = (clampedY / canvasSize.height) * widget.svgHeight;

    setState(() {
      _tappedSvgPoint = Offset(svgX, svgY);
    });
  }

  Fingerprint? get _nearestExistingPoint {
    if (_tappedSvgPoint == null || _existingPoints.isEmpty) return null;
    Fingerprint? closest;
    double minDist = double.infinity;
    for (final p in _existingPoints) {
      final d = SimilarityService.calculateSpatialDistance(
        _tappedSvgPoint!.dx,
        _tappedSvgPoint!.dy,
        p.x,
        p.y,
      );
      if (d < minDist) {
        minDist = d;
        closest = p;
      }
    }
    return minDist <= 35.0 ? closest : null;
  }

  double? get _nearestDistance {
    final nearest = _nearestExistingPoint;
    if (nearest == null || _tappedSvgPoint == null) return null;
    return SimilarityService.calculateSpatialDistance(
      _tappedSvgPoint!.dx,
      _tappedSvgPoint!.dy,
      nearest.x,
      nearest.y,
    );
  }

  Future<void> _captureAndUpload() async {
    if (_tappedSvgPoint == null) {
      _showMessage('Tap a point on the floor plan first to pick a location');
      return;
    }

    if (!_wifiScanner.isScanSupportedPlatform) {
      _showMessage(
        'WiFi scanning is not available on this platform (Android required for real AP scanning).',
      );
      return;
    }

    setState(() => _isCapturing = true);

    try {
      final hasPermission = await _wifiScanner.hasPermission();
      if (!hasPermission) {
        final granted = await _wifiScanner.requestPermission();
        if (!granted) {
          _showMessage('Location permission is required to scan WiFi');
          return;
        }
      }

      await _wifiScanner.requestFreshScan();
      await Future.delayed(const Duration(seconds: 2));

      final readings = await _wifiScanner.getCurrentReadings();
      if (readings.isEmpty) {
        _showMessage('No access points visible at this point. Try scanning again.');
        return;
      }

      final result = await _repo.saveFingerprint(
        floorId: widget.floorId,
        buildingName: widget.buildingName,
        floorLabel: widget.floorLabel,
        x: _tappedSvgPoint!.dx,
        y: _tappedSvgPoint!.dy,
        readings: readings,
      );

      if (result.success) {
        setState(() {
          _sessionSavedCount++;
        });
        await _loadExistingPoints();

        if (result.wasMerged) {
          _showRedundancyFeedbackDialog(
            title: 'Point Refined & Merged',
            isSuccess: true,
            message: result.message,
            readingsCount: readings.length,
          );
        } else {
          final locationTag = widget.buildingName != null
              ? '${widget.buildingName} ${widget.floorLabel ?? 'Floor ${widget.floorId}'}'
              : 'Floor ${widget.floorId}';
          _showMessage(
            'Saved -- ${readings.length} APs at $locationTag ($tappedXY)',
          );
        }
      } else if (result.isRedundant) {
        _showRedundancyFeedbackDialog(
          title: 'Duplicate Filtered Out',
          isSuccess: false,
          message: result.message,
          readingsCount: readings.length,
          eval: result.evaluation,
        );
      } else {
        _showMessage(result.message);
      }
    } catch (e) {
      _showMessage('Capture error: $e');
    } finally {
      if (mounted) {
        setState(() => _isCapturing = false);
      }
    }
  }

  void _showRedundancyFeedbackDialog({
    required String title,
    required bool isSuccess,
    required String message,
    required int readingsCount,
    SimilarityEvaluation? eval,
  }) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        icon: Icon(
          isSuccess ? Icons.auto_awesome : Icons.filter_alt,
          color: isSuccess ? Colors.teal : Colors.orange,
          size: 38,
        ),
        title: Text(title),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              message,
              style: const TextStyle(fontSize: 14),
            ),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Theme.of(ctx)
                    .colorScheme
                    .surfaceContainerHighest
                    .withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Column(
                children: [
                  _infoRow('Scanned APs', '$readingsCount access points'),
                  if (eval != null && eval.spatialDistance != double.infinity) ...[
                    const SizedBox(height: 6),
                    _infoRow(
                      'Spatial Distance',
                      '${eval.spatialDistance.toStringAsFixed(1)} px',
                    ),
                    const SizedBox(height: 6),
                    _infoRow(
                      'Radio Similarity',
                      '${(eval.overallSimilarity * 100).toStringAsFixed(0)}%',
                    ),
                    const SizedBox(height: 6),
                    _infoRow(
                      'BSSID Overlap',
                      '${(eval.jaccardOverlap * 100).toStringAsFixed(0)}%',
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
        actions: [
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  Widget _infoRow(String label, String value) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: const TextStyle(fontSize: 12, color: Colors.grey)),
        Text(value,
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
      ],
    );
  }

  void _showPointDetailsSheet(Fingerprint pt) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) {
        final theme = Theme.of(ctx);
        final devName = pt.deviceInfo?['device_name'] ?? 'Unknown Surveyor';
        final devModel = pt.deviceInfo?['model'] ?? '';
        final date = pt.capturedAt.toLocal().toString().split('.')[0];

        return Container(
          padding: const EdgeInsets.all(20),
          constraints: BoxConstraints(
            maxHeight: MediaQuery.of(ctx).size.height * 0.7,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Colors.green.withValues(alpha: 0.2),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.check_circle,
                        color: Colors.green, size: 28),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Surveyed Point (${pt.x.toStringAsFixed(1)}, ${pt.y.toStringAsFixed(1)})',
                          style: theme.textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        Text(
                          'Captured by $devName',
                          style: TextStyle(
                            fontSize: 12,
                            color: theme.colorScheme.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (pt.sampleCount > 1)
                    Chip(
                      avatar: const Icon(Icons.layers, size: 14),
                      label: Text('${pt.sampleCount}x Merged'),
                      backgroundColor: Colors.teal.shade50,
                    ),
                ],
              ),
              const SizedBox(height: 16),
              const Divider(),
              const SizedBox(height: 8),

              // Metadata Grid
              Row(
                children: [
                  Expanded(
                    child: _metaCard('Device', devModel.isEmpty ? devName : devModel, Icons.phone_android),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: _metaCard('Captured At', date, Icons.access_time),
                  ),
                ],
              ),
              const SizedBox(height: 12),

              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Visible Access Points (${pt.readings.length})',
                    style: theme.textTheme.labelLarge?.copyWith(
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  Text(
                    'Signal (dBm)',
                    style: TextStyle(
                      fontSize: 11,
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),

              // AP readings list
              Expanded(
                child: ListView.separated(
                  itemCount: pt.readings.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (_, index) {
                    final ap = pt.readings[index];
                    return ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(
                        Icons.wifi,
                        size: 20,
                        color: ap.rssi > -60
                            ? Colors.green
                            : (ap.rssi > -75 ? Colors.orange : Colors.grey),
                      ),
                      title: Text(
                        ap.ssid,
                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                      ),
                      subtitle: Text(
                        ap.bssid,
                        style: const TextStyle(fontSize: 11, fontFamily: 'monospace'),
                      ),
                      trailing: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: (ap.rssi > -60 ? Colors.green : Colors.blueGrey)
                              .withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          '${ap.rssi} dBm',
                          style: TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 12,
                            color: ap.rssi > -60 ? Colors.green.shade800 : Colors.blueGrey.shade800,
                          ),
                        ),
                      ),
                    );
                  },
                ),
              ),

              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: () async {
                    Navigator.of(ctx).pop();
                    if (pt.id != null) {
                      final confirmed = await showDialog<bool>(
                        context: context,
                        builder: (dCtx) => AlertDialog(
                          title: const Text('Delete Point?'),
                          content: Text(
                            'Are you sure you want to remove this surveyed point (${pt.x.toStringAsFixed(1)}, ${pt.y.toStringAsFixed(1)}) from MongoDB Atlas?',
                          ),
                          actions: [
                            TextButton(
                              onPressed: () => Navigator.of(dCtx).pop(false),
                              child: const Text('Cancel'),
                            ),
                            FilledButton(
                              style: FilledButton.styleFrom(backgroundColor: Colors.red),
                              onPressed: () => Navigator.of(dCtx).pop(true),
                              child: const Text('Delete'),
                            ),
                          ],
                        ),
                      );

                      if (confirmed == true) {
                        final ok = await _repo.deletePoint(pt.id!);
                        if (ok) {
                          _showMessage('Point deleted from MongoDB Atlas');
                          await _loadExistingPoints();
                        }
                      }
                    }
                  },
                  icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
                  label: const Text('Delete Survey Point', style: TextStyle(color: Colors.redAccent)),
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _metaCard(String label, String value, IconData icon) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.4),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Icon(icon, size: 18, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: const TextStyle(fontSize: 10, color: Colors.grey)),
                Text(
                  value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String get tappedXY => _tappedSvgPoint == null
      ? ''
      : '${_tappedSvgPoint!.dx.toStringAsFixed(1)}, ${_tappedSvgPoint!.dy.toStringAsFixed(1)}';

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(message),
      behavior: SnackBarBehavior.floating,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final appBarTitle = widget.floorLabel != null
        ? (widget.buildingName != null &&
                !widget.floorLabel!.contains(widget.buildingName!)
            ? '${widget.buildingName} -- ${widget.floorLabel}'
            : widget.floorLabel!)
        : (widget.buildingName != null
            ? '${widget.buildingName} -- Floor ${widget.floorId}'
            : 'Survey -- Floor ${widget.floorId}');

    final nearest = _nearestExistingPoint;
    final nearestDist = _nearestDistance;

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(appBarTitle, style: const TextStyle(fontSize: 16)),
            if (_surveyorName != null)
              Text(
                'Surveyor: $_surveyorName',
                style: TextStyle(
                  fontSize: 11,
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
          ],
        ),
        actions: [
          // Toggle existing points visibility
          IconButton(
            tooltip: _showExistingPoints ? 'Hide existing points' : 'Show existing points',
            icon: Icon(
              _showExistingPoints ? Icons.visibility : Icons.visibility_off,
              size: 20,
            ),
            onPressed: () {
              setState(() => _showExistingPoints = !_showExistingPoints);
            },
          ),
          // Refresh points from MongoDB Atlas
          IconButton(
            tooltip: 'Refresh points from Cloud',
            icon: _isLoadingPoints
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
            onPressed: _isLoadingPoints ? null : _loadExistingPoints,
          ),
          // Total count badge
          Padding(
            padding: const EdgeInsets.only(right: 12, left: 4),
            child: Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  _sessionSavedCount > 0
                      ? '${_existingPoints.length} Pts (+$_sessionSavedCount)'
                      : '${_existingPoints.length} Pts',
                  style: TextStyle(
                    color: theme.colorScheme.onPrimaryContainer,
                    fontWeight: FontWeight.bold,
                    fontSize: 12,
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          // SVG Interactive Floor Plan Viewer with Zoom & Pan
          Expanded(
            child: LayoutBuilder(
              builder: (context, constraints) {
                _viewportSize = Size(constraints.maxWidth, constraints.maxHeight);

                final svgAspect = widget.svgWidth / widget.svgHeight;
                final viewportAspect =
                    constraints.maxWidth / constraints.maxHeight;

                final double fittedWidth;
                final double fittedHeight;
                if (viewportAspect > svgAspect) {
                  fittedHeight = constraints.maxHeight;
                  fittedWidth = fittedHeight * svgAspect;
                } else {
                  fittedWidth = constraints.maxWidth;
                  fittedHeight = fittedWidth / svgAspect;
                }

                final pinX = _tappedSvgPoint != null
                    ? (_tappedSvgPoint!.dx / widget.svgWidth) * fittedWidth
                    : null;
                final pinY = _tappedSvgPoint != null
                    ? (_tappedSvgPoint!.dy / widget.svgHeight) * fittedHeight
                    : null;

                return Stack(
                  children: [
                    // Main Zoomable & Pannable Floor Plan
                    Positioned.fill(
                      child: InteractiveViewer(
                        transformationController: _transformationController,
                        minScale: _minScale,
                        maxScale: _maxScale,
                        boundaryMargin: const EdgeInsets.all(250),
                        clipBehavior: Clip.hardEdge,
                        child: Center(
                          child: SizedBox(
                            width: fittedWidth,
                            height: fittedHeight,
                            child: GestureDetector(
                              behavior: HitTestBehavior.opaque,
                              onTapUp: (details) {
                                _onTapFloorPlan(
                                  details.localPosition,
                                  Size(fittedWidth, fittedHeight),
                                );
                              },
                              child: Stack(
                                clipBehavior: Clip.none,
                                children: [
                                  // Base SVG Floor Plan
                                  Positioned.fill(
                                    child: SvgPicture.asset(
                                      widget.svgAssetPath,
                                      width: fittedWidth,
                                      height: fittedHeight,
                                      fit: BoxFit.fill,
                                    ),
                                  ),

                                  // Layer 1: Already Surveyed Points in MongoDB Atlas
                                  if (_showExistingPoints)
                                    ..._existingPoints.map((pt) {
                                      final ptX = (pt.x / widget.svgWidth) * fittedWidth;
                                      final ptY = (pt.y / widget.svgHeight) * fittedHeight;

                                      return Positioned(
                                        left: ptX - 12,
                                        top: ptY - 12,
                                        width: 24,
                                        height: 24,
                                        child: GestureDetector(
                                          behavior: HitTestBehavior.opaque,
                                          onTap: () => _showPointDetailsSheet(pt),
                                          child: ListenableBuilder(
                                            listenable: _transformationController,
                                            builder: (context, _) {
                                              final scale = _transformationController
                                                  .value
                                                  .getMaxScaleOnAxis()
                                                  .clamp(_minScale, _maxScale);
                                              return Transform.scale(
                                                scale: 1.0 / sqrt(scale),
                                                child: Container(
                                                  decoration: BoxDecoration(
                                                    color: Colors.green.shade600,
                                                    shape: BoxShape.circle,
                                                    border: Border.all(
                                                      color: Colors.white,
                                                      width: 2.0,
                                                    ),
                                                    boxShadow: const [
                                                      BoxShadow(
                                                        color: Colors.black38,
                                                        blurRadius: 4,
                                                        offset: Offset(0, 1),
                                                      ),
                                                    ],
                                                  ),
                                                  child: Center(
                                                    child: pt.sampleCount > 1
                                                        ? Text(
                                                            '${pt.sampleCount}',
                                                            style: const TextStyle(
                                                              color: Colors.white,
                                                              fontSize: 9,
                                                              fontWeight: FontWeight.bold,
                                                            ),
                                                          )
                                                        : Container(
                                                            width: 4,
                                                            height: 4,
                                                            decoration: const BoxDecoration(
                                                              color: Colors.white,
                                                              shape: BoxShape.circle,
                                                            ),
                                                          ),
                                                  ),
                                                ),
                                              );
                                            },
                                          ),
                                        ),
                                      );
                                    }),

                                  // Layer 2: Location Pin Marker & Precision Crosshair for current tap
                                  if (pinX != null && pinY != null) ...[
                                    Positioned(
                                      left: pinX - 4,
                                      top: pinY - 4,
                                      child: IgnorePointer(
                                        child: Container(
                                          width: 8,
                                          height: 8,
                                          decoration: BoxDecoration(
                                            color: Colors.redAccent,
                                            shape: BoxShape.circle,
                                            border: Border.all(
                                                color: Colors.white, width: 1.5),
                                            boxShadow: const [
                                              BoxShadow(
                                                  color: Colors.black45,
                                                  blurRadius: 3),
                                            ],
                                          ),
                                        ),
                                      ),
                                    ),

                                    // Counter-scaled Pin & Coordinate Tag
                                    Positioned(
                                      left: pinX - 40,
                                      top: pinY - 68,
                                      width: 80,
                                      height: 68,
                                      child: IgnorePointer(
                                        child: ListenableBuilder(
                                          listenable: _transformationController,
                                          builder: (context, _) {
                                            final currentScale =
                                                _transformationController.value
                                                    .getMaxScaleOnAxis()
                                                    .clamp(_minScale, _maxScale);
                                            return Transform.scale(
                                              scale: 1.0 / currentScale,
                                              alignment: Alignment.bottomCenter,
                                              child: Column(
                                                mainAxisSize: MainAxisSize.min,
                                                children: [
                                                  Container(
                                                    padding:
                                                        const EdgeInsets.symmetric(
                                                            horizontal: 6,
                                                            vertical: 2),
                                                    decoration: BoxDecoration(
                                                      color: Colors.black87,
                                                      borderRadius:
                                                          BorderRadius.circular(4),
                                                      boxShadow: const [
                                                        BoxShadow(
                                                          color: Colors.black38,
                                                          blurRadius: 3,
                                                          offset: Offset(0, 1),
                                                        ),
                                                      ],
                                                    ),
                                                    child: Text(
                                                      '${_tappedSvgPoint!.dx.toStringAsFixed(0)}, ${_tappedSvgPoint!.dy.toStringAsFixed(0)}',
                                                      style: const TextStyle(
                                                        color: Colors.white,
                                                        fontSize: 9,
                                                        fontWeight:
                                                            FontWeight.bold,
                                                      ),
                                                    ),
                                                  ),
                                                  const Icon(
                                                    Icons.location_pin,
                                                    color: Colors.redAccent,
                                                    size: 34,
                                                    shadows: [
                                                      Shadow(
                                                        color: Colors.black45,
                                                        blurRadius: 4,
                                                        offset: Offset(0, 2),
                                                      ),
                                                    ],
                                                  ),
                                                ],
                                              ),
                                            );
                                          },
                                        ),
                                      ),
                                    ),
                                  ],
                                ],
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),

                    // Top-Left Floating Info & Zoom Badge
                    Positioned(
                      top: 12,
                      left: 12,
                      child: ListenableBuilder(
                        listenable: _transformationController,
                        builder: (context, _) {
                          final currentScale = _transformationController.value
                              .getMaxScaleOnAxis();
                          final percent = (currentScale * 100).round();
                          return Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 10, vertical: 6),
                            decoration: BoxDecoration(
                              color: theme.colorScheme.surface.withValues(alpha: 0.9),
                              borderRadius: BorderRadius.circular(16),
                              boxShadow: const [
                                BoxShadow(
                                  color: Colors.black12,
                                  blurRadius: 8,
                                  offset: Offset(0, 2),
                                ),
                              ],
                              border: Border.all(
                                color: theme.colorScheme.outlineVariant
                                    .withValues(alpha: 0.5),
                              ),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(
                                  Icons.zoom_in,
                                  size: 16,
                                  color: theme.colorScheme.primary,
                                ),
                                const SizedBox(width: 4),
                                Text(
                                  '$percent%',
                                  style: TextStyle(
                                    fontSize: 12,
                                    fontWeight: FontWeight.bold,
                                    color: theme.colorScheme.onSurface,
                                  ),
                                ),
                                if (currentScale > 1.05 || currentScale < 0.95) ...[
                                  const SizedBox(width: 6),
                                  GestureDetector(
                                    onTap: _resetZoom,
                                    child: Container(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 6, vertical: 2),
                                      decoration: BoxDecoration(
                                        color: theme.colorScheme.secondaryContainer,
                                        borderRadius: BorderRadius.circular(8),
                                      ),
                                      child: Text(
                                        'Reset',
                                        style: TextStyle(
                                          fontSize: 10,
                                          fontWeight: FontWeight.bold,
                                          color: theme
                                              .colorScheme.onSecondaryContainer,
                                        ),
                                      ),
                                    ),
                                  ),
                                ],
                              ],
                            ),
                          );
                        },
                      ),
                    ),

                    // Floating Legend Badge
                    Positioned(
                      top: 12,
                      right: 12,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: theme.colorScheme.surface.withValues(alpha: 0.9),
                          borderRadius: BorderRadius.circular(16),
                          boxShadow: const [
                            BoxShadow(
                              color: Colors.black12,
                              blurRadius: 8,
                              offset: Offset(0, 2),
                            ),
                          ],
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Container(
                              width: 10,
                              height: 10,
                              decoration: BoxDecoration(
                                color: Colors.green.shade600,
                                shape: BoxShape.circle,
                                border: Border.all(color: Colors.white, width: 1.5),
                              ),
                            ),
                            const SizedBox(width: 6),
                            Text(
                              'Surveyed (${_existingPoints.length})',
                              style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
                            ),
                          ],
                        ),
                      ),
                    ),

                    // Floating On-Screen Zoom & Center Toolbar
                    Positioned(
                      right: 12,
                      bottom: 12,
                      child: Card(
                        elevation: 4,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(16),
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            // Center on Selected Pin (if present)
                            if (_tappedSvgPoint != null) ...[
                              IconButton(
                                icon: const Icon(Icons.my_location),
                                tooltip: 'Focus on Pin',
                                color: Colors.redAccent,
                                onPressed: () =>
                                    _focusOnPoint(_tappedSvgPoint!, fittedWidth, fittedHeight),
                              ),
                              const Divider(height: 1, indent: 8, endIndent: 8),
                            ],

                            // Zoom In Button (+)
                            IconButton(
                              icon: const Icon(Icons.add),
                              tooltip: 'Zoom In',
                              onPressed: () => _zoom(1.5),
                            ),
                            const Divider(height: 1, indent: 8, endIndent: 8),

                            // Zoom Out Button (-)
                            IconButton(
                              icon: const Icon(Icons.remove),
                              tooltip: 'Zoom Out',
                              onPressed: () => _zoom(1.0 / 1.5),
                            ),
                            const Divider(height: 1, indent: 8, endIndent: 8),

                            // Fit to Screen / Reset Zoom Button
                            IconButton(
                              icon: const Icon(Icons.restart_alt),
                              tooltip: 'Fit to Screen',
                              onPressed: _resetZoom,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                );
              },
            ),
          ),

          // Bottom Action & Selection Details Panel
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: theme.colorScheme.surface,
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.06),
                  blurRadius: 10,
                  offset: const Offset(0, -3),
                ),
              ],
            ),
            child: SafeArea(
              top: false,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // Proximity / Redundancy warning if tapped near existing point
                  if (nearest != null && nearestDist != null && nearestDist <= 20.0)
                    Container(
                      margin: const EdgeInsets.only(bottom: 10),
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(
                        color: Colors.amber.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: Colors.amber.shade700, width: 1),
                      ),
                      child: Row(
                        children: [
                          Icon(Icons.warning_amber_rounded,
                              size: 18, color: Colors.amber.shade900),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              'Near surveyed point (${nearestDist.toStringAsFixed(1)}px away). Redundancy filter is active.',
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                color: Colors.amber.shade900,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                  // Location Status Card
                  Container(
                    width: double.infinity,
                    padding:
                        const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    decoration: BoxDecoration(
                      color: _tappedSvgPoint != null
                          ? theme.colorScheme.primaryContainer.withValues(alpha: 0.5)
                          : theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.4),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: _tappedSvgPoint != null
                            ? theme.colorScheme.primary.withValues(alpha: 0.3)
                            : theme.colorScheme.outlineVariant.withValues(alpha: 0.3),
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _tappedSvgPoint != null
                              ? Icons.pin_drop
                              : Icons.touch_app_outlined,
                          color: _tappedSvgPoint != null
                              ? theme.colorScheme.primary
                              : theme.colorScheme.outline,
                          size: 22,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _tappedSvgPoint != null
                                    ? 'Selected: ($tappedXY)'
                                    : 'Tap map to pick a location',
                                style: TextStyle(
                                  fontWeight: FontWeight.bold,
                                  fontSize: 13,
                                  color: theme.colorScheme.onSurface,
                                ),
                              ),
                              Text(
                                _tappedSvgPoint != null
                                    ? 'Tap green dots to inspect existing points'
                                    : 'Green dots show points already surveyed across all devices',
                                style: TextStyle(
                                  fontSize: 11,
                                  color: theme.colorScheme.onSurfaceVariant,
                                ),
                              ),
                            ],
                          ),
                        ),
                        if (_tappedSvgPoint != null)
                          IconButton(
                            icon: const Icon(Icons.close, size: 18),
                            tooltip: 'Clear selection',
                            onPressed: () {
                              setState(() {
                                _tappedSvgPoint = null;
                              });
                            },
                          ),
                      ],
                    ),
                  ),

                  const SizedBox(height: 12),

                  // Capture WiFi Fingerprint Button
                  SizedBox(
                    width: double.infinity,
                    height: 48,
                    child: FilledButton.icon(
                      onPressed: _isCapturing || _tappedSvgPoint == null
                          ? null
                          : _captureAndUpload,
                      icon: _isCapturing
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            )
                          : const Icon(Icons.wifi),
                      label: Text(
                        _isCapturing
                            ? 'Scanning WiFi...'
                            : _tappedSvgPoint == null
                                ? 'Pick a location first'
                                : 'Capture WiFi fingerprint here',
                        style: const TextStyle(fontWeight: FontWeight.bold),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
