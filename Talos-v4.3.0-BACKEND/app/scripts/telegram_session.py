"""Generate a Telethon StringSession locally for stateless deployments.

Usage from backend directory after setting TELEGRAM_API_ID and TELEGRAM_API_HASH:
    python -m app.scripts.telegram_session

The output is a secret. Put it directly into Render as TELEGRAM_SESSION_STRING.
Never commit it to Git or paste it into issue trackers/chat logs.
"""

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.core.config import settings


async def main() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH first.")
    async with TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash) as client:
        print("\nTELEGRAM_SESSION_STRING=\n")
        print(client.session.save())
        print("\nTreat this value like a password.\n")


if __name__ == "__main__":
    asyncio.run(main())
