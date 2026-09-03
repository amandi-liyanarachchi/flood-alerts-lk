import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/api_client.dart';
import '../core/validators.dart';
import '../models/auth_response.dart';
import '../models/user.dart';

/// Register, login, logout, and the persistence of the resulting session.
///
/// The profile is kept in secure storage alongside the token rather than in
/// shared_preferences: it holds the user's NIC and phone number.
class AuthService {
  AuthService(this._api);

  final ApiClient _api;

  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
  );

  static const _tokenKey = 'auth_token';
  static const _userKey = 'auth_user';

  Future<AuthResponse> register({
    required String nic,
    required String firstName,
    required String lastName,
    required String phone,
    required String password,
  }) async {
    final json = await _api.post('/auth/register', {
      'nic': Validators.normaliseNic(nic),
      'firstName': firstName.trim(),
      'lastName': lastName.trim(),
      'phone': Validators.normalisePhone(phone),
      'password': password,
    });
    return _persist(AuthResponse.fromJson(json));
  }

  Future<AuthResponse> login({
    required String nic,
    required String password,
  }) async {
    final json = await _api.post('/auth/login', {
      'nic': Validators.normaliseNic(nic),
      'password': password,
    });
    return _persist(AuthResponse.fromJson(json));
  }

  /// Clears the stored session. Safe to call when nothing is stored.
  Future<void> logout() async {
    _api.setToken(null);
    await _storage.delete(key: _tokenKey);
    await _storage.delete(key: _userKey);
  }

  /// Returns the stored session, or null if there is none. Also primes
  /// [ApiClient] with the token so later calls are authenticated.
  Future<User?> restore() async {
    final token = await _storage.read(key: _tokenKey);
    if (token == null || token.isEmpty) return null;

    final rawUser = await _storage.read(key: _userKey);
    if (rawUser == null || rawUser.isEmpty) {
      // A token with no profile is unusable — treat it as no session.
      await logout();
      return null;
    }

    _api.setToken(token);
    try {
      return User.fromJson(jsonDecode(rawUser) as Map<String, dynamic>);
    } on Object {
      // Not just bad JSON: a stored value of the wrong shape throws TypeError
      // out of the cast and out of User.fromJson. Either way the profile is
      // unusable, so drop the session rather than letting it escape.
      await logout();
      return null;
    }
  }

  Future<AuthResponse> _persist(AuthResponse auth) async {
    _api.setToken(auth.token);
    await _storage.write(key: _tokenKey, value: auth.token);
    await _storage.write(key: _userKey, value: jsonEncode(auth.user.toJson()));
    return auth;
  }
}
