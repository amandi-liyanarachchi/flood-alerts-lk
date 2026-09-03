import 'package:flutter/material.dart';

/// Shown only while AuthGate decides where to send the user. No branding
/// animation by design.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(body: Center(child: CircularProgressIndicator()));
  }
}
