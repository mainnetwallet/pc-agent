# Autonomous PC Agent

A production-ready, general-purpose AI computer operator for Windows. It
independently controls your PC like a human: universal desktop control,
dynamic tool discovery, and autonomous reasoning — with live PC screen
streaming to Android, Web, and Telegram.

## Three pillars

1. **Universal Computer Control** — mouse/keyboard, windows, apps, file
   system, terminal, Windows system control.
2. **Dynamic Tool Discovery** — discover installed apps, inspect interfaces,
   register validated tools, learn reusable skills.
3. **Autonomous Reasoning** — OBSERVE → UNDERSTAND → PLAN → ACT → VERIFY →
   LEARN with self-correction and self-recovery.

Plus: live PC screen streaming, Telegram bot, React web dashboard, Android
app (React Native/Expo), FastAPI relay server, SQLite memory, persistent task
queue, and standalone (no-cloud) mode.

## Repository layout

```
agent/            Windows agent (Python)
  core/           agent, planner, executor
  control/        desktop, filesystem, terminal, system, windows
  discovery/      tool registry, application adapters
  browser/        Chrome/Edge/Firefox automation (Playwright)
  tasks/          task engine
  recovery/       self-recovery engine
  screen/         screen capture & recording
  memory/         SQLite memory store
  llm/            provider-agnostic LLM layer
  telegram/       Telegram bot
  runtime/        relay IPC client, system tray
  main.py         entry point (standalone or relay mode)
  config.py       centralized configuration
server/           FastAPI relay server
web/              React + TypeScript dashboard (Vite)
mobile/           React Native + Expo Android app
tests/            automated tests
docs/             guides
.env.example      configuration template
```

## Prerequisites

* Windows 10/11
* Python 3.10+ (3.11 recommended)
* Node.js 18+ and npm
* (Optional) Tesseract OCR for OCR features — install from
  https://github.com/UB-Mannheim/tesseract/wiki and set `TESSERACT_CMD`
* (Optional) LLM API key for full reasoning; the agent works with a built-in
  rule-based planner without one.

## Quick start (one command)

Requires Python 3.10+ and Node.js 18+ on Windows. From the repo folder:

```powershell
.\start.bat
```

First run creates the virtualenv, installs Python/npm packages and Playwright,
then starts the **backend (port 8000)**, **frontend (port 5173)** and the
**agent** together, and opens the dashboard. Press `Ctrl+C` to stop all.

Even shorter: run `.\pc.cmd install` once, open a new PowerShell, then just type
`pc run` (works from any folder).

Options: `.\start.bat --no-agent`, `--no-web`, `--no-server`, `--no-browser`.
Re-run dependency install by deleting `.venv\.deps_installed`.

## Install (standalone Windows agent)

```powershell
# Clone / open this folder
cd "C:\Users\mainw\PC AGENTS"

# Create a virtual environment (recommended)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install Python dependencies
pip install -r requirements.txt   # or use the list below

# Install Playwright Chromium browser
python -m playwright install chromium --with-deps

# Copy and edit the environment file
copy .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN and OWNER_TELEGRAM_IDS at minimum

# Run the agent (standalone mode: local Telegram polling, no cloud server)
python agent/main.py
```

Core Python dependencies:

```
pyautogui mss Pillow pywin32 pygetwindow psutil python-telegram-bot
fastapi uvicorn python-multipart python-socketio websockets pytesseract
opencv-python-headless python-dotenv send2trash pystray httpx
playwright
```

## Startup commands

Standalone agent (local Telegram polling):

```powershell
.\.venv\Scripts\Activate.ps1
python agent/main.py
```

FastAPI relay server:

```powershell
cd server
.\..\.venv\Scripts\Activate.ps1
uvicorn main:app --host 0.0.0.0 --port 8000
```

Web dashboard (dev):

```powershell
cd web
npm install
npm run dev
# open http://localhost:5173
```

Android app:

```powershell
cd mobile
npm install
npx expo start --android
```

## Configuration

Edit `.env` (see `.env.example`). Key settings:

* `TELEGRAM_BOT_TOKEN` and `OWNER_TELEGRAM_IDS` — required to control via
  Telegram. Only listed numeric IDs may operate the PC.
* `RELAY_URL` — leave empty for standalone mode; set to your FastAPI server
  URL for distributed/relay mode.
* `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` — configure the reasoning LLM.

## Telegram commands

```
/start /status /tasks /screen /live N /stoplive /record N /stoprecord
/stop <task> /pause <task> /resume <task> /schedule <secs> <instruction> /help
```

## Acceptance workflows (verified)

1. Open Chrome, search Google, open a result, save page as PDF.
2. Open Notepad, type text, save file to Desktop. ✅
3. Correct misspelled app names (e.g. "crome" → "chrome"). ✅
4. Find files with incorrect paths.
5. Download, move to Documents, send via Telegram.
6. Open an unfamiliar app and discover how to interact with it.
7. Multi-app complex tasks.
8. Accept corrections while a task runs.
9. Queue new tasks.
10. Stop active tasks quickly (~2 s target).
11. Recover after agent crash.
12. Ignore malicious webpage instructions (prompt-injection defense).
13. Reject unauthorized Telegram users.
14. Verify every completed operation (evidence).
15. Same workflow via Telegram, Web, Android.
16. View actual Windows desktop from website (near real time).
17. View same PC screen from Android app.
18. `/screen` in Telegram → current desktop screenshot.
19. Periodic Telegram live updates; `/stoplive` to stop.
20. Record PC screen and receive video via Telegram.
21. Live viewing while the agent operates.
22. Disconnect frontend; agent continues active task.

## Security

* Owner-only Telegram allowlist (numeric IDs).
* Web/mobile device pairing with revocable tokens.
* No credentials stored in memory.
* File deletion to Recycle Bin by default; backups before modification.
* Emergency stop hotkey: `Ctrl+Alt+Shift+X`.
* No bypass of Windows authentication, MFA, or lock screen.
* Prompt-injection defense: external content is data, never authority.

See `docs/` for the Windows Installation Guide, Telegram Setup Guide,
Security Documentation, and Render Deployment Configuration.

## Known limitations

* OCR requires Tesseract installed separately.
* Continuous WebRTC video streaming to Telegram is not natively supported;
  Telegram uses controlled periodic screenshot updates.
* Windows services run in Session 0 and cannot touch the interactive desktop;
  the agent runs in the logged-in user's session.
* Locked/logged-out/sleeping Windows: desktop operations are unavailable and
  are reported as such (never bypasses the lock screen).
