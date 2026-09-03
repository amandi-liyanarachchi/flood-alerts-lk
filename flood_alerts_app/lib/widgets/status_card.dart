import 'package:flutter/material.dart';

/// Icon + title + a few lines of detail, used for the location status on Home.
class StatusCard extends StatelessWidget {
  const StatusCard({
    super.key,
    required this.icon,
    required this.title,
    required this.lines,
    this.iconColor,
    this.footer,
  });

  final IconData icon;
  final String title;
  final List<String> lines;
  final Color? iconColor;
  final Widget? footer;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: iconColor ?? theme.colorScheme.primary, size: 28),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: theme.textTheme.titleMedium),
                  for (final line in lines) ...[
                    const SizedBox(height: 4),
                    Text(
                      line,
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                  if (footer != null) ...[const SizedBox(height: 12), footer!],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
