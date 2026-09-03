import 'package:flutter/material.dart';

/// Full-width filled button that shows a spinner in place of its label while
/// an action is in flight. Minimum height comes from the theme (48dp).
class PrimaryButton extends StatelessWidget {
  const PrimaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.isBusy = false,
    this.icon,
    this.semanticLabel,
    this.large = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool isBusy;
  final IconData? icon;
  final String? semanticLabel;

  /// Taller variant for the Send My Location Now button.
  final bool large;

  @override
  Widget build(BuildContext context) {
    final child = isBusy
        ? const SizedBox(
            height: 20,
            width: 20,
            child: CircularProgressIndicator(strokeWidth: 2.5),
          )
        : Text(label, style: large ? const TextStyle(fontSize: 17) : null);

    return Semantics(
      button: true,
      enabled: onPressed != null && !isBusy,
      label: semanticLabel,
      child: FilledButton(
        onPressed: isBusy ? null : onPressed,
        style: large
            ? FilledButton.styleFrom(minimumSize: const Size.fromHeight(60))
            : null,
        child: icon == null || isBusy
            ? child
            : Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(icon, size: large ? 24 : 20),
                  const SizedBox(width: 10),
                  child,
                ],
              ),
      ),
    );
  }
}
