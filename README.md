# Telegram Self Automation Panel — Railway

A Persian Telegram bot panel that manages user subscriptions and optional Telethon sessions.

## Features

- Mandatory channel membership gate with a single «بررسی عضویت» button.
- Main menu with support, subscription, self setup/settings.
- Phone-number share button, login code flow and 2FA password flow through Telethon.
- Separate `.session` file per Telegram numeric user ID.
- Separate SQLite database for subscriptions, settings, selected groups and payments.
- Subscription purchase: 30/60/90 days, receipt upload, admin approve/reject.
- After activation, self panel:
  - group list and multi-select
  - automatic «هاپ» schedule: 5/10/15/20 minutes + 20 seconds
  - fish workflow with a minute-based automatic «ماهی» interval plus sell/feed/fridge rules and numeric thresholds 1–6
  - scheduled «برداشت هاپو»: sends «هاپو» first, then clicks the inline withdrawal button in the reply
  - `/play` game mode with 100/200/300; runs in the chat where `/play` is sent and spreads 🎰 messages across 60 seconds
  - `/menu` command to open the configuration menu
  - automatic 3 rescue attempts for a configured street-dog event phrase
- Subscription expiry disables automation.
- All normal bot UI navigation edits the existing bot message rather than sending a new UI message.
- `/help` explains usage without exposing implementation details.
- `/start` always checks required channel membership first.

## Important Railway persistence note

Railway containers can be recreated. Mount a Railway Volume at `/data`, otherwise sessions/subscriptions can disappear after a redeploy/restart.

## Required setup

1. Create a bot with BotFather and put its token in `BOT_TOKEN`.
2. No API ID/API Hash is requested from users and no such Railway variables are needed. The bundled Telethon app credentials are used internally; users authenticate with phone number, Telegram code, and optional 2FA.
3. Add required channels to `FORCE_JOIN_CHANNELS` using `chat_id|join_url`.
4. Make the bot administrator in required channels so membership checks are reliable.
5. Put admin numeric IDs in `ADMIN_IDS` and payment admins in `PAYMENT_ADMIN_IDS`.
6. Set `CARD_NUMBER` and `SUPPORT_URL`.
7. Set `GAME_BOT_USERNAME` to the bot that replies to «ماهی».
8. For `/play` and `/fish` to work from the connected Telegram account in any chat, the self account's outgoing command listener must be active (this build enables it automatically after login).
8. Deploy this repository to Railway and mount a Volume to `/data`.

## Run locally

```bash
cp .env.example .env
# edit .env
docker build -t telegram-selfbot-panel .
docker run --env-file .env -p 8080:8080 -v "$PWD/data:/data" telegram-selfbot-panel
```

The health endpoint is available on port 8080.

## Notes about Telegram account login

The user shares their own phone number with the bot. The app asks for the Telegram login code and, if enabled, the 2FA password. Session data is kept per Telegram user ID.

Never publish your `BOT_TOKEN` or session files. The bundled Telethon app credentials should still be treated as application credentials.

Use automation only on accounts/chats where you are authorized to do so and respect Telegram's rules and rate limits.
