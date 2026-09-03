/// One crowdsourced answer to "Is there flooding in your area right now?".
///
/// Coordinates are nullable: if no fix is available the server falls back to
/// the user's last known ping.
class FeedbackAnswer {
  const FeedbackAnswer({
    required this.floodPresent,
    required this.latitude,
    required this.longitude,
    required this.answeredAt,
  });

  final bool floodPresent;
  final double? latitude;
  final double? longitude;
  final DateTime answeredAt;

  Map<String, dynamic> toJson() => {
    'floodPresent': floodPresent,
    'latitude': latitude,
    'longitude': longitude,
    'answeredAt': answeredAt.toUtc().toIso8601String(),
  };
}
