import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import '../core/api_exception.dart';
import '../core/config.dart';
import '../models/location_ping.dart';

/// What the app is currently allowed to do with location.
enum LocationAccess {
  /// Device GPS is switched off entirely.
  serviceDisabled,

  /// Refused, but we may ask again.
  denied,

  /// Refused permanently — only app settings can change this.
  deniedForever,

  /// Foreground only. Manual send works; background tracking does not.
  whileInUse,

  /// Full background access.
  always,
}

/// Owns the position stream, the 10-minute upload throttle, and the offline
/// ping queue. Contains no BuildContext and shows no UI.
class LocationService {
  LocationService(this._api);

  final ApiClient _api;

  static const String _queueKey = 'pending_location_pings';

  /// Marks "no GPS fix" so it is not mistaken for a delivery failure. Nothing
  /// was captured, so nothing was queued and nothing will be retried.
  static const String noFixCode = 'NO_LOCATION_FIX';

  StreamSubscription<Position>? _subscription;

  /// Gates the throttle. Only automatic uploads move this — a manual send
  /// deliberately does not, so pressing the button never delays the next
  /// scheduled ping.
  DateTime? _lastUploadedAt;

  /// Drives the "Last sent" line on Home. Any successful upload moves this.
  DateTime? _lastSuccessAt;

  List<LocationPing> _queue = const [];
  Future<void>? _loading;
  bool _isFlushing = false;
  bool _starting = false;

  /// Set once per app session so we never re-prompt for background access in
  /// a loop after the user has declined it.
  bool _hasRequestedAlways = false;

  /// Bumped by [stop]. An upload that was already in flight when the session
  /// ended must not write to the queue afterwards, or logout's clearQueue
  /// would be undone and the ping would outlive its owner.
  int _session = 0;

  /// Fired whenever anything the Home status card displays has changed.
  void Function()? onChanged;

  bool get isTracking => _subscription != null;
  DateTime? get lastSuccessAt => _lastSuccessAt;
  int get pendingCount => _queue.length;

  // ---------------------------------------------------------------- lifecycle

  /// Starts the long-lived position stream. Safe to call when already running
  /// or when permission is missing — it simply does nothing.
  Future<void> start() async {
    // Two awaits sit between this guard and the assignment below, so without
    // _starting a second caller can pass the check and open a second stream.
    // The orphan would survive stop() — still ticking, still holding the
    // Android foreground notification up, still POSTing after logout.
    if (_subscription != null || _starting) return;
    _starting = true;
    final session = _session;
    try {
      await _loadQueue();

      final access = await checkAccess();
      if (access != LocationAccess.always &&
          access != LocationAccess.whileInUse) {
        _log('not starting stream: access=$access');
        return;
      }

      // A stop() may have landed while we were awaiting.
      if (session != _session || _subscription != null) return;

      _subscription =
          Geolocator.getPositionStream(locationSettings: _settingsFor(access))
              .listen(
                _onPosition,
                onError: (Object e) => _log('stream error: ${e.runtimeType}'),
                cancelOnError: false,
              );
      _log('stream started (access=$access)');
      onChanged?.call();
    } finally {
      _starting = false;
    }
  }

  Future<void> stop() async {
    await _subscription?.cancel();
    _subscription = null;
    _session++;
    _lastUploadedAt = null;
    _lastSuccessAt = null;
    _hasRequestedAlways = false;
    _log('stream stopped');
    onChanged?.call();
  }

