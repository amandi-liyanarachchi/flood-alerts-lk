import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:floodwatch_lk/core/api_client.dart';
import 'package:floodwatch_lk/core/consent_text.dart';
import 'package:floodwatch_lk/services/consent_service.dart';

/// These cover the gating rule only. Nothing here touches the network:
/// hasConsented and read go to shared_preferences alone, so no request is
/// ever made.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late ConsentService service;

  ConsentService build() => ConsentService(ApiClient());

  String stored({required String version, required bool granted}) =>
      jsonEncode({
        'version': version,
        'granted': granted,
        'recordedAt': DateTime.now().toUtc().toIso8601String(),
      });

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    service = build();
  });

  group('hasConsented', () {
    test('is false when nothing has ever been recorded', () async {
      expect(await service.hasConsented('u_1'), isFalse);
    });

    test('is true for a grant against the current notice', () async {
      SharedPreferences.setMockInitialValues({
        'consent_u_1': stored(version: ConsentText.version, granted: true),
      });
      expect(await build().hasConsented('u_1'), isTrue);
    });

    test('is false once consent has been withdrawn', () async {
      SharedPreferences.setMockInitialValues({
        'consent_u_1': stored(version: ConsentText.version, granted: false),
      });
      expect(await build().hasConsented('u_1'), isFalse);
    });

    test('is false for a grant against an older notice', () async {
      // Consent is given to a specific notice. A material rewording bumps the
      // version and everyone must be asked again.
      SharedPreferences.setMockInitialValues({
        'consent_u_1': stored(version: '0.9', granted: true),
      });
      expect(await build().hasConsented('u_1'), isFalse);
    });

    test('is per user — one participant cannot consent for another', () async {
      SharedPreferences.setMockInitialValues({
        'consent_u_1': stored(version: ConsentText.version, granted: true),
      });
      final s = build();
      expect(await s.hasConsented('u_1'), isTrue);
      expect(await s.hasConsented('u_2'), isFalse);
    });

    test('is false when the stored record is corrupt', () async {
      SharedPreferences.setMockInitialValues({'consent_u_1': 'not json'});
      expect(await build().hasConsented('u_1'), isFalse);
    });

    test(
      'discards a corrupt record rather than re-reading it forever',
      () async {
        SharedPreferences.setMockInitialValues({'consent_u_1': '{"broken":'});
        await build().read('u_1');
        final prefs = await SharedPreferences.getInstance();
        expect(prefs.getString('consent_u_1'), isNull);
      },
    );
  });

  group('ConsentRecord', () {
    test('round-trips through JSON', () {
      final at = DateTime.utc(2026, 8, 31, 9, 15);
      final record = ConsentRecord(
        version: '1.0',
        granted: true,
        recordedAt: at,
      );
      final back = ConsentRecord.fromJson(record.toJson());
      expect(back.version, '1.0');
      expect(back.granted, isTrue);
      expect(back.recordedAt, at);
    });

    test('serialises the timestamp as UTC ISO-8601', () {
      final record = ConsentRecord(
        version: '1.0',
        granted: false,
        recordedAt: DateTime.utc(2026, 8, 31, 9, 15),
      );
      expect(record.toJson()['recordedAt'], '2026-08-31T09:15:00.000Z');
    });

    test('defaults to not granted when the field is missing', () {
      final back = ConsentRecord.fromJson({'version': '1.0'});
      expect(back.granted, isFalse);
    });
  });

  group('notice', () {
    test('carries no unfilled placeholders', () {
      // Guards against a placeholder being reintroduced, or a new required
      // field being added and left bracketed. A participant must never be
      // shown a notice with a blank where the controller should be.
      expect(ConsentText.hasPlaceholders, isFalse);
    });

    test('names a controller, a contact and a retention period', () {
      expect(ConsentText.controller, isNotEmpty);
      expect(ConsentText.contactEmail, contains('@'));
      expect(ConsentText.retentionPeriod, isNotEmpty);
    });

    test('declares a version', () {
      expect(ConsentText.version, isNotEmpty);
    });
  });
}
