from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Iterable

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from app.core.config import settings
from app.plugins.contracts import DataSource, Event


class TelegramSource(DataSource):
    """Dedicated-account Telegram connector.

    On stateless hosting such as Render, use TELEGRAM_SESSION_STRING. Locally a
    normal session file can be used while initially authorizing the account.
    """

    name = "telegram"

    def __init__(self) -> None:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        session = (
            StringSession(settings.telegram_session_string)
            if settings.telegram_session_string
            else settings.telegram_session_name
        )
        self.client = TelegramClient(session, settings.telegram_api_id, settings.telegram_api_hash)

    async def historical(
        self,
        *,
        channel: str,
        limit: int | None = None,
        offset_date: datetime | None = None,
        **_: Any,
    ) -> Iterable[Event]:
        result: list[Event] = []
        async with self.client:
            entity = await self.client.get_entity(channel)
            async for message in self.client.iter_messages(entity, limit=limit, offset_date=offset_date, reverse=True):
                result.append(self._message_event(channel, message, "message"))
        return result

    async def stream(self, *, channel: str, **_: Any) -> AsyncIterator[Event]:
        queue: "__import__('asyncio').Queue[Event]"  # type annotation only
        import asyncio

        queue = asyncio.Queue()

        @self.client.on(events.NewMessage(chats=channel))
        async def on_new(event):
            await queue.put(self._message_event(channel, event.message, "message"))

        @self.client.on(events.MessageEdited(chats=channel))
        async def on_edit(event):
            await queue.put(self._message_event(channel, event.message, "edit"))

        await self.client.start()
        try:
            while True:
                yield await queue.get()
        finally:
            await self.client.disconnect()

    @staticmethod
    def _message_event(channel: str, message: Any, event_type: str) -> Event:
        metadata = {
            "event_type": event_type,
            "edit_date": message.edit_date.isoformat() if message.edit_date else None,
            "reply_to_msg_id": getattr(message, "reply_to_msg_id", None),
            "grouped_id": str(message.grouped_id) if getattr(message, "grouped_id", None) else None,
        }
        return Event(
            source="telegram",
            timestamp=message.date,
            external_id=str(message.id),
            payload={
                "channel": channel,
                "text": message.message or "",
                "edit_date": metadata["edit_date"],
            },
            metadata=metadata,
        )
