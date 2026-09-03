// Dev-only mock of the Flood Alerts LK backend described in CLAUDE.md §7.
//
// This is NOT part of the app. It exists so the client can be run end to end
// before the real server exists, and it ships with nothing.
//
//   dart run tools/mock_server/main.dart            # listens on 0.0.0.0:8080
//   dart run tools/mock_server/main.dart --port 9000
//
// Then run the app. On an Android emulator the default API_BASE_URL already
// points here (10.0.2.2 is the emulator's alias for your machine). On a real
// device, pass the LAN address this prints at startup:
//
//   flutter run --dart-define=API_BASE_URL=http://192.168.1.x:8080
//
// Seeded accounts (see _seedUsers): 912345678V / password123
//                                   199112345678 / password123
//
// Dev controls, for exercising paths that are hard to reach by hand:
//   curl -X POST localhost:8080/_dev/alert -d '{"severity":"high"}'
//   curl -X POST localhost:8080/_dev/alert -d '{"clear":true}'
//   curl -X POST localhost:8080/_dev/fail  -d '{"mode":"500"}'   # queue retry
//   curl -X POST localhost:8080/_dev/fail  -d '{"mode":"400"}'   # poison drop
//   curl -X POST localhost:8080/_dev/fail  -d '{"mode":"401"}'   # session death
//   curl -X POST localhost:8080/_dev/fail  -d '{"mode":"off"}'
//   curl localhost:8080/_dev/state
//
// ignore_for_file: avoid_print

import 'dart:convert';
import 'dart:io';
import 'dart:math';

// ----------------------------------------------------------------- state

class MockUser {
  MockUser({
    required this.id,
    required this.nic,
    required this.firstName,
    required this.lastName,
    required this.phone,
    required this.password,
  });

  final String id;
  final String nic;
  final String firstName;
  final String lastName;
  final String phone;
  final String password;

  Map<String, dynamic> toJson() => {
    'id': id,
    'nic': nic,
    'firstName': firstName,
    'lastName': lastName,
    'phone': phone,
  };
}

final Map<String, MockUser> _usersByNic = {};
final Map<String, MockUser> _tokens = {};
final Map<String, String> _fcmTokens = {};
final List<Map<String, dynamic>> _pings = [];
final List<Map<String, dynamic>> _feedback = [];

/// Every consent record ever received, never overwritten — the audit trail is
/// the point of storing them at all (CLAUDE.md §7).
final List<Map<String, dynamic>> _consents = [];

Map<String, dynamic>? _activeAlert;

/// 'off' | '500' | '400' | '401' — forces failures on the data endpoints so
/// the offline queue and session-teardown paths can actually be exercised.
String _failMode = 'off';

final _rng = Random();
var _nextId = 1;

void _seedUsers() {
  for (final u in [
    MockUser(
      id: 'u_1',
      nic: '912345678V',
      firstName: 'Nimal',
      lastName: 'Perera',
      phone: '0712345678',
      password: 'password123',
    ),
    MockUser(
      id: 'u_2',
      nic: '199112345678',
      firstName: 'Kamala',
      lastName: 'Silva',
      phone: '0771234567',
      password: 'password123',
    ),
  ]) {
    _usersByNic[u.nic] = u;
    _nextId++;
  }
}

// ------------------------------------------------------------- helpers

String _normaliseNic(String raw) =>
    raw.replaceAll(RegExp(r'\s'), '').toUpperCase();

String _newToken() =>
    List.generate(32, (_) => _rng.nextInt(16).toRadixString(16)).join();

String _stamp() => DateTime.now().toUtc().toIso8601String();

void _send(HttpRequest req, int status, Object body) {
  req.response
    ..statusCode = status
    ..headers.contentType = ContentType.json
    ..write(jsonEncode(body));
  req.response.close();
}

void _fail(HttpRequest req, int status, String code, String message) =>
    _send(req, status, {
      'error': {'code': code, 'message': message},
    });

Future<Map<String, dynamic>> _body(HttpRequest req) async {
  final raw = await utf8.decoder.bind(req).join();
  if (raw.isEmpty) return {};
  final decoded = jsonDecode(raw);
  return decoded is Map<String, dynamic> ? decoded : {};
}

MockUser? _authenticate(HttpRequest req) {
  final header = req.headers.value(HttpHeaders.authorizationHeader);
  if (header == null || !header.startsWith('Bearer ')) return null;
  return _tokens[header.substring(7)];
}

