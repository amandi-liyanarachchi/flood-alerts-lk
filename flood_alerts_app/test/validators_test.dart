import 'package:flutter_test/flutter_test.dart';
import 'package:floodwatch_lk/core/validators.dart';

void main() {
  group('nic', () {
    test('accepts old format with uppercase V', () {
      expect(Validators.nic('912345678V'), isNull);
    });

    test('accepts old format with uppercase W', () {
      expect(Validators.nic('912345678W'), isNull);
    });

    test('accepts old format with lowercase v', () {
      expect(Validators.nic('912345678v'), isNull);
    });

    test('accepts old format with lowercase w', () {
      expect(Validators.nic('912345678w'), isNull);
    });

    test('accepts new 12-digit format', () {
      expect(Validators.nic('199112345678'), isNull);
    });

    test('accepts surrounding and internal whitespace', () {
      expect(Validators.nic('  912345678V  '), isNull);
      expect(Validators.nic('9121 45678 V'), isNull);
    });

    test('rejects 9 digits with no trailing letter', () {
      expect(Validators.nic('912345678'), isNotNull);
    });

    test('rejects 11 digits', () {
      expect(Validators.nic('19911234567'), isNotNull);
    });

    test('rejects 13 digits', () {
      expect(Validators.nic('1991123456789'), isNotNull);
    });

    test('rejects trailing X — research requirement is V/W only', () {
      expect(Validators.nic('912345678X'), isNotNull);
    });

    test('rejects letters inside the number', () {
      expect(Validators.nic('91A345678V'), isNotNull);
    });

    test('rejects a leading letter', () {
      expect(Validators.nic('V912345678'), isNotNull);
    });

    test('rejects 12 digits followed by a letter', () {
      expect(Validators.nic('199112345678V'), isNotNull);
    });

    test('rejects empty and null', () {
      expect(Validators.nic(''), 'NIC is required');
      expect(Validators.nic('   '), 'NIC is required');
      expect(Validators.nic(null), 'NIC is required');
    });

    test('format failure returns the specified message', () {
      expect(
        Validators.nic('912345678'),
        'NIC must be 9 digits ending in V or W, or 12 digits',
      );
    });
  });

  group('normaliseNic', () {
    test('uppercases the trailing letter so v and V are one account', () {
      expect(Validators.normaliseNic('912345678v'), '912345678V');
      expect(Validators.normaliseNic('912345678V'), '912345678V');
    });

    test('strips whitespace', () {
      expect(Validators.normaliseNic('  912 345 678 v '), '912345678V');
    });

    test('leaves a 12-digit NIC untouched', () {
      expect(Validators.normaliseNic('199112345678'), '199112345678');
    });
  });

  group('phone', () {
    test('accepts a valid 07 mobile', () {
      expect(Validators.phone('0712345678'), isNull);
      expect(Validators.phone('0771234567'), isNull);
    });

    test('accepts spaced input', () {
      expect(Validators.phone('071 234 5678'), isNull);
    });

    test('rejects 9 digits', () {
      expect(Validators.phone('071234567'), isNotNull);
    });

    test('rejects 11 digits', () {
      expect(Validators.phone('07123456789'), isNotNull);
    });

    test('rejects a landline prefix', () {
      expect(Validators.phone('0112345678'), isNotNull);
    });

    test('rejects the +94 international form', () {
      expect(Validators.phone('+94712345678'), isNotNull);
    });

    test('rejects letters', () {
      expect(Validators.phone('07123A5678'), isNotNull);
    });

    test('rejects empty and null', () {
      expect(Validators.phone(''), 'Mobile number is required');
      expect(Validators.phone(null), 'Mobile number is required');
    });

    test('format failure returns the specified message', () {
      expect(
        Validators.phone('0112345678'),
        'Mobile number must be 10 digits starting with 07',
      );
    });
  });

  group('password', () {
    test('accepts exactly 8 characters', () {
      expect(Validators.password('12345678'), isNull);
    });

    test('accepts a simple 8+ char password with no complexity rules', () {
      expect(Validators.password('aaaaaaaa'), isNull);
    });

    test('rejects 7 characters', () {
      expect(
        Validators.password('1234567'),
        'Password must be at least 8 characters',
      );
    });

    test('rejects empty and null', () {
      expect(Validators.password(''), 'Password is required');
      expect(Validators.password(null), 'Password is required');
    });
  });

  group('confirmPassword', () {
    test('accepts an exact match', () {
      expect(Validators.confirmPassword('secret123', 'secret123'), isNull);
    });

    test('rejects a mismatch', () {
      expect(
        Validators.confirmPassword('secret123', 'secret124'),
        'Passwords do not match',
      );
    });

    test('is case sensitive', () {
      expect(Validators.confirmPassword('Secret123', 'secret123'), isNotNull);
    });

    test('rejects empty', () {
      expect(
        Validators.confirmPassword('', 'secret123'),
        'Please re-enter your password',
      );
    });
  });

  group('name', () {
    test('accepts plain letters', () {
      expect(Validators.name('Nimal', 'First name'), isNull);
    });

    test('accepts spaces, hyphens and apostrophes', () {
      expect(Validators.name('Anne Marie', 'First name'), isNull);
      expect(Validators.name("O'Brien", 'Last name'), isNull);
      expect(Validators.name('Smith-Jones', 'Last name'), isNull);
    });

    test('accepts exactly 50 characters', () {
      expect(Validators.name('a' * 50, 'First name'), isNull);
    });

    test('rejects 51 characters', () {
      expect(
        Validators.name('a' * 51, 'First name'),
        'First name must be 50 characters or fewer',
      );
    });

    test('rejects digits', () {
      expect(Validators.name('Nimal2', 'First name'), isNotNull);
    });

    test('rejects empty and whitespace-only', () {
      expect(Validators.name('', 'First name'), 'First name is required');
      expect(Validators.name('   ', 'Last name'), 'Last name is required');
      expect(Validators.name(null, 'First name'), 'First name is required');
    });

    test('uses the supplied label in the message', () {
      expect(Validators.name('', 'Last name'), 'Last name is required');
    });
  });
}
