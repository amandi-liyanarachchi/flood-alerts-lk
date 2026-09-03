enum AlertSeverity { low, moderate, high }

class FloodAlert {
  const FloodAlert({
    required this.id,
    required this.severity,
    required this.title,
    required this.message,
    required this.issuedAt,
  });

  final String id;
  final AlertSeverity severity;
  final String title;
  final String message;
  final DateTime issuedAt;

  factory FloodAlert.fromJson(Map<String, dynamic> json) => FloodAlert(
    id: json['id'] as String? ?? '',
    severity: _severityFrom(json['severity'] as String?),
    title: json['title'] as String? ?? 'Flood alert',
    message: json['message'] as String? ?? '',
    issuedAt:
        DateTime.tryParse(json['issuedAt'] as String? ?? '')?.toUtc() ??
        DateTime.now().toUtc(),
  );

  /// An unrecognised severity is treated as [AlertSeverity.high]: if the server
  /// bothered to send an alert, failing loud is safer than failing quiet.
  static AlertSeverity _severityFrom(String? raw) =>
      switch (raw?.toLowerCase()) {
        'low' => AlertSeverity.low,
        'moderate' => AlertSeverity.moderate,
        'high' => AlertSeverity.high,
        _ => AlertSeverity.high,
      };
}