/// Applies the forced-failure mode to an authenticated data endpoint.
/// Returns true when it has already answered the request.
bool _injectedFailure(HttpRequest req) {
  switch (_failMode) {
    case '500':
      _fail(req, 500, 'SERVER_ERROR', 'Simulated server error.');
      return true;
    case '400':
      _fail(req, 400, 'VALIDATION_FAILED', 'Simulated permanent rejection.');
      return true;
    case '401':
      _fail(req, 401, 'UNAUTHORIZED', 'Simulated expired session.');
      return true;
    default:
      return false;
  }
}

// -------------------------------------------------------------- routes

Future<void> _handle(HttpRequest req) async {
  final path = req.uri.path;
  final method = req.method;
  print('${_stamp()}  $method $path');

  try {
    if (path.startsWith('/_dev')) return await _devRoutes(req, path, method);

    if (path == '/api/v1/auth/register' && method == 'POST') {
      return await _register(req);
    }
    if (path == '/api/v1/auth/login' && method == 'POST') {
      return await _login(req);
    }

    // Everything below is authenticated.
    final user = _authenticate(req);
    if (user == null) {
      return _fail(req, 401, 'UNAUTHORIZED', 'Your session has expired.');
    }
    if (_injectedFailure(req)) return;

    if (path == '/api/v1/locations' && method == 'POST') {
      final body = await _body(req);
      _pings.add({...body, 'userId': user.id, 'receivedAt': _stamp()});
      final source = body['source'];
      print(
        '    ping #${_pings.length} from ${user.nic} ($source) '
        'lat=${body['latitude']} lng=${body['longitude']} '
        'recordedAt=${body['recordedAt']}',
      );
      return _send(req, 201, {'accepted': true});
    }

    if (path == '/api/v1/feedback' && method == 'POST') {
      final body = await _body(req);
      _feedback.add({...body, 'userId': user.id, 'receivedAt': _stamp()});
      print(
        '    feedback from ${user.nic}: '
        'floodPresent=${body['floodPresent']}',
      );
      return _send(req, 201, {'accepted': true});
    }

    if (path == '/api/v1/consent' && method == 'POST') {
      final body = await _body(req);
      _consents.add({...body, 'userId': user.id, 'receivedAt': _stamp()});
      print(
        '    consent v${body['version']} from ${user.nic}: '
        'granted=${body['granted']}',
      );
      return _send(req, 200, {'accepted': true});
    }

    if (path == '/api/v1/alerts/active' && method == 'GET') {
      return _send(req, 200, {'alert': _activeAlert});
    }

    if (path == '/api/v1/devices/fcm-token') {
      final body = await _body(req);
      if (method == 'POST') {
        _fcmTokens[user.id] = body['fcmToken'] as String? ?? '';
        print(
          '    registered FCM token for ${user.nic} '
          '(${body['platform']})',
        );
        return _send(req, 200, {'accepted': true});
      }
      if (method == 'DELETE') {
        _fcmTokens.remove(user.id);
        print('    removed FCM token for ${user.nic}');
        return _send(req, 200, {'accepted': true});
      }
    }

    _fail(req, 404, 'NOT_FOUND', 'No such endpoint.');
  } on Object catch (e) {
    print('    !! $e');
    _fail(req, 500, 'SERVER_ERROR', 'Mock server blew up: $e');
  }
}

Future<void> _register(HttpRequest req) async {
  final body = await _body(req);
  final nic = _normaliseNic(body['nic'] as String? ?? '');
  final password = body['password'] as String? ?? '';

  if (nic.isEmpty || password.length < 8) {
    return _fail(req, 422, 'VALIDATION_FAILED', 'NIC or password is invalid.');
  }
  if (_usersByNic.containsKey(nic)) {
    return _fail(
      req,
      409,
      'NIC_ALREADY_REGISTERED',
      'That NIC is already registered.',
    );
  }

  final user = MockUser(
    id: 'u_${_nextId++}',
    nic: nic,
    firstName: body['firstName'] as String? ?? '',
    lastName: body['lastName'] as String? ?? '',
    phone: body['phone'] as String? ?? '',
    password: password,
  );
  _usersByNic[nic] = user;

  final token = _newToken();
  _tokens[token] = user;
  print('    registered ${user.nic} (${user.firstName} ${user.lastName})');
  _send(req, 201, {'token': token, 'user': user.toJson()});
}

