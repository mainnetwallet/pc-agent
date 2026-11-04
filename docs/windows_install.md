# Windows Installation Guide — Autonomous PC Agent

## 1. Prerequisites

* Windows 10 or Windows 11 (64-bit).
* Python 3.10+ (3.11 recommended): https://www.python.org/downloads/
  * During install, check **"Add Python to PATH"**.
* Node.js 18+ and npm (only for the web dashboard / Android app):
  https://nodejs.org/
* (Optional) Tesseract OCR for OCR features:
  https://github.com/UB-Mannheim/tesseract/wiki
  * After install, note `tesseract.exe` path (e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`).
  * Set `TESSERACT_CMD` in `.env` if not on PATH.

## 2. Get the code

```powershell
# The repository is already at C:\Users\mainw\PC AGENTS
cd "C:\Users\mainw\PC AGENTS"
```

## 3. Create a virtual environment and install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configure

```powershell
copy .env.example .env
notepad .env
```

Edit `.env` and set at minimum:

* `TELEGRAM_BOT_TOKEN` — your Telegram bot token (see Telegram Setup Guide).
* `OWNER_TELEGRAM_IDS` — comma-separated numeric Telegram user IDs allowed to control the PC.
* `RELAY_URL` — leave empty for standalone mode (no cloud server).

## 5. Run the agent (standalone mode)

```powershell
.\.venv\Scripts\Activate.ps1
python agent/main.py
```

The agent starts:
* A persistent task queue worker.
* Telegram long-polling bot (if token configured).
* Screen capture (on demand via `/screen`, `/live`, `/record`).

You can now send tasks to the Telegram bot, and they will execute on this PC.

## 6. Run as a background service (auto-start at login)

Create a scheduled task or shortcut in the Startup folder:

```
shell:startup
```

Add a shortcut that runs:

```
C:\Users\mainw\PC AGENTS\.venv\Scripts\python.exe C:\Users\mainw\PC AGENTS\agent\main.py
```

The system tray icon (if `pystray` is available) provides Pause / Stop /
Emergency Stop / Exit.

## 7. (Optional) Run the FastAPI relay server for remote access

```powershell
cd server
.\..\.venv\Scripts\Activate.ps1
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then set `RELAY_URL=https://your-server` on the agent and restart.

## 8. (Optional) Web dashboard

```powershell
cd web
npm install
npm run dev   # http://localhost:5173
```

Pair the dashboard device via the "Pair Device" step using a token from
`POST /devices/pair`.

## 9. (Optional) Android app

```powershell
cd mobile
npm install
npx expo start --android
```

## Troubleshooting

* `ModuleNotFoundError: No module named 'config'` — run from the repo root
  or ensure `agent/main.py` is the entry point (it adds `agent/` to `sys.path`).
* Tesseract not found — install Tesseract OCR or OCR simply returns empty.
* Screenshot black / active window None — Windows may be locked; the agent
  cannot interact with the lock screen by design.
