import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import '../core/api_exception.dart';
import '../core/consent_text.dart';

/// Records whether a participant has consented to taking part, and to which
/// version of the notice.
///
/// The record is kept per user id, not per device: consent is personal, so a
/// second person logging in on the same phone must be asked for their own.
///
/// The local copy is what gates the app. The server copy is what the PDPA
/// actually cares about — a controller has to be able to demonstrate consent
/// was given — so every grant and withdrawal is also pushed to the API. A
/// failed push never blocks the participant; it is retried on the next launch.
class ConsentService {
  ConsentService(this._api);

  final ApiClient _api;

  static const String _keyPrefix = 'consent_';
  static const String _unsyncedKey = 'consent_unsynced';

  String _key(String userId) => '$_keyPrefix$userId';

  /// True when this user has consented to the *current* notice. A stored
  /// consent against an older version does not count — consent is given to a
  /// specific notice, so a material rewording means asking again.
  Future<bool> hasConsented(String userId) async {
    final record = await read(userId);
    return record != null &&
        record.granted &&
        record.version == ConsentText.version;
  }

  Future<ConsentRecord?> read(String userId) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key(userId));
    if (raw == null || raw.isEmpty) return null;
    try {
      return ConsentRecord.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } on Object {
      await prefs.remove(_key(userId));
      return null;
    }
  }

  Future<ConsentRecord> grant(String userId) => _record(userId, granted: true);

  Future<ConsentRecord> withdraw(String userId) =>
      _record(userId, granted: false);

  Future<ConsentRecord> _record(String userId, {required bool granted}) async {
    final record = ConsentRecord(
      version: ConsentText.version,
      granted: granted,
      recordedAt: DateTime.now().toUtc(),
    );

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key(userId), jsonEncode(record.toJson()));

    await _push(userId, record);
    return record;
  }

  /// Best effort. The participant is never made to wait on the network to
  /// exercise a right, and a withdrawal takes effect locally whether or not
  /// the server hears about it.
  Future<void> _push(String userId, ConsentRecord record) async {
    try {
      await _api.post('/consent', record.toJson());
      await _clearUnsynced(userId);
      _log('consent v${record.version} synced (granted=${record.granted})');
    } on ApiException catch (e) {
      await _markUnsynced(userId);
      _log('consent sync failed (${e.statusCode}); will retry on next launch');
    }
  }

  /// Retries a consent record the server never received. Called on sign-in.
  Future<void> syncPending(String userId) async {
    final prefs = await SharedPreferences.getInstance();
    final pending = prefs.getStringList(_unsyncedKey) ?? const [];
    if (!pending.contains(userId)) return;

    final record = await read(userId);
    if (record == null) {
      await _clearUnsynced(userId);
      return;
    }
    await _push(userId, record);
  }

  Future<void> _markUnsynced(String userId) async {
    final prefs = await SharedPreferences.getInstance();
    final pending = {...?prefs.getStringList(_unsyncedKey), userId}.toList();
    await prefs.setStringList(_unsyncedKey, pending);
  }

  Future<void> _clearUnsynced(String userId) async {
    final prefs = await SharedPreferences.getInstance();
    final pending = (prefs.getStringList(_unsyncedKey) ?? const [])
        .where((id) => id != userId)
        .toList();
    await prefs.setStringList(_unsyncedKey, pending);
  }

  void _log(String message) {
    if (kDebugMode) debugPrint('[consent] $message');
  }
}

class ConsentRecord {
  const ConsentRecord({
    required this.version,
    required this.granted,
    required this.recordedAt,
  });

  final String version;
  final bool granted;
  final DateTime recordedAt;

  factory ConsentRecord.fromJson(Map<String, dynamic> json) => ConsentRecord(
    version: json['version'] as String? ?? '',
    granted: json['granted'] as bool? ?? false,
    recordedAt:
        DateTime.tryParse(json['recordedAt'] as String? ?? '')?.toUtc() ??
        DateTime.now().toUtc(),
  );

  Map<String, dynamic> toJson() => {
    'version': version,
    'granted': granted,
    'recordedAt': recordedAt.toUtc().toIso8601String(),
  };
}
