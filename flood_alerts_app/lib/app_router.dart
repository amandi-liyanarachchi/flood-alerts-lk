import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'providers/auth_provider.dart';
import 'providers/consent_provider.dart';
import 'screens/consent_screen.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'screens/profile_screen.dart';
import 'screens/register_screen.dart';
import 'screens/splash_screen.dart';

class AppRoutes {
  const AppRoutes._();

  static const String register = '/register';
  static const String profile = '/profile';
}

/// Used by the notification-tap handler, which has no BuildContext.
final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

Map<String, WidgetBuilder> appRoutes() => {
  AppRoutes.register: (_) => const RegisterScreen(),
  AppRoutes.profile: (_) => const ProfileScreen(),
};

/// The root widget. Restores the stored session once, then shows Login or
/// Home and swaps between them whenever AuthProvider's status changes — so
/// logging out or hitting a 401 anywhere returns the user to Login without
/// any screen having to navigate.
class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) context.read<AuthProvider>().restoreSession();
    });
  }

  @override
  Widget build(BuildContext context) {
    final status = context.select<AuthProvider, AuthStatus>((a) => a.status);
    if (status == AuthStatus.unknown) return const SplashScreen();
    if (status == AuthStatus.unauthenticated) return const LoginScreen();

    // Authenticated. Consent gates everything past this point: HomeScreen is
    // what starts location tracking, so keeping it unreachable until consent
    // is recorded is what guarantees nothing is collected beforehand.
    final userId = context.select<AuthProvider, String?>((a) => a.user?.id);
    if (userId == null) return const SplashScreen();

    final consent = context.watch<ConsentProvider>();
    if (consent.status == ConsentStatus.unknown) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) context.read<ConsentProvider>().loadFor(userId);
      });
      return const SplashScreen();
    }

    return switch (consent.status) {
      ConsentStatus.needed => const ConsentScreen(),
      _ => const HomeScreen(),
    };
  }
}
