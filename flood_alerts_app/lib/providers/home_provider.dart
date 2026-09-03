import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/api_exception.dart';
import '../models/flood_alert.dart';
import '../services/alert_service.dart';
import '../services/feedback_service.dart';
import '../services/location_service.dart';

/// Everything the Home screen shows: the alert banner, the location status
/// card, the manual send button and the feedback card.
class HomeProvider extends ChangeNotifier {
  HomeProvider(this._location, this._feedback, this._alerts) {
    _location.onChanged = notifyListeners;
  }

  final LocationService _location;
  final FeedbackService _feedback;
  final AlertService _alerts;

  LocationAccess _access = LocationAccess.denied;
  bool _isSending = false;
  FloodAlert? _alert;
  bool _isLoadingAlert = false;

  bool? _pendingChoice;
  bool _isSubmittingFeedback = false;
  DateTime? _answeredAt;
  bool _isEditingAnswer = false;

  LocationAccess get access => _access;
  bool get isTracking => _location.isTracking;
  DateTime? get lastSentAt => _location.lastSuccessAt;
  int get pendingCount => _location.pendingCount;
  bool get isSending => _isSending;

  FloodAlert? get alert => _alert;
  bool get isLoadingAlert => _isLoadingAlert;
  bool get pushAvailable => _alerts.pushAvailable;

  bool? get pendingChoice => _pendingChoice;
  bool get isSubmittingFeedback => _isSubmittingFeedback;
  DateTime? get answeredAt => _answeredAt;

  /// True while the feedback card should show the Yes/No controls rather than
  /// the acknowledgement.
  bool get isAnswering => _answeredAt == null || _isEditingAnswer;

  // ------------------------------------------------------------------ session

  /// Called once the user is authenticated: start tracking, register for push,
  /// and load the current alert.
  Future<void> onSignedIn() async {
    // Each step is isolated: a platform channel throwing in any one of them
    // must not stop the rest. Losing push registration and the alert load
    // because a permission lookup failed would leave the user with no
    // warnings at all.
    try {
      _answeredAt = (await _feedback.lastAnswer())?.answeredAt;
    } on Object {
      _answeredAt = null;
    }
    await _syncAccess();

    try {
      if (_access == LocationAccess.denied) {
        _access = await _location.requestAccess();
      } else {
        await _location.start();
      }
    } on Object catch (e) {
      if (kDebugMode) debugPrint('location start failed: ${e.runtimeType}');
    }
    notifyListeners();

    await _alerts.registerDevice();
    await refreshAlert();
  }

  /// Deliberate logout. Runs while the auth token is still valid, so the FCM
  /// registration can be deleted server-side before the token goes away.
  Future<void> onSignOut() async {
    await _alerts.unregisterDevice();
    await resetForLostSession();
  }

  /// Teardown with no network calls, for a session that has already expired.
  ///
  /// A 401 must not trigger unregisterDevice: that DELETE would 401 in turn
  /// and drive this straight back through the same path.
  Future<void> resetForLostSession() async {
    // Invalidate any alert GET still in flight, or it can land after this and
    // show the previous user's flood alert to whoever logs in next.
    _alertRequest++;
    await _location.stop();
    // Queued pings belong to the user who recorded them, not to whoever logs
    // in on this device next.
    await _location.clearQueue();
    await _feedback.clear();
    _alert = null;
    _answeredAt = null;
    _pendingChoice = null;
    _isEditingAnswer = false;
    _isSending = false;
    _isSubmittingFeedback = false;
    _isLoadingAlert = false;
    notifyListeners();
  }

  // ----------------------------------------------------------------- location

  /// Runs the permission flow again, e.g. from the inline warning on Home.
  Future<void> requestAccess() async {
    try {
      _access = await _location.requestAccess();
    } on Object catch (e) {
      if (kDebugMode) debugPrint('permission request failed: ${e.runtimeType}');
    }
    notifyListeners();
  }

  Future<void> openAppSettings() => _location.openAppSettings();

