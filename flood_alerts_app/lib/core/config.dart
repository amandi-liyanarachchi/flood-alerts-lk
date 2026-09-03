/// App-wide constants. Never hardcode a URL or an interval anywhere else.
class Config {
  const Config._();

  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8080', // Android emulator -> host machine
  );

  static const String apiPrefix = '/api/v1';

  /// How often a location ping is actually uploaded. The position stream ticks
  /// far more often than this on purpose — see LocationService.
  static const Duration locationInterval = Duration(minutes: 10);

  static const Duration requestTimeout = Duration(seconds: 20);

  /// Upper bound on the offline ping queue. Oldest entries are dropped first.
  static const int maxQueuedPings = 50;
}
