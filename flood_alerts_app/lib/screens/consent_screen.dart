import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/consent_text.dart';
import '../providers/auth_provider.dart';
import '../providers/consent_provider.dart';
import '../widgets/primary_button.dart';

/// Shown once a participant is authenticated but has not consented to the
/// current notice. Nothing that collects data is reachable from here — the
/// only ways out are agreeing or signing out.
class ConsentScreen extends StatefulWidget {
  const ConsentScreen({super.key});

  @override
  State<ConsentScreen> createState() => _ConsentScreenState();
}

class _ConsentScreenState extends State<ConsentScreen> {
  bool _isAdult = false;
  bool _agrees = false;

  /// Both boxes start clear and must be ticked deliberately. A pre-ticked box
  /// is not the affirmative act the PDPA requires.
  bool get _canContinue => _isAdult && _agrees;

  Future<void> _accept() async {
    final userId = context.read<AuthProvider>().user?.id;
    if (userId == null) return;
    await context.read<ConsentProvider>().grant(userId);
  }

  Future<void> _decline() async {
    final messenger = ScaffoldMessenger.of(context);
    final auth = context.read<AuthProvider>();
    final consent = context.read<ConsentProvider>();

    final leaving = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Not taking part?'),
        content: const Text(
          'That is completely fine and you do not need to give a reason. '
          'The app cannot take part in the study without location data, so '
          'you will be signed out. You can come back and agree at any time.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Go back'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Sign out'),
          ),
        ],
      ),
    );

    if (leaving != true) return;
    consent.reset();
    await auth.logout();
    messenger.showSnackBar(
      const SnackBar(content: Text('Signed out. Nothing was collected.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isBusy = context.select<ConsentProvider, bool>((c) => c.isBusy);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Before you start'),
        automaticallyImplyLeading: false,
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
                children: [
                  if (kDebugMode && ConsentText.hasPlaceholders)
                    const _PlaceholderWarning(),
                  Text('Your privacy', style: theme.textTheme.headlineSmall),
                  const SizedBox(height: 4),
                  Text(
                    'Version ${ConsentText.version}',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.outline,
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text(ConsentText.intro, style: theme.textTheme.bodyLarge),
                  for (final section in ConsentText.sections) ...[
                    const SizedBox(height: 24),
                    Text(
                      section.title,
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(section.body),
                  ],
                  const SizedBox(height: 28),
                  const Divider(),
                  CheckboxListTile(
                    value: _isAdult,
                    onChanged: isBusy
                        ? null
                        : (v) => setState(() => _isAdult = v ?? false),
                    title: const Text(ConsentText.ageDeclaration),
                    controlAffinity: ListTileControlAffinity.leading,
                    contentPadding: EdgeInsets.zero,
                  ),
                  CheckboxListTile(
                    value: _agrees,
                    onChanged: isBusy
                        ? null
                        : (v) => setState(() => _agrees = v ?? false),
                    title: const Text(ConsentText.consentDeclaration),
                    controlAffinity: ListTileControlAffinity.leading,
                    contentPadding: EdgeInsets.zero,
                  ),
                ],
              ),
            ),
            // Kept out of the scroll view so the choice is always reachable
            // without the participant having to hunt for it.
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  PrimaryButton(
                    label: 'I agree — start',
                    isBusy: isBusy,
                    onPressed: _canContinue ? _accept : null,
                    semanticLabel: _canContinue
                        ? 'Agree and start taking part'
                        : 'Tick both boxes above to continue',
                  ),
                  const SizedBox(height: 4),
                  TextButton(
                    onPressed: isBusy ? null : _decline,
                    child: const Text('No thanks, sign me out'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Debug-only. Stops the bracketed placeholders in ConsentText reaching a real
/// participant unnoticed.
class _PlaceholderWarning extends StatelessWidget {
  const _PlaceholderWarning();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 20),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.errorContainer,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.warning_amber_rounded,
            size: 20,
            color: scheme.onErrorContainer,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'This notice still contains placeholders. Fill in the controller, '
              'contact address and retention period in core/consent_text.dart '
              'before any real participant sees this.',
              style: TextStyle(color: scheme.onErrorContainer, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}