  Future<void> openLocationSettings() => _location.openLocationSettings();

  /// The manual "Send My Location Now" path. Never throws — the screen turns
  /// the result straight into a SnackBar.
  Future<({bool ok, String message})> sendLocationNow() async {
    if (_isSending) return (ok: false, message: 'Already sending…');
    _isSending = true;
    notifyListeners();
    try {
      await _location.sendNow();
      return (ok: true, message: 'Location sent');
    } on ApiException catch (e) {
      // No fix means nothing was captured, so nothing was queued and nothing
      // will be retried. It arrives as statusCode 0 like a network failure,
      // but promising a retry here would be a lie on the one button people
      // press when they are actually in danger.
      if (e.code == LocationService.noFixCode) {
        return (ok: false, message: e.message);
      }
      // Saying "sent" when it is sitting in the retry queue would also be a
      // lie, in the other direction.
      if (e.isNetworkFailure || e.statusCode >= 500) {
        return (
          ok: false,
          message: 'Could not reach the server — saved, will retry.',
        );
      }
      return (ok: false, message: e.message);
    } finally {
      _isSending = false;
      // On every path, not just success: a send that failed because the user
      // revoked permission must surface the warning card, or they get a
      // failure with no visible cause. Safe in a finally — _syncAccess
      // swallows its own throws and never awaits here.
      unawaited(_syncAccess());
      notifyListeners();
    }
  }

  // ------------------------------------------------------------------- alerts

  /// Bumped per request so a slow response cannot overwrite a newer one.
  int _alertRequest = 0;

  Future<void> refreshAlert() async {
    // onSignedIn, pull-to-refresh and every arriving push all call this, with
    // no ordering between them. Without this guard a slow GET issued before an
    // alert existed can return {"alert": null} after a later GET has already
    // put a HIGH banner up, and silently erase it.
    final request = ++_alertRequest;
    _isLoadingAlert = true;
    notifyListeners();
    try {
      final position = await _location.currentPosition();
      final alert = await _alerts.fetchActive(
        latitude: position?.latitude,
        longitude: position?.longitude,
      );
      if (request != _alertRequest) return;
      _alert = alert;
    } on ApiException {
      // Leave the previous banner in place: a stale warning is safer than a
      // silently vanished one.
    } finally {
      if (request == _alertRequest) {
        _isLoadingAlert = false;
        notifyListeners();
      }
    }
  }

  /// Refreshes the permission read off the critical path. Geolocator can throw
  /// here, and a send must never lose its result to a status lookup.
  Future<void> _syncAccess() async {
    try {
      final access = await _location.checkAccess();
      if (access == _access) return;
      _access = access;
      notifyListeners();
    } on Object {
      // Leave the last known value in place.
    }
  }

  /// Pull-to-refresh: alert plus a fresh permission read.
  Future<void> refresh() async {
    await _syncAccess();
    await refreshAlert();
  }

  // ----------------------------------------------------------------- feedback

  void selectAnswer(bool floodPresent) {
    _pendingChoice = floodPresent;
    notifyListeners();
  }

  void editAnswer() {
    _isEditingAnswer = true;
    _pendingChoice = null;
    notifyListeners();
  }

  Future<({bool ok, String message})> submitFeedback() async {
    final choice = _pendingChoice;
    if (choice == null) {
      return (ok: false, message: 'Please choose Yes or No first.');
    }
    _isSubmittingFeedback = true;
    notifyListeners();
    try {
      final position = await _location.currentPosition();
      await _feedback.submit(
        floodPresent: choice,
        latitude: position?.latitude,
        longitude: position?.longitude,
      );
      _answeredAt = DateTime.now().toUtc();
      _isEditingAnswer = false;
      _pendingChoice = null;
      return (ok: true, message: 'Thanks — your answer was recorded.');
    } on ApiException catch (e) {
      return (ok: false, message: e.message);
    } finally {
      _isSubmittingFeedback = false;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _location.onChanged = null;
    super.dispose();
  }
}
