import 'user.dart';

class AuthResponse {
  const AuthResponse({required this.token, required this.user});

  final String token;
  final User user;

  factory AuthResponse.fromJson(Map<String, dynamic> json) => AuthResponse(
    token: json['token'] as String? ?? '',
    user: User.fromJson(json['user'] as Map<String, dynamic>? ?? const {}),
  );
}
