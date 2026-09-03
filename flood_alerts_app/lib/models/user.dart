class User {
  const User({
    required this.id,
    required this.nic,
    required this.firstName,
    required this.lastName,
    required this.phone,
  });

  final String id;
  final String nic;
  final String firstName;
  final String lastName;
  final String phone;

  String get fullName => '$firstName $lastName';

  factory User.fromJson(Map<String, dynamic> json) => User(
    id: json['id'] as String? ?? '',
    nic: json['nic'] as String? ?? '',
    firstName: json['firstName'] as String? ?? '',
    lastName: json['lastName'] as String? ?? '',
    phone: json['phone'] as String? ?? '',
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'nic': nic,
    'firstName': firstName,
    'lastName': lastName,
    'phone': phone,
  };
}
