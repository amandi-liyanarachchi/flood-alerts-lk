import 'package:flutter/foundation.dart';

import '../services/consent_service.dart';

enum ConsentStatus {
  /// Not yet read from storage — the gate shows the splash meanwhile.
  unknown,

  /// This participant has consented to the current notice.
  granted,

  /// Never consented, withdrew, or consented only to an older notice.
  needed,
}

/// Gates the app on a recorded consent. Nothing that collects data may be
/// reachable while the status is [ConsentStatus.needed].
class ConsentProvider extends ChangeNotifier {
  ConsentProvider(this._service);

  final ConsentService _service;

  ConsentStatus _status = ConsentStatus.unknown;
  bool _isBusy = false;

  /// Which participant [_status] describes, so a different user signing in on
  /// the same device is never let through on someone else's consent.
  String? _loadedFor;

  ConsentStatus get status => _status;
  bool get isBusy => _isBusy;

  /// Idempotent per user: safe to call from a build-triggered callback.
  Future<void> loadFor(String userId) async {
    if (_loadedFor == userId && _status != ConsentStatus.unknown) return;
    _loadedFor = userId;

    final consented = await _service.hasConsented(userId);
    _status = consented ? ConsentStatus.granted : ConsentStatus.needed;
    notifyListeners();

    if (consented) {
      // A grant the server never received is retried here rather than on the
      // consent screen, which this participant will not see again.
      await _service.syncPending(userId);
    }
  }

  Future<void> grant(String userId) async {
    _isBusy = true;
    notifyListeners();
    try {
      await _service.grant(userId);
      _loadedFor = userId;
      _status = ConsentStatus.granted;
    } finally {
      _isBusy = false;
      notifyListeners();
    }
  }

  Future<void> withdraw(String userId) async {
    _isBusy = true;
    notifyListeners();
    try {
      await _service.withdraw(userId);
      _loadedFor = userId;
      _status = ConsentStatus.needed;
    } finally {
      _isBusy = false;
      notifyListeners();
    }
  }

  Future<ConsentRecord?> record(String userId) => _service.read(userId);

  /// Called on logout and on a lost session, so the next participant to sign
  /// in on this device is evaluated from their own record.
  void reset() {
    _loadedFor = null;
    _status = ConsentStatus.unknown;
    notifyListeners();
  }
}
