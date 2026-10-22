import 'package:flutter/material.dart';
import '../models/building.dart';
import '../services/survey_repository.dart';
import 'survey_screen.dart';

/// Screen allowing the surveyor to select a floor within the chosen building.
class FloorPickerScreen extends StatefulWidget {
  final BuildingConfig building;
  final String? backendBaseUrl;

  const FloorPickerScreen({
    super.key,
    required this.building,
    this.backendBaseUrl,
  });

  @override
  State<FloorPickerScreen> createState() => _FloorPickerScreenState();
}

class _FloorPickerScreenState extends State<FloorPickerScreen> {
  final _repo = SurveyRepository();
  Map<int, int> _floorPointCounts = {};
  bool _isLoadingCounts = true;

  @override
  void initState() {
    super.initState();
    _loadFloorCounts();
  }

  Future<void> _loadFloorCounts() async {
    setState(() => _isLoadingCounts = true);
    final counts = <int, int>{};
    for (final floor in widget.building.floors) {
      final count = await _repo.getPointCount(
        floor.id,
        buildingName: widget.building.code,
      );
      counts[floor.id] = count;
    }
    if (mounted) {
      setState(() {
        _floorPointCounts = counts;
        _isLoadingCounts = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final building = widget.building;

    return Scaffold(
      appBar: AppBar(
        title: Text('${building.code} -- Select Floor'),
        actions: [
          IconButton(
            tooltip: 'Refresh floor counts',
            icon: _isLoadingCounts
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
            onPressed: _isLoadingCounts ? null : _loadFloorCounts,
          ),
        ],
      ),
      body: Column(
        children: [
          // Building Info Card
          Container(
            width: double.infinity,
            margin: const EdgeInsets.all(16),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: theme.colorScheme.primaryContainer.withValues(alpha: 0.4),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: theme.colorScheme.primary.withValues(alpha: 0.2),
              ),
            ),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: theme.colorScheme.primary,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(
                    building.icon,
                    color: theme.colorScheme.onPrimary,
                    size: 28,
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        building.name,
                        style: theme.textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        building.description,
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),

          // Floor List or Empty State
          Expanded(
            child: building.floors.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32.0),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.map_outlined,
                            size: 64,
                            color: theme.colorScheme.outline,
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'No Floor Plans Available',
                            style: theme.textTheme.titleMedium?.copyWith(
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Floor plan SVGs for ${building.code} have not been uploaded yet.\nPlease check back soon.',
                            textAlign: TextAlign.center,
                            style: theme.textTheme.bodyMedium?.copyWith(
                              color: theme.colorScheme.onSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    itemCount: building.floors.length,
                    itemBuilder: (context, index) {
                      final floor = building.floors[index];
                      final count = _floorPointCounts[floor.id] ?? 0;

                      return Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        elevation: 1,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(14),
                          side: BorderSide(
                            color: theme.colorScheme.outlineVariant.withValues(alpha: 0.5),
                          ),
                        ),
                        child: ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 8,
                          ),
                          leading: CircleAvatar(
                            backgroundColor: theme.colorScheme.primaryContainer,
                            foregroundColor: theme.colorScheme.primary,
                            child: Text(
                              '${floor.floorNumber}',
                              style: const TextStyle(fontWeight: FontWeight.bold),
                            ),
                          ),
                          title: Row(
                            children: [
                              Expanded(
                                child: Text(
                                  floor.label,
                                  style: const TextStyle(fontWeight: FontWeight.w600),
                                ),
                              ),
                              Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 8,
                                  vertical: 3,
                                ),
                                decoration: BoxDecoration(
                                  color: count > 0
                                      ? Colors.green.withValues(alpha: 0.15)
                                      : Colors.grey.withValues(alpha: 0.15),
                                  borderRadius: BorderRadius.circular(8),
                                ),
                                child: Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Icon(
                                      Icons.cloud_done,
                                      size: 14,
                                      color: count > 0 ? Colors.green.shade700 : Colors.grey,
                                    ),
                                    const SizedBox(width: 4),
                                    Text(
                                      '$count pts',
                                      style: TextStyle(
                                        fontSize: 11,
                                        fontWeight: FontWeight.bold,
                                        color: count > 0 ? Colors.green.shade800 : Colors.grey.shade700,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                          subtitle: Text(
                            'ViewBox: ${floor.svgWidth.toInt()} × ${floor.svgHeight.toInt()} • SVG floor plan',
                            style: TextStyle(
                              fontSize: 12,
                              color: theme.colorScheme.onSurfaceVariant,
                            ),
                          ),
                          trailing: const Icon(Icons.arrow_forward_ios, size: 16),
                          onTap: () async {
                            await Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => SurveyScreen(
                                  svgAssetPath: floor.svgAssetPath,
                                  floorId: floor.id,
                                  buildingName: building.code,
                                  floorLabel: '${building.code} -- ${floor.label}',
                                  backendBaseUrl: widget.backendBaseUrl,
                                  svgWidth: floor.svgWidth,
                                  svgHeight: floor.svgHeight,
                                ),
                              ),
                            );
                            _loadFloorCounts();
                          },
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