Future<void> _login(HttpRequest req) async {
  final body = await _body(req);
  final nic = _normaliseNic(body['nic'] as String? ?? '');
  final password = body['password'] as String? ?? '';

  final user = _usersByNic[nic];
  if (user == null || user.password != password) {
    return _fail(
      req,
      401,
      'INVALID_CREDENTIALS',
      'NIC or password is incorrect',
    );
  }

  final token = _newToken();
  _tokens[token] = user;
  print('    logged in ${user.nic}');
  _send(req, 200, {'token': token, 'user': user.toJson()});
}

Future<void> _devRoutes(HttpRequest req, String path, String method) async {
  if (path == '/_dev/state') {
    return _send(req, 200, {
      'users': _usersByNic.values.map((u) => u.toJson()).toList(),
      'activeSessions': _tokens.length,
      'fcmTokens': _fcmTokens,
      'failMode': _failMode,
      'activeAlert': _activeAlert,
      'pingCount': _pings.length,
      'pings': _pings.length > 20 ? _pings.sublist(_pings.length - 20) : _pings,
      'feedback': _feedback,
      'consents': _consents,
    });
  }

  if (path == '/_dev/alert' && method == 'POST') {
    final body = await _body(req);
    if (body['clear'] == true) {
      _activeAlert = null;
      print('    >> alert cleared');
      return _send(req, 200, {'alert': null});
    }
    _activeAlert = {
      'id': 'a_${_rng.nextInt(999)}',
      'severity': body['severity'] as String? ?? 'high',
      'title': body['title'] as String? ?? 'Flood risk in your area',
      'message':
          body['message'] as String? ??
          'Rising water levels reported near the Kelani River. '
              'Move to higher ground.',
      'issuedAt': _stamp(),
    };
    print('    >> alert set: ${_activeAlert!['severity']}');
    return _send(req, 200, {'alert': _activeAlert});
  }

  if (path == '/_dev/fail' && method == 'POST') {
    final body = await _body(req);
    _failMode = body['mode'] as String? ?? 'off';
    print('    >> fail mode: $_failMode');
    return _send(req, 200, {'failMode': _failMode});
  }

  if (path == '/_dev/reset' && method == 'POST') {
    _pings.clear();
    _feedback.clear();
    _consents.clear();
    _activeAlert = null;
    _failMode = 'off';
    print('    >> reset');
    return _send(req, 200, {'ok': true});
  }

  _fail(req, 404, 'NOT_FOUND', 'No such dev endpoint.');
}

// ---------------------------------------------------------------- main

Future<void> main(List<String> args) async {
  final portIndex = args.indexOf('--port');
  final port = portIndex >= 0 && portIndex + 1 < args.length
      ? int.tryParse(args[portIndex + 1]) ?? 8080
      : 8080;

  _seedUsers();

  final HttpServer server;
  try {
    server = await HttpServer.bind(InternetAddress.anyIPv4, port);
  } on SocketException catch (e) {
    // Almost always a mock server already running from an earlier session. A
    // raw SocketException and a stack trace sends people hunting for a bug in
    // the code instead of at a `lsof` prompt.
    stderr.writeln('Could not listen on port $port.');
    if (e.osError?.errorCode == 48) {
      stderr.writeln('');
      stderr.writeln('Something is already using it — most likely a mock');
      stderr.writeln('server still running from earlier. Either stop it:');
      stderr.writeln('');
      stderr.writeln('    lsof -nP -iTCP:$port -sTCP:LISTEN     # find it');
      stderr.writeln('    kill \$(lsof -t -iTCP:$port -sTCP:LISTEN)');
      stderr.writeln('');
      stderr.writeln('or run this one somewhere else:');
      stderr.writeln('');
      stderr.writeln('    dart run tools/mock_server/main.dart --port 8081');
      stderr.writeln('');
      stderr.writeln('Remember the app defaults to port 8080, so a different');
      stderr.writeln('port needs a matching --dart-define:');
      stderr.writeln('');
      stderr.writeln(
        '    flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8081',
      );
    } else {
      stderr.writeln(e.osError?.message ?? e.message);
    }
    exitCode = 1;
    return;
  }

  print('Flood Alerts LK mock server  —  CLAUDE.md §7');
  print('listening on http://0.0.0.0:$port');
  print('');
  print('  Android emulator : http://10.0.2.2:$port   (the app default)');
  for (final ni in await NetworkInterface.list(
    type: InternetAddressType.IPv4,
  )) {
    for (final addr in ni.addresses) {
      if (!addr.isLoopback) {
        print('  Physical device  : http://${addr.address}:$port');
      }
    }
  }
  print('');
  print('  Seeded logins    : 912345678V     / password123');
  print('                     199112345678   / password123');
  print('');

  await for (final req in server) {
    await _handle(req);
  }
}
