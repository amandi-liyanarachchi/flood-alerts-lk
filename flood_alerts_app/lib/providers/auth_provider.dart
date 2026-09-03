import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/api_exception.dart';
import '../models/auth_response.dart';
import '../models/user.dart';
import '../services/auth_service.dart';

enum AuthStatus { unknown, authenticated, unauthenticated }

class AuthProvider extends ChangeNotifier {
  AuthProvider(this._authService);

  final AuthService _authService;

  AuthStatus _status = AuthStatus.unknown;
  User? _user;
  bool _isBusy = false;
  String? _errorMessage;

  AuthStatus get status => _status;
  User? get user => _user;
  bool get isBusy => _isBusy;
  String? get errorMessage => _errorMessage;

  /// Called by the splash screen. Decides Home vs Login.
  ///
  /// This must always reach a decision. AuthGate renders the splash spinner
  /// for as long as the status is unknown, so anything thrown here — a
  /// flutter_secure_storage read can fail outright on Android after a keystore
  /// reset or a backup restore — would strand the user on that spinner with no
  /// way out but clearing app data.
  Future<void> restoreSession() async {
    User? user;
    try {
      user = await _authService.restore();
    } on Object catch (e) {
      if (kDebugMode) debugPrint('session restore failed: ${e.runtimeType}');
      user = null;
    }
    _user = user;
    _status = user == null
        ? AuthStatus.unauthenticated
        : AuthStatus.authenticated;
    notifyListeners();
  }

  Future<bool> login({required String nic, required String password}) =>
      _run(() => _authService.login(nic: nic, password: password));

  Future<bool> register({
    required String nic,
    required String firstName,
    required String lastName,
    required String phone,
    required String password,
  }) => _run(
    () => _authService.register(
      nic: nic,
      firstName: firstName,
      lastName: lastName,
      phone: phone,
      password: password,
    ),
  );

  Future<void> logout() async {
    await _authService.logout();
    _user = null;
    _errorMessage = null;
    _status = AuthStatus.unauthenticated;
    notifyListeners();
  }

  /// Fired by ApiClient when any request comes back 401. The token is long
  /// lived and there is no refresh flow (§7), so the only response is to drop
  /// the session and send the user back to Login.
  void handleUnauthorized() {
    if (_status == AuthStatus.unauthenticated) return;
    // setToken(null) inside logout() is synchronous, so the token is already
    // unusable; only the secure-storage deletes are outstanding. Swallow their
    // failure rather than raising an unhandled async error from a callback the
    // UI cannot catch — but do log it, because a surviving stored token would
    // be restored into a dead session on the next launch.
    unawaited(
      _authService.logout().catchError((Object e) {
        if (kDebugMode) debugPrint('logout cleanup failed: ${e.runtimeType}');
      }),
    );
    _user = null;
    _status = AuthStatus.unauthenticated;
    _errorMessage = 'Your session has expired. Please log in again.';
    notifyListeners();
  }

  void clearError() {
    if (_errorMessage == null) return;
    _errorMessage = null;
    notifyListeners();
  }

  Future<bool> _run(Future<AuthResponse> Function() action) async {
    _isBusy = true;
    _errorMessage = null;
    notifyListeners();
    try {
      final auth = await action();
      _user = auth.user;
      _status = AuthStatus.authenticated;
      return true;
    } on ApiException catch (e) {
      _errorMessage = e.message;
      return false;
    } finally {
      _isBusy = false;
      notifyListeners();
    }
  }
}