  /// The stream ticks about once a minute while we only upload every ten. That
  /// is deliberate: the frequent ticks keep the OS location session warm, so a
  /// fresh fix is always ready the moment the throttle opens — and so a manual
  /// send never has to cold-start the GPS. LocationService, not the stream, is
  /// what decides when a POST actually happens.
  LocationSettings _settingsFor(LocationAccess access) {
    if (defaultTargetPlatform == TargetPlatform.iOS) {
      return AppleSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 0,
        // Asking for background updates without `always` throws on iOS.
        allowBackgroundLocationUpdates: access == LocationAccess.always,
        showBackgroundLocationIndicator: true,
        pauseLocationUpdatesAutomatically: false,
        activityType: ActivityType.other,
      );
    }
    return AndroidSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 0,
      intervalDuration: const Duration(minutes: 1),
      foregroundNotificationConfig: const ForegroundNotificationConfig(
        notificationTitle: 'Flood Alerts LK',
        notificationText: 'Sharing your location for flood alerts',
        enableWakeLock: true,
        setOngoing: true,
      ),
    );
  }

  // -------------------------------------------------------------- permissions

  Future<LocationAccess> checkAccess() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return LocationAccess.serviceDisabled;
    }
    return _map(await Geolocator.checkPermission());
  }

  /// Runs the §8 permission flow: while-in-use first, then a single attempt at
  /// upgrading to always-on. Never loops, never blocks the app.
  Future<LocationAccess> requestAccess() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return LocationAccess.serviceDisabled;
    }

    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }

    var access = _map(permission);

    // Only ask for background once while-in-use is actually granted, and only
    // once per session.
    if (access == LocationAccess.whileInUse && !_hasRequestedAlways) {
      _hasRequestedAlways = true;
      access = _map(await Geolocator.requestPermission());
    }

    if (access == LocationAccess.always ||
        access == LocationAccess.whileInUse) {
      await start();
    }
    onChanged?.call();
    return access;
  }

  Future<bool> openAppSettings() => Geolocator.openAppSettings();

  Future<bool> openLocationSettings() => Geolocator.openLocationSettings();

  static LocationAccess _map(LocationPermission permission) =>
      switch (permission) {
        LocationPermission.always => LocationAccess.always,
        LocationPermission.whileInUse => LocationAccess.whileInUse,
        LocationPermission.deniedForever => LocationAccess.deniedForever,
        LocationPermission.denied => LocationAccess.denied,
        LocationPermission.unableToDetermine => LocationAccess.denied,
      };

  // ------------------------------------------------------------------ sending

  /// Manual "Send My Location Now". Bypasses the throttle without resetting
  /// it, and works on while-in-use permission alone.
  ///
  /// Throws [ApiException] if the ping could not be delivered — it will have
  /// been queued for retry by then.
  Future<void> sendNow() async {
    final position = await currentPosition();
    if (position == null) {
      throw const ApiException(
        0,
        'Could not get a location fix. Try again.',
        code: noFixCode,
      );
    }
    await _upload(_pingFrom(position, 'manual'), isAuto: false);
  }

  /// Best-effort current fix, used by manual send and by the feedback card.
  Future<Position?> currentPosition() async {
    final access = await checkAccess();
    if (access != LocationAccess.always &&
        access != LocationAccess.whileInUse) {
      return null;
    }
    try {
      return await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
          timeLimit: Duration(seconds: 15),
        ),
      );
    } on TimeoutException {
      _log('current position timed out; falling back to last known');
      return Geolocator.getLastKnownPosition();
    } on Exception catch (e) {
      _log('current position failed: ${e.runtimeType}');
      return null;
    }
  }

  Future<void> _onPosition(Position position) async {
    final now = DateTime.now().toUtc();
    final due =
        _lastUploadedAt == null ||
        now.difference(_lastUploadedAt!) >= Config.locationInterval;
    if (!due) return;

    // Move the gate before awaiting so a slow upload cannot let a second tick
    // through and double-post.
    _lastUploadedAt = now;
    try {
      await _upload(_pingFrom(position, 'auto'), isAuto: true);
    } on ApiException {
      // Already queued. The next tick is the retry — see §8.
    }
  }

  LocationPing _pingFrom(Position position, String source) => LocationPing(
    latitude: position.latitude,
    longitude: position.longitude,
    accuracy: position.accuracy,
    recordedAt: position.timestamp.toUtc(),
    source: source,
  );

  Future<void> _upload(LocationPing ping, {required bool isAuto}) async {
    final session = _session;
    await _flushQueue();
    try {
      await _api.post('/locations', ping.toJson());
      if (session != _session) return;
      _lastSuccessAt = DateTime.now().toUtc();
      _log('ping uploaded (${ping.source})');
      onChanged?.call();
    } on ApiException catch (e) {
      if (session != _session) rethrow;
      if (_isRetryable(e)) {
        await _enqueue(ping);
        // Reopen the throttle so the next tick retries, rather than making a
        // flood-time outage cost a further ten minutes of silence (§8).
        if (isAuto) _lastUploadedAt = null;
      }
      _log('ping failed (${ping.source}): ${e.statusCode}');
      onChanged?.call();
      rethrow;
    }
  }

  // -------------------------------------------------------------------- queue

  /// Memoised so concurrent callers await one read rather than racing to
  /// populate [_queue] from the same stored list.
  Future<void> _loadQueue() => _loading ??= _readQueue();

  Future<void> _readQueue() async {
    try {
      await _readQueueOrThrow();
    } on Object catch (e) {
      // getInstance() and remove() sit outside the parse guard below and can
      // both fail. Without this the memo would hold a rejected future that
      // ??= never re-runs, so every later enqueue, flush and start() would
      // rethrow for the rest of the process. Drop the memo so a later call
      // can retry instead.
      _loading = null;
      _log('queue load failed: ${e.runtimeType}');
    }
  }

  Future<void> _readQueueOrThrow() async {
    final session = _session;
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_queueKey);
    if (raw == null || raw.isEmpty) return;
    if (session != _session) return;
    try {
      final decoded = jsonDecode(raw) as List<dynamic>;
      _queue = decoded
          .whereType<Map<String, dynamic>>()
          .map(LocationPing.fromJson)
          .toList();
      _log('restored ${_queue.length} queued pings');
    } on Object catch (e) {
      // Any malformed value, not just invalid JSON: a stored object instead of
      // a list throws TypeError, and a bad field throws from fromJson. If it
      // escaped here it would re-throw out of start() on every single launch
      // and no tracking would ever begin.
      _queue = const [];
      await prefs.remove(_queueKey);
      _log('discarded a corrupt ping queue: ${e.runtimeType}');
    }
  }

  Future<void> _enqueue(LocationPing ping) async {
    final session = _session;
    await _loadQueue();
    final next = [..._queue, ping];
    // A logout can land while _loadQueue is awaiting. Bail before touching
    // _queue, not after: leaving this ping in the in-memory list would let the
    // next user's first flush POST it under their token, since clearQueue has
    // already resolved the load memo and nothing will re-read the empty disk.
    if (session != _session) return;
    // Cap the queue, dropping the oldest first.
    _queue = next.length > Config.maxQueuedPings
        ? next.sublist(next.length - Config.maxQueuedPings)
        : next;
    await _saveQueue();
  }

  /// Sends queued pings oldest-first, stopping at the first retryable failure
  /// — if one cannot get through, neither can the rest.
  ///
  /// Only one flush runs at a time. A manual send arriving mid-drain must not
  /// start a second pass over the same entries, or every queued ping is
  /// delivered twice.
  Future<void> _flushQueue() async {
    if (_isFlushing) return;
    _isFlushing = true;
    final session = _session;
    try {
      await _loadQueue();
      if (_queue.isEmpty) return;

      final remaining = [..._queue];
      var sent = 0;
      var dropped = 0;
      while (remaining.isNotEmpty) {
        try {
          await _api.post('/locations', remaining.first.toJson());
          remaining.removeAt(0);
          sent++;
        } on ApiException catch (e) {
          if (_isRetryable(e)) break;
          // The server will never accept this one. Drop it and carry on: a
          // single poisoned entry must not wedge the whole queue forever.
          remaining.removeAt(0);
          dropped++;
          _log('dropped an unsendable queued ping: ${e.statusCode}');
        }
      }

      // Logging out mid-flush already cleared the queue. Writing back now
      // would restore the previous user's pings for the next session to send.
      if (session != _session) return;
      if (sent == 0 && dropped == 0) return;

      _queue = remaining;
      await _saveQueue();
      // Only a real delivery moves "Last sent". Discarding pings the server
      // refuses is not a send, and reporting it as one would tell the user
      // their location went out when it was thrown away.
      if (sent > 0) _lastSuccessAt = DateTime.now().toUtc();
      _log('queue: $sent sent, $dropped dropped, ${_queue.length} left');
      onChanged?.call();
    } finally {
      _isFlushing = false;
    }
  }

  Future<void> _saveQueue() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
        _queueKey,
        jsonEncode(_queue.map((p) => p.toJson()).toList()),
      );
    } on Object catch (e) {
      // This runs from inside _upload's ApiException handler. Throwing here
      // would replace the rethrow with something no caller catches — every
      // caller filters on ApiException — costing _onPosition its handler and
      // a manual send its user feedback. The ping is already lost if prefs is
      // broken; losing the error report as well helps nobody.
      _log('could not persist the queue: ${e.runtimeType}');
    }
  }

  /// Drops queued pings and forgets they were ever loaded. Called on logout:
  /// the queue is keyed to nothing but the device, so leaving it in place
  /// would upload one user's coordinates under the next user's token.
  Future<void> clearQueue() async {
    // Bump here too rather than relying on every caller having called stop()
    // first — this is what makes an in-flight read or write bail out.
    _session++;
    _queue = const [];
    // Resolve the memo rather than clearing it: the stored key is about to go
    // away, so a later _loadQueue must be a no-op, not a fresh read that could
    // resurrect the list a concurrent write put back.
    _loading = Future<void>.value();
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_queueKey);
    _queue = const [];
  }

  /// Worth queueing for a retry. A 401 means the session is already gone, and
  /// a 4xx means the server rejected this payload and always will — keeping
  /// either would fill the 50-slot queue with pings that can never land.
  static bool _isRetryable(ApiException e) =>
      e.isNetworkFailure || e.statusCode >= 500;

  /// Never carries coordinates, and never runs in release (§8, §11).
  void _log(String message) {
    if (kDebugMode) debugPrint('[location] $message');
  }
}
