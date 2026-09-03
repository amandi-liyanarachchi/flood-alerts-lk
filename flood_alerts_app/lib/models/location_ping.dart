/// A single GPS reading queued for or sent to the server.
///
/// Also round-trips through shared_preferences as part of the failed-upload
/// queue, so [fromJson] must accept exactly what [toJson] produces.
class LocationPing {
  const LocationPing({
    required this.latitude,
    required this.longitude,
    required this.accuracy,
    required this.recordedAt,
    required this.source,
  });

  final double latitude;
  final double longitude;
  final double accuracy;
  final DateTime recordedAt;

  /// 'auto' for the throttled background stream, 'manual' for Send Now.
  final String source;

  factory LocationPing.fromJson(Map<String, dynamic> json) => LocationPing(
    latitude: (json['latitude'] as num?)?.toDouble() ?? 0,
    longitude: (json['longitude'] as num?)?.toDouble() ?? 0,
    accuracy: (json['accuracy'] as num?)?.toDouble() ?? 0,
    recordedAt:
        DateTime.tryParse(json['recordedAt'] as String? ?? '')?.toUtc() ??
        DateTime.now().toUtc(),
    source: json['source'] as String? ?? 'auto',
  );

  Map<String, dynamic> toJson() => {
    'latitude': latitude,
    'longitude': longitude,
    'accuracy': accuracy,
    'recordedAt': recordedAt.toUtc().toIso8601String(),
    'source': source,
  };
}
