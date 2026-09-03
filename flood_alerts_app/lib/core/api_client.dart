import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'api_exception.dart';
import 'config.dart';

/// Thin wrapper over `package:http`. The only place in the app that talks
/// HTTP: services call this, screens never do.
class ApiClient {
  ApiClient({http.Client? httpClient}) : _http = httpClient ?? http.Client();

  final http.Client _http;

  String? _token;

  /// Invoked before a 401 is thrown, so AuthProvider can tear down the session
  /// exactly once no matter which service made the call.
  void Function()? onUnauthorized;

  void setToken(String? token) => _token = token;

  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) =>
      _send('GET', path, query: query);

  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) =>
      _send('POST', path, body: body);

  Future<Map<String, dynamic>> delete(
    String path, {
    Map<String, dynamic>? body,
  }) => _send('DELETE', path, body: body);

  void close() => _http.close();

  Future<Map<String, dynamic>> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    var uri = Uri.parse('${Config.apiBaseUrl}${Config.apiPrefix}$path');
    if (query != null && query.isNotEmpty) {
      uri = uri.replace(queryParameters: query);
    }

    final headers = <String, String>{
      HttpHeaders.acceptHeader: 'application/json',
      if (body != null)
        HttpHeaders.contentTypeHeader: 'application/json; charset=utf-8',
      if (_token != null && _token!.isNotEmpty)
        HttpHeaders.authorizationHeader: 'Bearer $_token',
    };

    final request = http.Request(method, uri)..headers.addAll(headers);
    if (body != null) request.body = jsonEncode(body);

    http.Response response;
    try {
      final streamed = await _http.send(request).timeout(Config.requestTimeout);
      response = await http.Response.fromStream(streamed);
    } on TimeoutException {
      throw const ApiException(0, 'The server took too long to respond.');
    } on SocketException {
      throw const ApiException(
        0,
        'Cannot reach the server. Check your internet connection.',
      );
    } on http.ClientException {
      throw const ApiException(
        0,
        'Cannot reach the server. Check your internet connection.',
      );
    }

    // A 401 from /auth/* means "those credentials are wrong", not "your
    // session expired". Tearing the session down there would clear the
    // pending ping queue because someone mistyped their password.
    return _handle(response, isAuthEndpoint: path.startsWith('/auth/'));
  }

  Map<String, dynamic> _handle(
    http.Response response, {
    required bool isAuthEndpoint,
  }) {
    final status = response.statusCode;

    if (status >= 200 && status < 300) {
      if (response.body.isEmpty) return const {};
      // A captive portal or a misconfigured gateway can answer 200 with HTML.
      // That must still surface as an ApiException, because every caller
      // catches ApiException only — a raw FormatException escaping here would
      // leave the manual-send button with no feedback at all.
      try {
        final decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) return decoded;
      } on FormatException {
        // Fall through.
      }
      throw ApiException(status, 'The server returned an unexpected response.');
    }

    if (status == 401 && !isAuthEndpoint) onUnauthorized?.call();

    throw _errorFrom(response);
  }

  /// Parses `{"error": {"code": "...", "message": "..."}}`. A body we cannot
  /// read must never surface to the user as raw JSON or a stack trace.
  ApiException _errorFrom(http.Response response) {
    final status = response.statusCode;
    try {
      final decoded = jsonDecode(response.body);
      final error = (decoded as Map<String, dynamic>)['error'];
      if (error is Map<String, dynamic>) {
        final message = error['message'] as String?;
        if (message != null && message.isNotEmpty) {
          return ApiException(status, message, code: error['code'] as String?);
        }
      }
    } on FormatException {
      // Fall through to the generic message below.
    } on TypeError {
      // Ditto — the envelope was not the shape we expect.
    }
    return ApiException(status, _genericMessage(status));
  }

  String _genericMessage(int status) => switch (status) {
    400 || 422 => 'Some of the details you entered are not valid.',
    401 => 'Your session has expired. Please log in again.',
    403 => 'You do not have permission to do that.',
    404 => 'That was not found on the server.',
    >= 500 => 'The server is having trouble. Please try again shortly.',
    _ => 'Something went wrong. Please try again.',
  };
}
