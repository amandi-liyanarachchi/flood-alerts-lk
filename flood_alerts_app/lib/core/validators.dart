/// Every validation rule in the app lives here. Never inline a regex in a
/// screen — these are unit-tested in test/validators_test.dart.
class Validators {
  const Validators._();

  /// Old-format NIC: 9 digits followed by V or W, e.g. 912345678V
  static final RegExp _oldNic = RegExp(r'^\d{9}[VWvw]$');

  /// New-format NIC: exactly 12 digits, e.g. 199112345678
  static final RegExp _newNic = RegExp(r'^\d{12}$');

  /// Sri Lankan mobile: 10 digits starting 07
  static final RegExp _phone = RegExp(r'^07\d{8}$');

  static final RegExp _name = RegExp(r"^[A-Za-z][A-Za-z \-']*$");

  /// NIC is the login identifier, so it must be stored and transmitted in one
  /// canonical form: whitespace stripped, trailing letter uppercased.
  static String normaliseNic(String raw) =>
      raw.replaceAll(RegExp(r'\s'), '').toUpperCase();

  static String normalisePhone(String raw) => raw.replaceAll(RegExp(r'\s'), '');

  static String? nic(String? value) {
    final v = normaliseNic(value ?? '');
    if (v.isEmpty) return 'NIC is required';
    if (_oldNic.hasMatch(v) || _newNic.hasMatch(v)) return null;
    return 'NIC must be 9 digits ending in V or W, or 12 digits';
  }

  static String? phone(String? value) {
    final v = normalisePhone(value ?? '');
    if (v.isEmpty) return 'Mobile number is required';
    if (_phone.hasMatch(v)) return null;
    return 'Mobile number must be 10 digits starting with 07';
  }

  static String? password(String? value) {
    final v = value ?? '';
    if (v.isEmpty) return 'Password is required';
    if (v.length < 8) return 'Password must be at least 8 characters';
    return null;
  }

  static String? confirmPassword(String? value, String? original) {
    if ((value ?? '').isEmpty) return 'Please re-enter your password';
    if (value != original) return 'Passwords do not match';
    return null;
  }

  /// [label] is used verbatim in the message, e.g. 'First name'.
  static String? name(String? value, String label) {
    final v = (value ?? '').trim();
    if (v.isEmpty) return '$label is required';
    if (v.length > 50) return '$label must be 50 characters or fewer';
    if (!_name.hasMatch(v)) {
      return '$label can only contain letters, spaces, hyphens and apostrophes';
    }
    return null;
  }
}
