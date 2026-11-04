"""Centralized configuration for the Autonomous PC Agent.

Loads settings from environment variables and a .env file when present.
All configuration lives here; other modules import from this module.
"""

import os
from dotenv import load_dotenv

# Load .env if it exists (no error when missing)
load_dotenv()


def _env(name, default):
    return os.getenv(name, default)


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Identity / security
# ---------------------------------------------------------------------------
AGENT_NAME = _env("AGENT_NAME", "PC Agent")
AGENT_ID = _env("AGENT_ID", os.getenv("COMPUTERNAME", "pc-agent"))

# Telegram owner allowlist (numeric user IDs). Only these IDs may control the PC.
# Comma-separated list of integer IDs.
_RAW_OWNER_IDS = _env("OWNER_TELEGRAM_IDS", "")
OWNER_TELEGRAM_IDS = set()
for _part in _RAW_OWNER_IDS.split(","):
    _part = _part.strip()
    if _part:
        try:
            OWNER_TELEGRAM_IDS.add(int(_part))
        except ValueError:
            pass

TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_MODE = _env("TELEGRAM_MODE", "polling")  # polling | webhook
TELEGRAM_WEBHOOK_URL = _env("TELEGRAM_WEBHOOK_URL", "")
TELEGRAM_WEBHOOK_PORT = _env_int("TELEGRAM_WEBHOOK_PORT", 8443)

# ---------------------------------------------------------------------------
# Relay / server (standalone mode when RELAY_URL is empty)
# ---------------------------------------------------------------------------
RELAY_URL = _env("RELAY_URL", "")  # empty => run standalone (local Telegram polling)
RELAY_API_KEY = _env("RELAY_API_KEY", "")
RELAY_HEARTBEAT_INTERVAL = _env_int("RELAY_HEARTBEAT_INTERVAL", 30)
RELAY_RECONNECT_INTERVAL = _env_int("RELAY_RECONNECT_INTERVAL", 5)

# ---------------------------------------------------------------------------
# LLM brain (provider-agnostic)
# ---------------------------------------------------------------------------
LLM_PROVIDER = _env("LLM_PROVIDER", "openai")  # openai | anthropic | custom | mock
LLM_API_KEY = _env("LLM_API_KEY", "")
LLM_BASE_URL = _env("LLM_BASE_URL", "")
LLM_MODEL = _env("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.2") or "0.2")
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 2000)
LLM_ENABLE_VISION = _env_bool("LLM_ENABLE_VISION", True)

# ---------------------------------------------------------------------------
# Task engine
# ---------------------------------------------------------------------------
TASK_DB_PATH = _env("TASK_DB_PATH", "data/agent.db")
TASK_EXECUTION_TIMEOUT = _env_int("TASK_EXECUTION_TIMEOUT", 3600)  # seconds
STUCK_STATE_THRESHOLD = _env_int("STUCK_STATE_THRESHOLD", 120)  # seconds without progress
MAX_RETRIES = _env_int("MAX_RETRIES", 3)
RETRY_BASE_BACKOFF = _env_int("RETRY_BASE_BACKOFF", 2)  # seconds

# ---------------------------------------------------------------------------
# Reasoning loop (multi-call LLM agent)
# ---------------------------------------------------------------------------
AGENT_MAX_ITERATIONS = _env_int("AGENT_MAX_ITERATIONS", 25)  # LLM turns per task
AGENT_MAX_ACTIONS_PER_TURN = _env_int("AGENT_MAX_ACTIONS_PER_TURN", 3)
AGENT_HISTORY_WINDOW = _env_int("AGENT_HISTORY_WINDOW", 8)  # recent actions shown in detail
AGENT_OBSERVE_OCR = _env_bool("AGENT_OBSERVE_OCR", True)  # add OCR text to each observation

# ---------------------------------------------------------------------------
# Desktop control
# ---------------------------------------------------------------------------
DESKTOP_INPUT_LOCK = _env_bool("DESKTOP_INPUT_LOCK", True)
SCREENSHOT_QUALITY = _env_int("SCREENSHOT_QUALITY", 70)
SCREENSHOT_MAX_DIMENSION = _env_int("SCREENSHOT_MAX_DIMENSION", 1920)
PYAUTOGUI_PAUSE = float(_env("PYAUTOGUI_PAUSE", "0.1") or "0.1")
PYAUTOGUI_FAILSAFE = _env_bool("PYAUTOGUI_FAILSAFE", True)

