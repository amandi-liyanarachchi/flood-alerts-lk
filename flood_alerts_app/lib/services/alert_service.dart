import 'dart:io' show Platform;

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../core/api_client.dart';
import '../core/api_exception.dart';
import '../models/flood_alert.dart';

/// Top-level, trivial, and annotated so the engine can find it in a background
/// isolate. The OS draws the notification from the server's `notification`
/// payload; doing HTTP work here is explicitly out of scope (§9).
@pragma('vm:entry-point')
Future<void> floodAlertBackgroundHandler(RemoteMessage message) async {}

/// Fetches active alerts and, when Firebase is configured, handles push.
///
/// Alert *fetching* works with no Firebase at all — it is a plain authenticated
/// GET. Only push delivery depends on the google-services files being present,
/// so the app degrades to pull-only rather than breaking (§9, §15).
class AlertService {
  AlertService(this._api);

  final ApiClient _api;

  static const String _channelId = 'flood_alerts';

  final FlutterLocalNotificationsPlugin _local =
      FlutterLocalNotificationsPlugin();

  /// False when google-services.json / GoogleService-Info.plist are missing.
  bool _pushAvailable = false;
  bool get pushAvailable => _pushAvailable;

  /// GET /alerts/active. Returns null when there is no active alert.
  Future<FloodAlert?> fetchActive({double? latitude, double? longitude}) async {
    final json = await _api.get(
      '/alerts/active',
      query: {
        if (latitude != null) 'latitude': latitude.toString(),
        if (longitude != null) 'longitude': longitude.toString(),
      },
    );
    final alert = json['alert'];
    if (alert is! Map<String, dynamic>) return null;
    return FloodAlert.fromJson(alert);
  }

  /// Wires up push handling. [onAlert] fires whenever a flood alert arrives or
  /// is tapped, so Home can refresh its banner.
  ///
  /// [firebaseReady] is false when Firebase could not be initialised; in that
  /// case this is a no-op and the app stays pull-only.
  Future<void> initMessaging({
    required bool firebaseReady,
    required void Function() onAlert,
  }) async {
    _pushAvailable = firebaseReady;
    if (!firebaseReady) {
      _log('push disabled — Firebase is not configured');
      return;
    }

    await _initLocalNotifications();

    FirebaseMessaging.onBackgroundMessage(floodAlertBackgroundHandler);

    FirebaseMessaging.onMessage.listen((message) {
      _showLocal(message);
      onAlert();
    });

    FirebaseMessaging.onMessageOpenedApp.listen((_) => onAlert());

    final initial = await FirebaseMessaging.instance.getInitialMessage();
    if (initial != null) onAlert();

    FirebaseMessaging.instance.onTokenRefresh.listen(_sendToken);
  }

  /// Asks for notification permission and registers the device token. Called
  /// after login and after register — never at first launch (§9).
  Future<void> registerDevice() async {
    if (!_pushAvailable) return;
    try {
      await FirebaseMessaging.instance.requestPermission();
      final token = await FirebaseMessaging.instance.getToken();
      if (token != null) await _sendToken(token);
    } on Exception catch (e) {
      _log('token registration failed: ${e.runtimeType}');
    }
  }

  /// Called on logout, while the auth token is still valid.
  Future<void> unregisterDevice() async {
    if (!_pushAvailable) return;
    try {
      final token = await FirebaseMessaging.instance.getToken();
      if (token == null) return;
      await _api.delete('/devices/fcm-token', body: {'fcmToken': token});
    } on ApiException catch (e) {
      // Logging out must never be blocked by a failed cleanup call.
      _log('token unregister failed: ${e.statusCode}');
    } on Exception catch (e) {
      _log('token unregister failed: ${e.runtimeType}');
    }
  }

  Future<void> _sendToken(String token) async {
    try {
      await _api.post('/devices/fcm-token', {
        'fcmToken': token,
        'platform': Platform.isIOS ? 'ios' : 'android',
      });
      _log('device token registered');
    } on ApiException catch (e) {
      _log('device token rejected: ${e.statusCode}');
    }
  }

  Future<void> _initLocalNotifications() async {
    const settings = InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      iOS: DarwinInitializationSettings(),
    );
    await _local.initialize(settings);

    await _local
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >()
        ?.createNotificationChannel(
          const AndroidNotificationChannel(
            _channelId,
            'Flood alerts',
            description: 'Flood risk warnings for your area',
            importance: Importance.max,
          ),
        );
  }

  /// Foreground messages only — in the background the OS already drew it.
  Future<void> _showLocal(RemoteMessage message) async {
    final notification = message.notification;
    if (notification == null) return;

    await _local.show(
      notification.hashCode,
      notification.title ?? 'Flood alert',
      notification.body,
      const NotificationDetails(
        android: AndroidNotificationDetails(
          _channelId,
          'Flood alerts',
          channelDescription: 'Flood risk warnings for your area',
          importance: Importance.max,
          priority: Priority.high,
        ),
        iOS: DarwinNotificationDetails(),
      ),
    );
  }

  void _log(String message) {
    if (kDebugMode) debugPrint('[alerts] $message');
  }
}
