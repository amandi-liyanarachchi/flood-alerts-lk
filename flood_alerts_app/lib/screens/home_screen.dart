import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_router.dart';
import '../models/flood_alert.dart';
import '../providers/home_provider.dart';
import '../services/location_service.dart';
import '../widgets/primary_button.dart';
import '../widgets/status_card.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) context.read<HomeProvider>().onSignedIn();
    });
  }

  void _report(({bool ok, String message}) result) {
    if (!mounted) return;
    final scheme = Theme.of(context).colorScheme;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(result.message),
          backgroundColor: result.ok ? null : scheme.error,
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    final home = context.watch<HomeProvider>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Flood Alerts LK'),
        actions: [
          IconButton(
            icon: const Icon(Icons.person_outline),
            tooltip: 'Profile',
            onPressed: () => Navigator.of(context).pushNamed(AppRoutes.profile),
          ),
        ],
      ),
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: home.refresh,
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
            children: [
              if (home.alert != null) ...[
                _AlertBanner(alert: home.alert!),
                const SizedBox(height: 16),
              ],
              _LocationStatus(home: home),
              const SizedBox(height: 16),
              PrimaryButton(
                label: 'Send My Location Now',
                icon: Icons.my_location,
                large: true,
                isBusy: home.isSending,
                semanticLabel: 'Send my location to Flood Alerts LK now',
                onPressed: () async => _report(await home.sendLocationNow()),
              ),
              if (_needsPermission(home.access)) ...[
                const SizedBox(height: 16),
                _PermissionWarning(home: home),
              ],
              const SizedBox(height: 16),
              _FeedbackCard(home: home, onResult: _report),
            ],
          ),
        ),
      ),
    );
  }

  static bool _needsPermission(LocationAccess access) =>
      access != LocationAccess.always;
}

// --------------------------------------------------------------------- alert

class _AlertBanner extends StatelessWidget {
  const _AlertBanner({required this.alert});

  final FloodAlert alert;

