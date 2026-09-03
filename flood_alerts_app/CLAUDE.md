# CLAUDE.md — Flood Alerts LK

Guidance for Claude Code when working in this repository.

---

## 1. What this project is

**Flood Alerts LK** is a cross-platform Flutter mobile app (Android + iOS) built for a research
project: *Smart Flood Early Warning System for Sri Lanka Using Crowdsourced Evidence,
Integrated Data, and AI*.

The app is a **data collection and alerting client**. It does two things:

1. **Sends the logged-in user's live GPS location to our server every 10 minutes**, automatically
   and without user interaction, plus on demand via a manual "Send Now" button.
2. **Collects one-question crowdsourced flood feedback** ("Is there flooding in your area right
   now?" → Yes / No) which the server aggregates per micro-region. If ≥75% of recent answers in a
   region are "Yes", the server-side AI model treats it as a possible flood signal.

It also **receives flood-risk push notifications** from the server.

All intelligence lives on the **server**. The app never computes flood risk, never aggregates
feedback, and never decides what a "region" is. It is a thin, reliable client.

### App name
- Display name: **Flood Alerts LK**
- Android `applicationId` / iOS bundle id: `lk.floodwatch.app`
- Flutter package name: `floodwatch_lk`

---

## 2. Hard constraints — read before writing any code

These exist because this is a research prototype with a small team, not a commercial product.

**DO:**
- Keep it simple. Basic Material 3 UI, basic colors, no custom design system.
- Use `Provider` for state, `http` for networking, plain hand-written `fromJson`/`toJson`.
- Prefer one obvious file over three clever ones.
- Handle errors visibly (SnackBar / inline text) — never swallow them silently.

**DO NOT:**
- Do not add: `dio`, `get_it`, `injectable`, `riverpod`, `bloc`, `freezed`, `json_serializable`,
  `build_runner`, `retrofit`, `auto_route`, `go_router`, or any code generation.
- Do not build a local SQLite database. The only local persistence needed is the auth token, the
  user profile, and a small failed-upload queue (see §8).
- Do not create abstract interfaces / repository layers with a single implementation.
- Do not add analytics, crash reporting, social login, profile photos, maps, charts,
  onboarding carousels, dark-mode theming work, localization files, or animations.
- Do not refactor working code for style. Do not add a feature that was not asked for.
- Do not write `main.dart` as a monolith — but equally, do not split a 40-line screen into
  5 files.

If a task seems to require breaking one of these rules, **stop and ask** rather than deciding.

**This section outranks the installed Flutter skills.** The skills are Flutter-team reference
material written for general production apps; several of them prescribe more structure than this
project wants. See §12 for which to use and where they are explicitly overridden.

---

## 3. Tech stack

| Concern | Choice |
|---|---|
| Framework | Flutter (stable channel), Dart 3, null-safety |
| Platforms | Android + iOS only (no web/desktop — delete those folders if present) |
| State management | `provider` (ChangeNotifier) |
| Networking | `http` |
| Location | `geolocator` (foreground **and** background, single package) |
| Push notifications | `firebase_core` + `firebase_messaging` |
| Local notification display | `flutter_local_notifications` (foreground messages only) |
| Secure token storage | `flutter_secure_storage` |
| Small non-secret prefs / upload queue | `shared_preferences` |
| Date formatting | `intl` |

`pubspec.yaml` dependencies — nothing beyond this list without asking:

```yaml
dependencies:
  flutter:
    sdk: flutter
  provider: ^6.1.2
  http: ^1.2.2
  geolocator: ^13.0.1
  firebase_core: ^3.6.0
  firebase_messaging: ^15.1.3
  flutter_local_notifications: ^17.2.3
  flutter_secure_storage: ^9.2.2
  shared_preferences: ^2.3.2
  intl: ^0.19.0

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^6.0.0
```

Pin versions loosely with `^`. Run `flutter pub get` after any pubspec edit and confirm it resolves.

---

## 4. Project structure

The repo currently contains a default Flutter project with only `main.dart`. Build out to exactly
this shape — feature-first, shallow:

```
lib/
  main.dart                     # runApp, Firebase init, MultiProvider, MaterialApp, theme
  app_router.dart              # named routes + AuthGate widget
  core/
    config.dart                # API base URL, intervals, constants
    api_client.dart            # thin http wrapper: get/post + auth header + error mapping
    api_exception.dart         # ApiException { statusCode, message, code }
    consent_text.dart          # PDPA privacy notice content + version (single source of truth)
    validators.dart            # NIC / phone / password / name validators (single source of truth)
    theme.dart                 # ColorScheme.fromSeed + minor overrides
  models/
    user.dart
    auth_response.dart
    location_ping.dart
    feedback_answer.dart
    flood_alert.dart
  services/
    auth_service.dart          # register / login / logout / token+user persistence
    consent_service.dart       # PDPA consent record: persist per user, sync, withdraw
    location_service.dart      # permissions, background stream, 10-min throttle, manual send, queue
    feedback_service.dart      # submit answer, remember last-submitted time
    alert_service.dart         # FCM token registration, message handling, fetch active alerts
  providers/
    auth_provider.dart         # ChangeNotifier: authStatus, user, login/register/logout
    consent_provider.dart      # ChangeNotifier: gates the app until consent is recorded
    home_provider.dart         # ChangeNotifier: last ping time, tracking state, active alert
  screens/
    splash_screen.dart
    consent_screen.dart        # PDPA notice + explicit opt-in; gates Home
    login_screen.dart
    register_screen.dart
    home_screen.dart
    profile_screen.dart        # name, NIC, phone, permission status, Logout
  widgets/
    primary_button.dart
    app_text_field.dart
    error_banner.dart        # shared inline server-error strip (login + register)
    status_card.dart
test/
  validators_test.dart         # required — NIC + phone edge cases
```

Rules:
- **Screens contain no HTTP calls and no business logic.** They read from providers and call
  provider methods.
- **Providers contain no HTTP calls.** They call services.
- **Services contain no `BuildContext`** and never show UI.
- One public class per file. File names `snake_case`, classes `PascalCase`.

---

## 5. Screens

Five screens. Basic Material 3, seeded from a single blue. No gradients, no custom fonts.

### 5.1 Splash / AuthGate
Reads token from secure storage. Routes to Home if a valid token exists, else Login. Shows a
centered `CircularProgressIndicator` while deciding. No branding animation.

### 5.2 Login
Fields: **NIC**, **Password**. A "Login" button and a "Create an account" text link.
Client-side validation before submit. Show server errors inline above the button or in a SnackBar.
Disable the button and show a spinner inside it while the request is in flight.

### 5.3 Register
Fields, in this order:
1. First Name
2. Last Name
3. NIC
4. Mobile Phone Number
5. Password
6. Confirm Password

Validate all fields with `core/validators.dart` on submit. On success: auto-login and go to Home
(the register endpoint returns a token — see §7).

### 5.4 Home — the main screen
Single scrollable column, top to bottom:

1. **Alert banner** (only when an active alert exists for the user's area) — a colored `Card` with
   the alert severity, title, message, and issued time. Red for `high`, amber for `moderate`.
2. **Location status card** — "Location sharing: Active / Paused / Permission needed",
   the last successful upload time ("Last sent: 3 minutes ago"), and a count of queued pings if
   any are pending.
3. **"Send My Location Now"** — a large, prominent, full-width primary button. This is a safety
   feature; it must always be visible without scrolling on a typical phone. Show clear success
   ("Location sent") or failure feedback.
4. **Feedback card** — the question **"Is there flooding in your area right now?"** with two
   `RadioListTile`s (Yes / No) and a "Submit" button. After submitting, replace the controls with
   "Thanks — your answer was recorded at HH:mm" and a "Change my answer" text button. Users may
   submit again at any time; the server keeps the latest answer per user per time window.
5. If location permission is missing or denied, show an inline warning with a button that opens
   app settings (`Geolocator.openAppSettings()`).

An AppBar with the app name and a person icon leading to Profile.

### 5.5 Profile
Read-only display of first name, last name, NIC, phone. A "Location permission" status row.
A "Logout" button that clears the token, stops tracking, deletes the FCM token server-side, and
returns to Login.

---

## 6. Validation rules — `core/validators.dart`

All validation lives here and is unit-tested. Never inline a regex in a screen.

```dart
// NIC — two accepted Sri Lankan formats:
//   Old: 9 digits followed by V or W (case-insensitive), e.g. 912345678V
//   New: exactly 12 digits,                              e.g. 199112345678
// Normalise before sending: trim, remove spaces, uppercase the trailing letter.
static final _oldNic = RegExp(r'^\d{9}[VWvw]$');
static final _newNic = RegExp(r'^\d{12}$');

// Mobile phone — Sri Lankan mobile, 10 digits starting 07
static final _phone = RegExp(r'^07\d{8}$');
```

- **Password**: minimum 8 characters. That is the only rule — do not add complexity requirements.
- **Confirm password**: must match exactly.
- **First / Last name**: required, 1–50 chars, letters/spaces/hyphens/apostrophes only.
- Error messages must be plain and specific: `"NIC must be 9 digits ending in V or W, or 12 digits"`,
  `"Mobile number must be 10 digits starting with 07"`.
- NIC is the login identifier: always store and transmit it **normalised and uppercased** so that
  `912345678v` and `912345678V` are the same account.

> Note: some older Sri Lankan NICs end in `X`. Only `V`/`W` are accepted per the research
> requirement. If that changes, it changes in this one file only.

---

## 7. API contract

The backend does not exist yet. **This section is the specification** — build the app against it
exactly, and the server will be implemented to match. If reality later differs, update this
section in the same commit as the code change.

A dev-only mock implementing exactly this contract lives in `tools/mock_server/` — run it with
`dart run tools/mock_server/main.dart` to exercise the app before the real server exists. It is
not part of the app and ships with nothing. Keep it in step with this section.

### Base URL and configuration
Never hardcode a URL in a service. In `core/config.dart`:

```dart
class Config {
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8080', // Android emulator -> host machine
  );
  static const Duration locationInterval = Duration(minutes: 10);
  static const Duration requestTimeout = Duration(seconds: 20);
  static const String apiPrefix = '/api/v1';
}
```

Run with: `flutter run --dart-define=API_BASE_URL=https://api.example.lk`

### Auth
JWT bearer token. **Access token only — no refresh token.** Token lifetime is long (server: 30
days). On any `401`, `AuthProvider` clears the session and routes to Login. Do not build refresh
logic.

Header on every authenticated request: `Authorization: Bearer <token>`

### Endpoints

All request and response bodies are JSON; all timestamps are **UTC ISO-8601** strings.

**`POST /api/v1/auth/register`** — public
```json
// request
{ "nic": "912345678V", "firstName": "Nimal", "lastName": "Perera",
  "phone": "0712345678", "password": "secret123" }
// 201 response
{ "token": "eyJ...", "user": { "id": "u_123", "nic": "912345678V",
  "firstName": "Nimal", "lastName": "Perera", "phone": "0712345678" } }
```

**`POST /api/v1/auth/login`** — public
```json
// request
{ "nic": "912345678V", "password": "secret123" }
// 200 response — same shape as register
{ "token": "eyJ...", "user": { ... } }
```

**`POST /api/v1/locations`** — authenticated
```json
// request
{ "latitude": 6.9271, "longitude": 79.8612, "accuracy": 12.5,
  "recordedAt": "2026-08-28T09:15:00Z", "source": "auto" }   // "auto" | "manual"
// 201 response
{ "accepted": true }
```
The app sends **raw coordinates only**. Region/geohash bucketing is entirely server-side.

**`POST /api/v1/feedback`** — authenticated
```json
// request
{ "floodPresent": true, "latitude": 6.9271, "longitude": 79.8612,
  "answeredAt": "2026-08-28T09:15:00Z" }
// 201 response
{ "accepted": true }
```
Include coordinates so the server can bucket the answer without a separate lookup. If location is
unavailable, send `null` for both and let the server fall back to the last known ping.

**`GET /api/v1/alerts/active?latitude=6.9271&longitude=79.8612`** — authenticated
```json
// 200 response — alert is null when there is no active alert
{ "alert": { "id": "a_77", "severity": "high", "title": "Flood risk in your area",
  "message": "Rising water levels reported near Kelani River. Move to higher ground.",
  "issuedAt": "2026-08-28T09:00:00Z" } }
```
`severity` is one of `"low" | "moderate" | "high"`. Called on Home screen load, on pull-to-refresh,
and when a push notification arrives.

**`POST /api/v1/devices/fcm-token`** — authenticated
```json
{ "fcmToken": "fZ8...", "platform": "android" }   // "android" | "ios"
// 200 -> { "accepted": true }
```
Called after login, after register, and on every `onTokenRefresh`.

**`DELETE /api/v1/devices/fcm-token`** — authenticated, called on logout. Body: `{ "fcmToken": "..." }`

**`POST /api/v1/consent`** — authenticated
```json
// request
{ "version": "1.0", "granted": true, "recordedAt": "2026-08-31T09:15:00Z" }
// 200 -> { "accepted": true }
```
Sent when a participant grants consent and again when they withdraw (`granted: false`). The
PDPA requires the controller to be able to *demonstrate* consent was given, so the server
record is the authoritative one — the local copy only gates the client. Store every record
rather than overwriting: a withdrawal does not erase the fact that consent was previously
given, and the audit trail is the point. Failures are retried on next launch and never block
the participant.

### Error envelope
Every non-2xx response:
```json
{ "error": { "code": "INVALID_CREDENTIALS", "message": "NIC or password is incorrect" } }
```
`ApiClient` parses this into `ApiException(statusCode, message)` and the UI shows `message`
directly. If the body is unparseable, use a generic message — never show a raw stack trace or
JSON blob to the user.

Expected codes to handle by name: `INVALID_CREDENTIALS`, `NIC_ALREADY_REGISTERED`,
`VALIDATION_FAILED`, `UNAUTHORIZED`.

---

## 8. Location tracking — the critical path

This is the most important and most fragile part of the app. It is a flood warning system, so a
missed location is a real cost. Treat reliability over elegance here.

### Approach
Use **`geolocator` alone** — do not add a second background-work package.

Start a single long-lived position stream when the user is authenticated:

```dart
Geolocator.getPositionStream(
  locationSettings: AndroidSettings(
    accuracy: LocationAccuracy.high,
    distanceFilter: 0,
    intervalDuration: const Duration(minutes: 1),
    foregroundNotificationConfig: const ForegroundNotificationConfig(
      notificationTitle: 'Flood Alerts LK',
      notificationText: 'Sharing your location for flood alerts',
      enableWakeLock: true,
      setOngoing: true,
    ),
  ),
);
// iOS: AppleSettings(accuracy: high, allowBackgroundLocationUpdates: true,
//                    showBackgroundLocationIndicator: true,
//                    pauseLocationUpdatesAutomatically: false,
//                    activityType: ActivityType.other)
```

Then **throttle uploads in `LocationService`**: keep `_lastUploadedAt` and only POST when
`now - _lastUploadedAt >= Config.locationInterval`. The stream ticks more often than we upload;
that is intentional — it keeps the OS location session alive so a fix is always fresh and ready.

### Manual send
`sendNow()` calls `Geolocator.getCurrentPosition(desiredAccuracy: high, timeLimit: 15s)` and
POSTs immediately with `"source": "manual"`. It **bypasses the throttle** and does not reset it.
It must work even if background permission was denied, as long as while-in-use permission exists.

### Permissions flow
1. `Geolocator.isLocationServiceEnabled()` — if false, prompt the user to enable GPS.
2. `checkPermission()` → `requestPermission()` for while-in-use.
3. Only **after** while-in-use is granted, request always-on (`requestPermission()` again on
   Android returns `always` if the user upgrades; on iOS the OS escalates on its own).
4. Never block the app on a denied permission. Degrade: manual send still offered, warning shown
   on Home. Never show a permission dialog in a loop.

### Failed-upload queue
Network drops during floods. If a POST to `/locations` fails:
- Append the ping (lat, lng, accuracy, recordedAt, source) to a JSON list in `shared_preferences`.
- Cap the queue at **50** entries, dropping the oldest.
- On the next successful upload attempt, flush the queue oldest-first before sending the new ping.
- Surface the pending count on the Home status card.
- **Only queue what is worth retrying**: network failures and `5xx`. A `401` means the session is
  already gone, and a non-401 `4xx` means the server rejected that payload and always will —
  queueing either just fills the 50 slots with pings that can never land. For the same reason, a
  queued ping the server permanently rejects is dropped mid-flush rather than halting it, so one
  bad entry cannot wedge the queue forever.
- A failed automatic ping reopens the throttle, so the next tick retries instead of an outage
  costing a further ten minutes of silence.
- Clear the queue on logout. It is keyed to the device, not the user, so leaving it would upload
  one user's coordinates under the next user's token.

Do not build retry backoff timers, isolates, or a sync engine. The next tick is the retry.

### Lifecycle
- Start tracking on login/app-start-while-authenticated; stop on logout.
- Never start tracking when unauthenticated.
- Log ping outcomes with `debugPrint` in debug only. **Never log coordinates, NIC, phone, or
  tokens in release builds.**

---

## 9. Push notifications

Firebase Cloud Messaging. The server sends to device tokens; the app only receives.

- `Firebase.initializeApp()` in `main()` before `runApp`.
- Request notification permission (`FirebaseMessaging.instance.requestPermission()`) after
  successful login, not at first launch.
- Register the FCM token with the backend after login/register and on `onTokenRefresh`.
- **Background/terminated**: the OS displays the notification from the server's `notification`
  payload. Keep the background handler a top-level function annotated `@pragma('vm:entry-point')`
  and keep it trivial — no HTTP calls in it.
- **Foreground**: `onMessage` → display via `flutter_local_notifications` (one channel:
  `flood_alerts`, importance `max`) **and** refresh the Home alert banner.
- **Tap handling**: `onMessageOpenedApp` and `getInitialMessage()` → navigate to Home and refresh
  the active alert. No deep-link routing beyond that.

Expected data payload from the server: `{ "type": "flood_alert", "alertId": "a_77", "severity": "high" }`

Setup files are required and are **not** in the repo:
`android/app/google-services.json` and `ios/Runner/GoogleService-Info.plist`. If they are missing,
say so and stop rather than guessing — the app will not build without them.

---

## 10. Platform configuration

### Android (`android/app/src/main/AndroidManifest.xml`)
```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>
<uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>
<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_LOCATION"/>
<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
<uses-permission android:name="android.permission.WAKE_LOCK"/>
```
- `minSdkVersion 24`, `compileSdkVersion` / `targetSdkVersion 36` — these track the Flutter
  3.47 defaults via `flutter.minSdkVersion` etc. The 23/34 originally specified here predate
  this toolchain: Flutter 3.47 no longer supports API 23, and the plugin set needs 36.
- `coreLibraryDesugaringEnabled` is on: `flutter_local_notifications` needs it.
- `android/app/build.gradle.kts`: the `com.google.gms.google-services` plugin and its
  `settings.gradle.kts` declaration are present but **commented out**, because applying it
  without `google-services.json` fails the build. Uncomment both once the file is in place.

### iOS (`ios/Runner/Info.plist`)
```xml
<key>NSLocationWhenInUseUsageDescription</key>
<string>Flood Alerts LK uses your location to send you flood alerts for your area.</string>
<key>NSLocationAlwaysAndWhenInUseUsageDescription</key>
<string>Flood Alerts LK shares your location every 10 minutes, even in the background, so we can warn you about flooding near you.</string>
<key>UIBackgroundModes</key>
<array>
  <string>location</string>
  <string>remote-notification</string>
</array>
```
- iOS deployment target **13.0** minimum (Firebase requirement).
- Xcode → Signing & Capabilities: enable **Background Modes → Location updates** and
  **Remote notifications**, plus **Push Notifications**.
- Purpose strings must honestly describe background use or App Store review will reject the app.

---

## 11. Security

- Auth token: `flutter_secure_storage` **only**. Never `shared_preferences`.
- Never log or persist the password. Clear controllers on dispose.
- Never print the token, NIC, phone number, or coordinates in a release build.
- All non-local traffic over HTTPS. Do not disable certificate validation, ever, including "just
  for testing" — use `--dart-define` to point at a local HTTP dev server instead.
- On `401`: clear secure storage, stop location tracking, route to Login.
- Do not add certificate pinning, biometric login, or encryption-at-rest for cached data.

---

## 12. Flutter skills — when to use them

Flutter skills are installed in this environment (the Flutter team's `flutter/skills` plugin plus a
broader marketplace collection). **Use them.** Before implementing a Flutter mechanic you would
otherwise guess at — layout constraints, widget testing, `http` usage, form validation, theming,
Xcode/CocoaPods setup — check for a relevant skill and read it first. It is faster and more correct
than reasoning from memory about current Flutter APIs.

Two practical notes:
- The collection contains **two naming families** for the same topics: task-style
  (`flutter-setup-declarative-routing`) and topic-style (`flutter-routing-and-navigation`). Use
  whichever is actually installed. If unsure what is available, list the skills rather than
  guessing at a name.
- Skills are **advisory**. §2 and §4 of this file define the shape of this app. Where a skill
  suggests more layers, more packages, or more files than this project calls for, follow this file.

### Skill map for this project

| When you are… | Use | Scope it to |
|---|---|---|
| Building model classes | `flutter-implement-json-serialization` | Fully aligned with our rules — hand-written `fromJson`/`toJson`, no codegen |
| Writing API calls | `flutter-use-http-package` / `flutter-http-and-json` | Applies inside `core/api_client.dart` only; services must not call `http` directly |
| Building login/register forms | `flutter-building-forms` / `flutter-form` | `Form` + `TextFormField` + our `core/validators.dart` |
| Wiring Provider state | `flutter-managing-state` / `flutter-state-management` | `ChangeNotifier` + `provider` only |
| Laying out a screen | `flutter-building-layouts` / `flutter-layout` | Phone portrait only |
| Debugging a RenderFlex overflow or unbounded-constraint error | `flutter-fix-layout-issues` | Ideal use — reach for this immediately on any layout exception |
| Setting up the theme | `flutter-theming-apps` / `flutter-theming` | One `ColorScheme.fromSeed` seed color, nothing more |
| Writing the validator tests | `flutter-add-widget-test` | Validator unit tests are required (see §14 → Testing); widget tests only if asked |
| Fixing an Xcode / CocoaPods / Android SDK problem | `flutter-environment-setup-macos` / `flutter-setting-up-on-macos` | Developer is on a MacBook Pro |
| Doing a final polish pass | `flutter-improving-accessibility` / `flutter-accessibility-audit` | One pass at the end: semantic labels on the manual-send button and radio options, 48dp tap targets, contrast on the alert banner. Worth doing — do not turn it into a project. |
| Chasing real jank or battery drain from the location stream | `flutter-performance` | Only in response to a measured problem |
| Responsive / adaptive layout | `flutter-build-responsive-layout` | Only enough to avoid overflow on small screens. Do **not** build breakpoint systems or tablet layouts. |

### Skills to skip entirely on this project

`flutter-setup-localization` / `flutter-localizing-apps` (English only) ·
`flutter-working-with-databases` / `flutter-caching-data` (no local DB — `shared_preferences` only) ·
`flutter-handling-concurrency` (no isolates) ·
`flutter-interoperating-with-native-apis`, `flutter-building-plugins`,
`flutter-embedding-native-views` (`geolocator` and `firebase_messaging` handle all platform code) ·
`flutter-animating-apps` · `flutter-reducing-app-size` · `flutter-adding-home-screen-widgets` ·
`flutter-add-integration-test` (unless explicitly asked) ·
`flutter-add-widget-preview` (optional, dev-only convenience).

### Explicit overrides

**1. `flutter-apply-architecture-best-practices` / `flutter-architecture` — read the principle,
ignore the topology.**
That skill prescribes `lib/data/` + `lib/domain/` + `lib/ui/features/…`, a mandatory
Service→Repository split, use-case classes, and suggests `freezed` and `get_it`. That is correct
for a large app and wrong for a 5-screen research client. Keep §4's structure and map its concepts
onto ours:

| Skill's concept | This project |
|---|---|
| UI layer | `screens/` + `widgets/` |
| ViewModel | `providers/` (`ChangeNotifier`) |
| Repository + Service | collapsed into a single `services/` class per concern |
| Domain layer / use cases | none |
| `freezed`, `get_it`, `Result<T>` wrappers | not used — plain classes, `MultiProvider`, thrown `ApiException` |

Take from it: UI never touches HTTP, services never touch `BuildContext`, one concern per class.
Do not take from it: the folder tree, the extra layers, or the extra packages.

**2. `flutter-setup-declarative-routing` / `flutter-routing-and-navigation` — do not apply.**
That skill installs `go_router` and `MaterialApp.router`. This app has five screens, no web target,
and no deep links beyond "open Home and refresh the alert." Use plain `MaterialApp` named routes in
`app_router.dart` with `Navigator.pushNamed` / `pushReplacementNamed`, and a `navigatorKey` for
navigation from the notification-tap handler. `go_router` stays on the forbidden list in §2.

**3. Any skill that recommends a package not listed in §3 — ask before adding it.**

---

## 13. Version control

The repo is a git repository with the default Flutter project already committed. It is
**local-only for now** — there is no remote, and a single developer works on it.

### Claude Code's git permissions

| Action | Allowed? |
|---|---|
| `git status`, `git diff`, `git log`, `git branch` | Yes, freely — read the state before changing it |
| `git checkout -b` a new task branch | Yes |
| `git add` (specific paths) + `git commit` | **Yes, freely** — commit each completed unit of work |
| `git merge` a task branch into `main` | Only when asked |
| `git push` | **Never.** There is no remote; do not add one. |
| `git rebase`, `commit --amend`, `reset --hard`, `push --force`, `git clean` | **Never without being asked.** These destroy work. |
| `git checkout .` / discarding uncommitted changes | **Never.** Those changes may be the developer's, not yours. |

If the working tree is dirty with changes you did not make, **stop and ask** before committing —
do not sweep someone else's work into your commit.

### Branching

`main` is the only long-lived branch and must always analyze clean, pass tests, and build.

Create one short-lived branch per task, named `<type>/<short-kebab-description>`:

```
feat/auth-login-register
feat/location-background-stream
feat/feedback-card
fix/home-overflow-small-screen
chore/android-permissions
```

Types: `feat`, `fix`, `chore`, `test`, `docs`. The build order in §14 is a reasonable branch plan —
roughly one branch per numbered step. Delete a branch after merging it.

### Commits

**One commit per logical unit of work, not per file and not per session.** A commit should leave
the app in a state that analyzes clean and passes tests — never commit a knowingly broken tree.

Message format — a light convention, not enforced tooling:

```
<type>: <imperative summary, <=72 chars, no trailing period>

<optional body: why, not what. Wrap at 72. Note any CLAUDE.md section
this changes or any deviation from the spec.>
```

Examples:

```
feat: add NIC and phone validators with unit tests

chore: add background location permissions to AndroidManifest

feat: throttle location uploads to 10 minutes

The geolocator stream ticks every minute to keep the OS location
session warm; LocationService gates the actual POST. See CLAUDE.md §8.
```

Rules:
- **Stage intentionally.** Use explicit paths. Do not use `git add -A` or `git add .` — that is how
  build output, `.DS_Store`, and Firebase config files end up in history.
- Never mention Claude, AI, or this file's guidance in a commit message.
- If a change alters the API contract, the `pubspec.yaml` dependency list, or any spec section,
  **update CLAUDE.md in the same commit** as the code.
- Run `flutter analyze`, `dart format`, and `flutter test` **before** committing, not after.

### `.gitignore` — audit this before the next commit

The default `flutter create` `.gitignore` is a good start but is not sufficient here. Verify these
entries exist and add any that are missing:

```gitignore
# Flutter / Dart
build/
.dart_tool/
.flutter-plugins
.flutter-plugins-dependencies
.packages

# Android
android/local.properties
android/.gradle/
android/app/google-services.json
*.jks
*.keystore
android/key.properties

# iOS
ios/Pods/
ios/.symlinks/
ios/Flutter/Flutter.framework
ios/Flutter/Flutter.podspec
ios/Flutter/flutter_export_environment.sh
ios/Runner/GoogleService-Info.plist

# Editors / OS
.idea/
*.iml
.vscode/
.DS_Store
```

**Do** commit `pubspec.lock` (this is an application, not a package) and `ios/Podfile.lock`.

**Never commit**, under any circumstances: `google-services.json`,
`GoogleService-Info.plist`, any `.jks`/`.keystore`/`key.properties`, a real API base URL with
credentials, or any file containing a token. The Firebase config files are gitignored because this
repo may be published alongside the research paper; keep a copy outside the repo and re-download
from the Firebase console if lost.

**One-time check** — the default project was already committed, so verify nothing unwanted got in:

```bash
git ls-files | grep -Ei 'build/|\.dart_tool|Pods/|local\.properties|google-services|GoogleService-Info|\.jks|\.keystore|\.DS_Store'
```

If that prints anything, remove it from tracking without deleting the local file, then commit:

```bash
git rm -r --cached <path>
```

Flag this to the developer rather than fixing it silently — a file already in history needs more
than `git rm --cached` if it was ever a secret.

---

## 14. Working style for Claude Code

### Commands
```bash
flutter pub get
flutter analyze                 # must be clean — zero warnings — before you say a task is done
dart format lib test
flutter test
flutter run                     # add --dart-define=API_BASE_URL=... when hitting a real server
flutter build apk --debug       # Android build check
flutter build ios --no-codesign # iOS build check (macOS; run when iOS config changed)
```

The developer is on a **MacBook Pro**, so both Android and iOS can be built and tested locally.
When platform config changes, verify both.

### Definition of done for any task
1. `flutter analyze` reports no issues.
2. `dart format` applied.
3. `flutter test` passes.
4. The app still builds for Android; also for iOS if iOS files were touched.
5. No new dependency added without asking.
6. The work is committed on its task branch with a clear message (§13).

### Testing
Minimal and targeted. Write unit tests for `core/validators.dart` (all NIC and phone edge cases:
9 digits without a letter, lowercase v, 12 digits, 11 digits, letters in the number, empty). Do not
write widget or integration tests unless asked.

### Build order — suggested
1. `pubspec.yaml`, `core/config.dart`, `core/theme.dart`, `core/validators.dart` + its test
2. `models/`, `core/api_client.dart`, `core/api_exception.dart`
3. `services/auth_service.dart`, `providers/auth_provider.dart`
4. `main.dart`, `app_router.dart`, splash / login / register — get auth working end to end
5. `services/location_service.dart` + Home screen with manual send
6. Background stream, throttle, and the failed-upload queue
7. Feedback card + `feedback_service.dart`
8. Firebase + `alert_service.dart` + alert banner
9. Profile screen, logout wiring
10. Platform config, permission strings, final analyze/format/build pass

### Communication rules
- If a requirement here is ambiguous or conflicts with the code, **ask** — do not invent a
  requirement or silently pick a direction.
- If an installed Flutter skill conflicts with this file, follow this file and **say so in one
  line** ("skipping the go_router setup from the routing skill per CLAUDE.md §12") so the
  divergence is visible rather than silent.
- If an endpoint's shape needs to change, update §7 in the same change.
- Do not create README files, docs, or comment blocks unless asked. Comment only genuinely
  non-obvious code (the throttle-vs-stream-interval trick in §8 deserves a comment; a getter
  does not).
- Commit completed work as you go (§13). Never push — there is no remote.

---

## 15. Open items

Flag these to the developer rather than guessing:
- `google-services.json` / `GoogleService-Info.plist` are not yet in the repo, and are gitignored
  by design (§13) — they must be placed manually on each machine.
- The production API base URL is not yet known — use `--dart-define` in the meantime.
- No git remote yet. Research code with no offsite backup is a real risk; suggest adding a private
  remote once there is working code worth losing.
- Server-side region size / geohash precision for the 75% aggregation rule is a backend decision;
  the app is intentionally unaware of it.
- **Android builds and is verified**: `flutter build apk --debug` succeeds (SDK at
  `~/Library/Android/sdk`, platform 36/37, build-tools 36.0.0). The built manifest carries the
  correct `lk.floodwatch.app` id and all of §10's permissions.
- **iOS builds and is verified**: `flutter build ios --no-codesign` succeeds. The built
  `Runner.app/Info.plist` carries the `lk.floodwatch.app` id, both location purpose strings,
  `UIBackgroundModes` = location + remote-notification, and the `NSAllowsLocalNetworking` ATS
  entry. Deployment target is 15.0 (above §10's stated 13.0 floor).
- **CocoaPods lives at `/opt/homebrew/bin/pod` but is not on the default PATH**, so
  `flutter doctor` reports it as missing and iOS builds fail until you prefix
  `export PATH="/opt/homebrew/bin:$PATH"`. Worth adding to your shell profile. Note most plugins
  now resolve through Swift Package Manager, not CocoaPods — only `flutter_local_notifications`
  and `flutter_secure_storage` still come from pods, which is why `Podfile.lock` looks sparse.
- Push notifications are wired in Dart but **inert**: `Firebase.initializeApp()` is guarded, so a
  missing `google-services.json` disables push rather than blocking launch. The
  `com.google.gms.google-services` plugin is commented out in both gradle files — uncomment it
  together with adding the file. Alerts still work meanwhile via `GET /alerts/active`.
