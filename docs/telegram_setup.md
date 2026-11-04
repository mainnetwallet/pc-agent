# Telegram Setup Guide

## 1. Create the bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts to name your bot.
3. BotFather gives you a bot token, e.g. `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`.

## 2. Get your numeric user ID

1. Message **@userinfobot** (or any ID bot) in Telegram.
2. It replies with your numeric user ID, e.g. `123456789`.

## 3. Configure the agent

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
OWNER_TELEGRAM_IDS=123456789
TELEGRAM_MODE=polling
```

Only IDs in `OWNER_TELEGRAM_IDS` may control the PC. Everyone else gets
"Unauthorized."

## 4. Run

Standalone (local polling):

```powershell
python agent/main.py
```

The bot starts long-polling. Send it instructions.

## 5. Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/status` | PC and agent status |
| `/tasks` | List recent tasks |
| `/screen` | Send current desktop screenshot |
| `/live N` | Periodic screenshot every N seconds |
| `/stoplive` | Stop live updates |
| `/record N` | Record screen for N seconds and send video |
| `/stoprecord` | Stop recording |
| `/stop <task>` | Cancel a task |
| `/pause <task>` | Pause a task |
| `/resume <task>` | Resume a paused task |
| `/schedule <secs> <instruction>` | Schedule a task |
| `/help` | Help |

Send any plain text as a task instruction (English, Bengali, or Banglish).

## Security

* Telegram numeric user-ID allowlisting — unauthorized accounts cannot control the computer.
* Only one polling or webhook mode active per bot token at a time.

## Troubleshooting

* Bot doesn't respond — check `TELEGRAM_BOT_TOKEN` and that your user ID is in `OWNER_TELEGRAM_IDS`.
* "Unauthorized" — your ID isn't allowlisted.
* Webhook conflicts — ensure only one polling/webhook instance runs per token.
