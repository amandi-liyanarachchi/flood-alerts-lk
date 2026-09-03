import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/consent_text.dart';
import '../providers/auth_provider.dart';
import '../providers/consent_provider.dart';
import '../providers/home_provider.dart';
import '../services/location_service.dart';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key});

  /// Order matters: the FCM token has to be deleted server-side while the auth
  /// token is still valid, so tear the session down before clearing it (§5.5).
  Future<void> _logout(BuildContext context) async {
    // Resolved before the first await so no BuildContext is used across a gap.
    final navigator = Navigator.of(context);
    final home = context.read<HomeProvider>();
    final auth = context.read<AuthProvider>();
    final consent = context.read<ConsentProvider>();
    await home.onSignOut();
    consent.reset();
    await auth.logout();
    navigator.popUntil((route) => route.isFirst);
  }

  /// The PDPA requires withdrawing to be as easy as consenting, so this sits
  /// on the same screen as the profile rather than behind a support request.
  Future<void> _withdraw(BuildContext context) async {
    final navigator = Navigator.of(context);
    final messenger = ScaffoldMessenger.of(context);
    final home = context.read<HomeProvider>();
    final auth = context.read<AuthProvider>();
    final consent = context.read<ConsentProvider>();
    final userId = auth.user?.id;
    if (userId == null) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Withdraw consent?'),
        content: const Text(
          'Location sharing stops immediately and you will be signed out. '
          'Anything still waiting to upload is discarded.\n\n'
          'This does not delete data already collected. To ask for that, '
          'email ${ConsentText.contactEmail}.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Withdraw'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    // Recorded before the session is torn down, while the token is still
    // valid, so the server can be told.
    await consent.withdraw(userId);
    await home.onSignOut();
    await auth.logout();
    navigator.popUntil((route) => route.isFirst);
    messenger.showSnackBar(
      const SnackBar(content: Text('Consent withdrawn. Sharing has stopped.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = context.watch<AuthProvider>().user;
    final home = context.watch<HomeProvider>();

    if (user == null) {
      return const Scaffold(body: SizedBox.shrink());
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          _Field(label: 'First name', value: user.firstName),
          _Field(label: 'Last name', value: user.lastName),
          _Field(label: 'NIC', value: user.nic),
          _Field(label: 'Mobile number', value: user.phone),
          const Divider(height: 24),
          ListTile(
            leading: Icon(
              _icon(home.access),
              color: home.access == LocationAccess.always
                  ? Theme.of(context).colorScheme.primary
                  : Theme.of(context).colorScheme.error,
            ),
            title: Text(
              'Location permission',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            subtitle: Text(
              _label(home.access),
              style: Theme.of(context).textTheme.bodyLarge,
            ),
            trailing: home.access == LocationAccess.always
                ? null
                : TextButton(
                    onPressed: home.openAppSettings,
                    child: const Text('Change'),
                  ),
          ),
          const Divider(height: 24),
          ListTile(
            leading: Icon(
              Icons.privacy_tip_outlined,
              color: Theme.of(context).colorScheme.primary,
            ),
            title: Text(
              'Data consent',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            subtitle: Text(
              'Given — notice version ${ConsentText.version}',
              style: Theme.of(context).textTheme.bodyLarge,
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: OutlinedButton(
              onPressed: () => _withdraw(context),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
              ),
              child: const Text('Withdraw consent and stop sharing'),
            ),
          ),
          const Divider(height: 24),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: OutlinedButton.icon(
              onPressed: () => _logout(context),
              icon: const Icon(Icons.logout),
              label: const Text('Logout'),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
                foregroundColor: Theme.of(context).colorScheme.error,
              ),
            ),
          ),
        ],
      ),
    );
  }

  static IconData _icon(LocationAccess access) => switch (access) {
    LocationAccess.always => Icons.location_on,
    LocationAccess.whileInUse => Icons.location_searching,
    _ => Icons.location_disabled,
  };

  static String _label(LocationAccess access) => switch (access) {
    LocationAccess.always => 'Allowed all the time',
    LocationAccess.whileInUse => 'Only while using the app',
    LocationAccess.serviceDisabled => 'Location services are off',
    LocationAccess.deniedForever => 'Blocked — change it in app settings',
    LocationAccess.denied => 'Not granted',
  };
}

class _Field extends StatelessWidget {
  const _Field({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      title: Text(label, style: Theme.of(context).textTheme.bodySmall),
      subtitle: Text(value, style: Theme.of(context).textTheme.bodyLarge),
    );
  }
}