  @override
  Widget build(BuildContext context) {
    final (background, foreground, label) = switch (alert.severity) {
      AlertSeverity.high => (
        const Color(0xFFB3261E),
        Colors.white,
        'HIGH RISK',
      ),
      AlertSeverity.moderate => (
        const Color(0xFFFFE082),
        const Color(0xFF5B4300),
        'MODERATE RISK',
      ),
      AlertSeverity.low => (
        const Color(0xFFE3F2FD),
        const Color(0xFF0D3C61),
        'ADVISORY',
      ),
    };

    return Semantics(
      liveRegion: true,
      container: true,
      child: Card(
        margin: EdgeInsets.zero,
        color: background,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.warning_amber_rounded, color: foreground),
                  const SizedBox(width: 8),
                  Text(
                    label,
                    style: TextStyle(
                      color: foreground,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.6,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Text(
                alert.title,
                style: Theme.of(context).textTheme.titleMedium
                    ?.copyWith(color: foreground, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 6),
              Text(alert.message, style: TextStyle(color: foreground)),
              const SizedBox(height: 10),
              Text(
                'Issued ${_relative(alert.issuedAt)}',
                style: TextStyle(
                  color: foreground.withValues(alpha: 0.85),
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------ location

class _LocationStatus extends StatelessWidget {
  const _LocationStatus({required this.home});

  final HomeProvider home;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    final (title, icon, color) = switch (home.access) {
      LocationAccess.always when home.isTracking => (
        'Location sharing: Active',
        Icons.location_on,
        scheme.primary,
      ),
      // start() does run the stream on while-in-use, so with the app open we
      // really are uploading. Calling that "Paused" would be wrong; the
      // caveat about closing the app is on the line below.
      LocationAccess.whileInUse when home.isTracking => (
        'Location sharing: Active while open',
        Icons.location_searching,
        const Color(0xFF8A6100),
      ),
      LocationAccess.whileInUse => (
        'Location sharing: Paused',
        Icons.location_searching,
        const Color(0xFF8A6100),
      ),
      LocationAccess.serviceDisabled => (
        'Location sharing: GPS is off',
        Icons.location_disabled,
        scheme.error,
      ),
      LocationAccess.denied || LocationAccess.deniedForever => (
        'Location sharing: Permission needed',
        Icons.location_disabled,
        scheme.error,
      ),
      _ => (
        'Location sharing: Paused',
        Icons.location_searching,
        const Color(0xFF8A6100),
      ),
    };

    final sentAt = home.lastSentAt;
    return StatusCard(
      icon: icon,
      iconColor: color,
      title: title,
      lines: [
        sentAt == null
            ? 'Last sent: not yet'
            : 'Last sent: ${_relative(sentAt)}',
        if (home.pendingCount > 0)
          '${home.pendingCount} ping${home.pendingCount == 1 ? '' : 's'} waiting to upload',
        if (home.access == LocationAccess.whileInUse)
          'Background sharing is off, so pings stop when you close the app.',
      ],
    );
  }
}

class _PermissionWarning extends StatelessWidget {
  const _PermissionWarning({required this.home});

  final HomeProvider home;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final access = home.access;

    final (message, actionLabel, action) = switch (access) {
      LocationAccess.serviceDisabled => (
        'Location services are switched off. Flood Alerts LK cannot send your '
            'location until GPS is on.',
        'Open location settings',
        home.openLocationSettings,
      ),
      LocationAccess.deniedForever => (
        'Location permission is blocked. Open app settings to allow it.',
        'Open app settings',
        home.openAppSettings,
      ),
      LocationAccess.whileInUse => (
        'Flood Alerts LK can only see your location while the app is open. Allow '
            '"Always" so we can warn you during a flood.',
        'Allow all the time',
        home.openAppSettings,
      ),
      _ => (
        'Flood Alerts LK needs your location to send you flood alerts for your '
            'area.',
        'Allow location',
        home.requestAccess,
      ),
    };

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: scheme.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.info_outline, size: 20, color: scheme.onSurface),
              const SizedBox(width: 10),
              Expanded(child: Text(message)),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: OutlinedButton(
              onPressed: () => action(),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
              ),
              child: Text(actionLabel),
            ),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ feedback

class _FeedbackCard extends StatelessWidget {
  const _FeedbackCard({required this.home, required this.onResult});

  final HomeProvider home;
  final void Function(({bool ok, String message})) onResult;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'Is there flooding in your area right now?',
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (home.isAnswering)
              ..._controls(context)
            else
              ..._recorded(context),
          ],
        ),
      ),
    );
  }

  List<Widget> _controls(BuildContext context) => [
    RadioGroup<bool>(
      groupValue: home.pendingChoice,
      // RadioGroup.onChanged is non-nullable, so the disabled case is
      // guarded here rather than by passing null.
      onChanged: (v) {
        if (v == null || home.isSubmittingFeedback) return;
        home.selectAnswer(v);
      },
      child: const Column(
        children: [
          RadioListTile<bool>(
            value: true,
            title: Text('Yes'),
            contentPadding: EdgeInsets.zero,
          ),
          RadioListTile<bool>(
            value: false,
            title: Text('No'),
            contentPadding: EdgeInsets.zero,
          ),
        ],
      ),
    ),
    const SizedBox(height: 8),
    PrimaryButton(
      label: 'Submit',
      isBusy: home.isSubmittingFeedback,
      onPressed: () async => onResult(await home.submitFeedback()),
    ),
  ];

  List<Widget> _recorded(BuildContext context) {
    final at = home.answeredAt!.toLocal();
    return [
      Row(
        children: [
          Icon(
            Icons.check_circle_outline,
            size: 20,
            color: Theme.of(context).colorScheme.primary,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Thanks — your answer was recorded at '
              '${DateFormat('HH:mm').format(at)}',
            ),
          ),
        ],
      ),
      Align(
        alignment: Alignment.centerLeft,
        child: TextButton(
          onPressed: home.editAnswer,
          child: const Text('Change my answer'),
        ),
      ),
    ];
  }
}

/// "3 minutes ago" for recent times, an absolute stamp once it is stale.
String _relative(DateTime utc) {
  final diff = DateTime.now().toUtc().difference(utc);
  if (diff.isNegative || diff.inSeconds < 45) return 'just now';
  if (diff.inMinutes < 60) {
    return '${diff.inMinutes} minute${diff.inMinutes == 1 ? '' : 's'} ago';
  }
  if (diff.inHours < 24) {
    return '${diff.inHours} hour${diff.inHours == 1 ? '' : 's'} ago';
  }
  return DateFormat('d MMM, HH:mm').format(utc.toLocal());
}
