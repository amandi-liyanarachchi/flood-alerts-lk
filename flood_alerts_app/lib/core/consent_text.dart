/// The privacy notice shown on the consent screen, and the details the
/// Sri Lankan Personal Data Protection Act No. 9 of 2022 requires it to carry.
///
/// The notice lives here as data rather than inside the screen so that the
/// wording, the version and the contact details are all in one auditable
/// place. Consent is recorded against [version]: bump it whenever the wording
/// changes materially and every user is asked again, because consent is given
/// to a specific notice and does not carry over to a different one.
class ConsentText {
  const ConsentText._();

  /// Bump on any material change to the notice below. Anything else is a typo
  /// fix and does not invalidate consent already given.
  static const String version = '1.0';

  // ---------------------------------------------------------------------
  // TODO(pdpa): these three must be filled in before the app is put in front
  // of a single real participant. The PDPA requires a data subject to be told
  // who controls their data, how to reach them, and how long it is kept. The
  // placeholders are deliberately obvious so they cannot ship unnoticed.
  // ---------------------------------------------------------------------

  /// The controller — the university or department running the study.
  static const String controller = 'KIU University - Faculty of Computer Science and Engineering';

  /// A monitored address a participant can actually use to exercise rights.
  static const String contactEmail = 'ama.liyanarachchi2001@gmail.com';

  /// How long location traces are kept before deletion.
  static const String retentionPeriod = '5 Years';

  /// True when the placeholders above are still in place, so the screen can
  /// warn in debug builds rather than quietly showing a bracketed placeholder
  /// to a participant.
  static bool get hasPlaceholders =>
      controller.contains('[') ||
      contactEmail.contains('[') ||
      retentionPeriod.contains('[');

  static const String intro =
      'Flood Alerts LK is part of a university research study into flood early '
      'warning for Sri Lanka. Taking part is voluntary. Please read this before '
      'you agree — it explains exactly what the app collects and what you can '
      'ask us to do with it.';

  static const List<ConsentSection> sections = [
    ConsentSection(
      title: 'What we collect',
      body:
          'Your name, your NIC number and your mobile number, which you gave us '
          'when you registered.\n\n'
          'Your location — latitude, longitude and accuracy — roughly every 10 '
          'minutes while you are taking part, including when the app is in the '
          'background and your phone is in your pocket. You can also send your '
          'location on demand with the "Send My Location Now" button.\n\n'
          'Your answers to the question "Is there flooding in your area right '
          'now?".\n\n'
          'A notification token for your device, so we can send you flood '
          'warnings.',
    ),
    ConsentSection(
      title: 'Why we collect it',
      body:
          'To detect floods sooner. Your location tells the system where people '
          'are, and your answers tell it what is actually happening on the '
          'ground. Combined with rainfall and river-level data, this is used to '
          'work out which areas may be flooding and to warn people there.\n\n'
          'Your data is also analysed as part of the research study and may be '
          'reported in academic publications. Nothing that identifies you '
          'personally is ever published.',
    ),
    ConsentSection(
      title: 'How long we keep it',
      body:
          'Your location history is kept for $retentionPeriod, then deleted.\n\n'
          'If you withdraw or ask us to delete your data, we remove it from our '
          'systems. Results already published in aggregate form cannot be '
          'withdrawn, because they contain nothing that identifies you.',
    ),
    ConsentSection(
      title: 'Who can see it',
      body:
          'The research team at $controller.\n\n'
          'We do not sell your data, and we do not share it for advertising. '
          'Aggregated flood signals — never individual locations — may be '
          'shared with disaster management authorities so that warnings reach '
          'the people who need them.',
    ),
    ConsentSection(
      title: 'Your rights under the PDPA',
      body:
          'Under the Personal Data Protection Act No. 9 of 2022 you can, at any '
          'time and free of charge:\n\n'
          '•  Ask for a copy of the data we hold about you\n'
          '•  Ask us to correct anything that is wrong\n'
          '•  Ask us to delete your data\n'
          '•  Withdraw your consent and stop taking part\n'
          '•  Object to how we are using your data\n'
          '•  Complain to the Data Protection Authority of Sri Lanka\n\n'
          'Withdrawing is as easy as giving consent: open Profile and tap '
          '"Withdraw consent". Location sharing stops immediately.',
    ),
    ConsentSection(
      title: 'If you say no',
      body:
          'Nothing happens to you, and you do not have to give a reason. The '
          'app simply cannot take part in the study without location data, so '
          'it will sign you out. You can come back and agree at any time.',
    ),
    ConsentSection(
      title: 'Contact',
      body:
          'Questions, or want to exercise any of the rights above?\n'
          'Email $contactEmail.',
    ),
  ];

  static const String ageDeclaration = 'I am 18 years of age or older.';

  static const String consentDeclaration =
      'I have read the above and I agree to Flood Alerts LK collecting and '
      'using my data as described, including sharing my location every 10 '
      'minutes in the background.';
}

class ConsentSection {
  const ConsentSection({required this.title, required this.body});

  final String title;
  final String body;
}
