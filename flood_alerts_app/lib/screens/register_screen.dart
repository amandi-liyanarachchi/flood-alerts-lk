import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/validators.dart';
import '../providers/auth_provider.dart';
import '../widgets/app_text_field.dart';
import '../widgets/error_banner.dart';
import '../widgets/primary_button.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _formKey = GlobalKey<FormState>();
  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  final _nic = TextEditingController();
  final _phone = TextEditingController();
  final _password = TextEditingController();
  final _confirmPassword = TextEditingController();

  @override
  void dispose() {
    _firstName.dispose();
    _lastName.dispose();
    _nic.dispose();
    _phone.dispose();
    _password
      ..clear()
      ..dispose();
    _confirmPassword
      ..clear()
      ..dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    if (!_formKey.currentState!.validate()) return;

    final ok = await context.read<AuthProvider>().register(
      nic: _nic.text,
      firstName: _firstName.text,
      lastName: _lastName.text,
      phone: _phone.text,
      password: _password.text,
    );
    if (!mounted || !ok) return;

    // Register auto-logs-in (§5.3). AuthGate has already swapped the root over
    // to Home, so this route just needs to get out of the way.
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();

    return Scaffold(
      appBar: AppBar(title: const Text('Create an account')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Form(
                key: _formKey,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AppTextField(
                      controller: _firstName,
                      label: 'First name',
                      validator: (v) => Validators.name(v, 'First name'),
                      textCapitalization: TextCapitalization.words,
                      autofillHints: const [AutofillHints.givenName],
                      enabled: !auth.isBusy,
                    ),
                    const SizedBox(height: 16),
                    AppTextField(
                      controller: _lastName,
                      label: 'Last name',
                      validator: (v) => Validators.name(v, 'Last name'),
                      textCapitalization: TextCapitalization.words,
                      autofillHints: const [AutofillHints.familyName],
                      enabled: !auth.isBusy,
                    ),
                    const SizedBox(height: 16),
                    AppTextField(
                      controller: _nic,
                      label: 'NIC',
                      helperText: '912345678V or 199112345678',
                      validator: Validators.nic,
                      textCapitalization: TextCapitalization.characters,
                      autofillHints: const [AutofillHints.username],
                      enabled: !auth.isBusy,
                    ),
                    const SizedBox(height: 16),
                    AppTextField(
                      controller: _phone,
                      label: 'Mobile number',
                      helperText: '0712345678',
                      validator: Validators.phone,
                      keyboardType: TextInputType.phone,
                      autofillHints: const [AutofillHints.telephoneNumber],
                      enabled: !auth.isBusy,
                    ),
                    const SizedBox(height: 16),
                    AppTextField(
                      controller: _password,
                      label: 'Password',
                      helperText: 'At least 8 characters',
                      validator: Validators.password,
                      obscureText: true,
                      autofillHints: const [AutofillHints.newPassword],
                      enabled: !auth.isBusy,
                    ),
                    const SizedBox(height: 16),
                    AppTextField(
                      controller: _confirmPassword,
                      label: 'Confirm password',
                      validator: (v) =>
                          Validators.confirmPassword(v, _password.text),
                      obscureText: true,
                      textInputAction: TextInputAction.done,
                      enabled: !auth.isBusy,
                      onFieldSubmitted: (_) => _submit(),
                    ),
                    if (auth.errorMessage != null) ...[
                      const SizedBox(height: 16),
                      ErrorBanner(message: auth.errorMessage!),
                    ],
                    const SizedBox(height: 24),
                    PrimaryButton(
                      label: 'Create account',
                      isBusy: auth.isBusy,
                      onPressed: _submit,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