# ---------------------------------------------------------------------------
# Screen capture / streaming
# ---------------------------------------------------------------------------
SCREEN_CAPTURE_FPS = _env_int("SCREEN_CAPTURE_FPS", 5)
SCREEN_CAPTURE_QUALITY = _env_int("SCREEN_CAPTURE_QUALITY", 60)
SCREEN_CAPTURE_MONITOR = _env_int("SCREEN_CAPTURE_MONITOR", 0)  # 0 = all/primary
SCREEN_CAPTURE_ENABLED = _env_bool("SCREEN_CAPTURE_ENABLED", True)
STREAMING_INDICATOR = _env_bool("STREAMING_INDICATOR", True)

# ---------------------------------------------------------------------------
# Filesystem safety
# ---------------------------------------------------------------------------
FILE_DELETE_TO_RECYCLE_BIN = _env_bool("FILE_DELETE_TO_RECYCLE_BIN", True)
BACKUP_BEFORE_MODIFY = _env_bool("BACKUP_BEFORE_MODIFY", True)
MAX_RECURSIVE_DEPTH = _env_int("MAX_RECURSIVE_DEPTH", 10)

# ---------------------------------------------------------------------------
# Windows runtime
# ---------------------------------------------------------------------------
AUTO_STARTUP = _env_bool("AUTO_STARTUP", True)
EMERGENCY_STOP_HOTKEY = _env("EMERGENCY_STOP_HOTKEY", "ctrl+alt+shift+x")
WATCHDOG_ENABLED = _env_bool("WATCHDOG_ENABLED", True)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = _env("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOGS_DIR = _env("LOGS_DIR", os.path.join(BASE_DIR, "logs"))
RECORDINGS_DIR = _env("RECORDINGS_DIR", os.path.join(DATA_DIR, "recordings"))
SCREENSHOTS_DIR = _env("SCREENSHOTS_DIR", os.path.join(DATA_DIR, "screenshots"))

for _d in (DATA_DIR, LOGS_DIR, RECORDINGS_DIR, SCREENSHOTS_DIR):
    os.makedirs(_d, exist_ok=True)

__all__ = [
    "AGENT_NAME", "AGENT_ID", "OWNER_TELEGRAM_IDS", "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_MODE", "TELEGRAM_WEBHOOK_URL", "TELEGRAM_WEBHOOK_PORT",
    "RELAY_URL", "RELAY_API_KEY", "RELAY_HEARTBEAT_INTERVAL",
    "RELAY_RECONNECT_INTERVAL", "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL",
    "LLM_MODEL", "LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_ENABLE_VISION",
    "TASK_DB_PATH", "TASK_EXECUTION_TIMEOUT", "STUCK_STATE_THRESHOLD",
    "MAX_RETRIES", "RETRY_BASE_BACKOFF", "AGENT_MAX_ITERATIONS",
    "AGENT_MAX_ACTIONS_PER_TURN", "AGENT_HISTORY_WINDOW", "AGENT_OBSERVE_OCR",
    "DESKTOP_INPUT_LOCK",
    "SCREENSHOT_QUALITY", "SCREENSHOT_MAX_DIMENSION", "PYAUTOGUI_PAUSE",
    "PYAUTOGUI_FAILSAFE", "SCREEN_CAPTURE_FPS", "SCREEN_CAPTURE_QUALITY",
    "SCREEN_CAPTURE_MONITOR", "SCREEN_CAPTURE_ENABLED", "STREAMING_INDICATOR",
    "FILE_DELETE_TO_RECYCLE_BIN", "BACKUP_BEFORE_MODIFY", "MAX_RECURSIVE_DEPTH",
    "AUTO_STARTUP", "EMERGENCY_STOP_HOTKEY", "WATCHDOG_ENABLED",
    "BASE_DIR", "DATA_DIR", "LOGS_DIR", "RECORDINGS_DIR", "SCREENSHOTS_DIR",
]
