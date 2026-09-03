import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_router.dart';
import 'core/api_client.dart';
import 'core/theme.dart';
import 'providers/auth_provider.dart';
import 'providers/consent_provider.dart';
import 'providers/home_provider.dart';
import 'services/alert_service.dart';
import 'services/auth_service.dart';
import 'services/consent_service.dart';
import 'services/feedback_service.dart';
import 'services/location_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // google-services.json and GoogleService-Info.plist are gitignored by design
  // (§13) and must be placed on each machine by hand. Without them Firebase
  // cannot start — so push is disabled and the app falls back to polling
  // GET /alerts/active, rather than refusing to launch.
  var firebaseReady = false;
  try {
    await Firebase.initializeApp();
    firebaseReady = true;
  } on Object catch (e) {
    if (kDebugMode) {
      debugPrint(
        'Firebase not configured (${e.runtimeType}) — push notifications are '
        'disabled. Add google-services.json / GoogleService-Info.plist and '
        'uncomment the google-services gradle plugin.',
      );
    }
  }

  runApp(FloodAlertsApp(firebaseReady: firebaseReady));
}

class FloodAlertsApp extends StatefulWidget {
  const FloodAlertsApp({super.key, required this.firebaseReady});

  final bool firebaseReady;

  @override
  State<FloodAlertsApp> createState() => _FloodAlertsAppState();
}

class _FloodAlertsAppState extends State<FloodAlertsApp> {
  late final ApiClient _api = ApiClient();

  late final AuthService _authService = AuthService(_api);
  late final ConsentService _consentService = ConsentService(_api);
  late final LocationService _locationService = LocationService(_api);
  late final FeedbackService _feedbackService = FeedbackService(_api);
  late final AlertService _alertService = AlertService(_api);

  late final AuthProvider _authProvider = AuthProvider(_authService);
  late final ConsentProvider _consentProvider = ConsentProvider(
    _consentService,
  );
  late final HomeProvider _homeProvider = HomeProvider(
    _locationService,
    _feedbackService,
    _alertService,
  );

  @override
  void initState() {
    super.initState();

    // Any 401, from any service, drops the session exactly once. The reset is
    // the network-free one: the token is already invalid, so calling the
    // server again would only 401 straight back into here.
    _api.onUnauthorized = () {
      unawaited(_homeProvider.resetForLostSession());
      // The next participant to sign in on this device is evaluated from
      // their own consent record, never the previous one's.
      _consentProvider.reset();
      _authProvider.handleUnauthorized();
    };

    _alertService.initMessaging(
      firebaseReady: widget.firebaseReady,
      onAlert: _onAlert,
    );
  }

  /// A push arrived or was tapped: get back to Home and refresh the banner.
  /// No deep-link routing beyond that (§9).
  void _onAlert() {
    navigatorKey.currentState?.popUntil((route) => route.isFirst);
    _homeProvider.refreshAlert();
  }

  @override
  void dispose() {
    _homeProvider.dispose();
    _consentProvider.dispose();
    _authProvider.dispose();
    _api.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthProvider>.value(value: _authProvider),
        ChangeNotifierProvider<ConsentProvider>.value(value: _consentProvider),
        ChangeNotifierProvider<HomeProvider>.value(value: _homeProvider),
      ],
      child: MaterialApp(
        title: 'Flood Alerts LK',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light(),
        navigatorKey: navigatorKey,
        routes: appRoutes(),
        home: const AuthGate(),
      ),
    );
  }
}
