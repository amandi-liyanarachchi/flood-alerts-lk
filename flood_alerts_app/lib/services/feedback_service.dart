import 'package:shared_preferences/shared_preferences.dart';

import '../core/api_client.dart';
import '../models/feedback_answer.dart';

/// Submits the one-question crowdsourced answer and remembers when the user
/// last answered, so Home can show the acknowledgement across app restarts.
///
/// Nothing is aggregated here — the 75% rule and the region buckets are
/// entirely server-side (§1).
class FeedbackService {
  FeedbackService(this._api);

  final ApiClient _api;

  static const String _answeredAtKey = 'feedback_last_answered_at';
  static const String _answerKey = 'feedback_last_answer';

  /// [latitude] and [longitude] may be null: the server then falls back to the
  /// user's last known ping to bucket the answer (§7).
  Future<void> submit({
    required bool floodPresent,
    required double? latitude,
    required double? longitude,
  }) async {
    final answer = FeedbackAnswer(
      floodPresent: floodPresent,
      latitude: latitude,
      longitude: longitude,
      answeredAt: DateTime.now().toUtc(),
    );

    await _api.post('/feedback', answer.toJson());

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_answeredAtKey, answer.answeredAt.toIso8601String());
    await prefs.setBool(_answerKey, floodPresent);
  }

  Future<({DateTime answeredAt, bool floodPresent})?> lastAnswer() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_answeredAtKey);
    final value = prefs.getBool(_answerKey);
    if (raw == null || value == null) return null;
    final at = DateTime.tryParse(raw);
    if (at == null) return null;
    return (answeredAt: at.toUtc(), floodPresent: value);
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_answeredAtKey);
    await prefs.remove(_answerKey);
  }
}
