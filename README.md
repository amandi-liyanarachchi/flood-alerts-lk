# Flood Alerts LK

Guide to run the system

## Terminal 1 — backend:
```bash
cd flood-alerts-lk/floodwatch-backend
./run_local.sh --reset
```

## Terminal 2 — Check system status:
```bash
cd /Users/thisura/Developer/Ama/floodwatch-backend
.venv/bin/python -m scripts.preflight
```

## Browser — admin dashboard:
```bash
http://localhost:8000
```
Enter admin token demo-admin and your name.

## Emulator + app:
```bash
# boot the emulator first if it isn't already running
flutter emulators
flutter emulators --launch <emulator_id>
cd flood-alerts-lk/flood_alerts_app
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```
Log in: NIC 999000000V, password demo1234. 
Accept the PDPA consent screen.

Keep the laptop’s internet connection on
