"""Telegram bot interface — STUB.

Not implemented; the CLI (cli.py) is sufficient for current use.

TODO when this is wanted:
  - Read TELEGRAM_BOT_TOKEN from env (or a `.env` file via python-dotenv)
  - Wire up python-telegram-bot or aiogram
  - Commands:
      /search <ORIGIN> <DESTINATION> <DD-MM-YYYY> [HH:MM]
      /stations <query>
  - Render results via formatter.render_results(); paginate if >5 options
  - Suppress raw stack traces; surface friendly errors
"""

raise NotImplementedError(
    "telegram_bot.py is a stub. CLI (cli.py) is the supported entry point."
)
