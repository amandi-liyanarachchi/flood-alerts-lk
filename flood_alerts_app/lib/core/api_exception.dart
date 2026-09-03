/// Everything the UI is allowed to know about a failed request.
///
/// [message] comes straight from the server's error envelope and is safe to
/// show to the user; [code] is the machine-readable name from §7 (e.g.
/// NIC_ALREADY_REGISTERED) for the few cases handled by name.
class ApiException implements Exception {
  const ApiException(this.statusCode, this.message, {this.code});

  /// 0 means the request never reached the server (offline, DNS, timeout).
  final int statusCode;
  final String message;
  final String? code;

  bool get isUnauthorized => statusCode == 401;
  bool get isNetworkFailure => statusCode == 0;

  @override
  String toString() => message;
}
