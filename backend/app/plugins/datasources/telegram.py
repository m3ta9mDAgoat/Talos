from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Iterable
from urllib.parse import urlparse

from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession

from app.core.config import settings
from app.services.security import get_secret
from app.plugins.contracts import DataSource, Event


class TelegramSource(DataSource):
    """Dedicated-account Telegram connector.

    Talos uses a normal dedicated Telegram account through MTProto, not a bot,
    so it can read history for chats that account can access. On stateless
    hosting such as Render, TELEGRAM_SESSION_STRING keeps the authenticated
    session outside the ephemeral filesystem.
    """

    name = "telegram"

    def __init__(self) -> None:
        stored = get_secret("telegram")
        api_id = stored.get("api_id") or settings.telegram_api_id
        api_hash = stored.get("api_hash") or settings.telegram_api_hash
        session_string = stored.get("session_string") or settings.telegram_session_string
        if not api_id or not api_hash:
            raise RuntimeError("Telegram credentials are not configured. Add them in Settings → Secure Vault.")
        session = StringSession(session_string) if session_string else settings.telegram_session_name
        self.client = TelegramClient(session, int(api_id), str(api_hash))

    @staticmethod
    def normalize_identifier(identifier: str) -> str | int:
        value = identifier.strip()
        if not value:
            raise ValueError("Telegram channel identifier is empty")

        # Accept full public links, @usernames, bare usernames and numeric peer IDs.
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            if parsed.netloc.lower() in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
                path = parsed.path.strip("/")
                if path and not path.startswith("+") and not path.startswith("joinchat/"):
                    return path.split("/")[0]
        if value.startswith("@"):
            return value[1:]
        try:
            return int(value)
        except ValueError:
            return value

    @staticmethod
    def _invite_hash(identifier: str) -> str | None:
        value = identifier.strip()
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            if parsed.netloc.lower() in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
                path = parsed.path.strip("/")
                if path.startswith("+"):
                    return path[1:]
                if path.startswith("joinchat/"):
                    return path.split("/", 1)[1]
        return None

    async def _resolve_entity(self, identifier: str):
        invite_hash = self._invite_hash(identifier)
        if invite_hash:
            invite = await self.client(functions.messages.CheckChatInviteRequest(hash=invite_hash))
            if isinstance(invite, types.ChatInviteAlready):
                return invite.chat
            raise RuntimeError(
                "The Talos Telegram account has not joined this private invite yet. "
                "Join it with the dedicated account first, then test access again."
            )
        return await self.client.get_entity(self.normalize_identifier(identifier))

    async def total_messages(self, *, channel: str) -> int | None:
        """Return Telegram's current message-count estimate for progress UI."""
        await self.client.connect()
        try:
            if not await self.client.is_user_authorized():
                raise RuntimeError("TELEGRAM_SESSION_STRING is not authorized")
            entity = await self._resolve_entity(channel)
            result = await self.client.get_messages(entity, limit=0)
            total = getattr(result, "total", None)
            return int(total) if total is not None else None
        finally:
            await self.client.disconnect()

    async def access_info(self, *, channel: str) -> dict[str, Any]:
        await self.client.connect()
        try:
            if not await self.client.is_user_authorized():
                raise RuntimeError("TELEGRAM_SESSION_STRING is not authorized")
            entity = await self._resolve_entity(channel)
            messages = await self.client.get_messages(entity, limit=1)
            title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(entity.id)
            username = getattr(entity, "username", None)
            kind = entity.__class__.__name__.lower()
            return {
                "access": True,
                "can_read_history": True,
                "entity_id": int(entity.id),
                "title": title,
                "username": username,
                "kind": kind,
                "sample_message_id": int(messages[0].id) if messages else None,
            }
        finally:
            await self.client.disconnect()

    async def historical(
        self,
        *,
        channel: str,
        limit: int | None = None,
        offset_date: datetime | None = None,
        min_id: int = 0,
        reverse: bool = True,
        **_: Any,
    ) -> Iterable[Event]:
        result: list[Event] = []
        async with self.client:
            entity = await self._resolve_entity(channel)
            async for message in self.client.iter_messages(
                entity,
                limit=limit,
                offset_date=offset_date,
                min_id=min_id,
                reverse=reverse,
            ):
                result.append(self._message_event(channel, message, "message"))
        return result

    async def historical_before(self, *, channel: str, limit: int = 1000, offset_id: int = 0) -> Iterable[Event]:
        """Fetch a page newest→oldest. offset_id is exclusive and survives message-ID gaps."""
        result: list[Event] = []
        async with self.client:
            entity = await self._resolve_entity(channel)
            async for message in self.client.iter_messages(entity, limit=limit, offset_id=offset_id, reverse=False):
                result.append(self._message_event(channel, message, "message"))
        return result

    async def latest(self, *, channel: str, limit: int = 250) -> Iterable[Event]:
        """Fetch the newest messages on demand.

        Render free services sleep, so V0.3 exposes an explicit sync action rather
        than pretending a 24/7 stream is reliable on that hosting tier.
        """
        return await self.historical(channel=channel, limit=limit, reverse=False)

    async def stream(self, *, channel: str, **_: Any) -> AsyncIterator[Event]:
        import asyncio

        queue: asyncio.Queue[Event] = asyncio.Queue()
        resolved = self.normalize_identifier(channel)

        @self.client.on(events.NewMessage(chats=resolved))
        async def on_new(event):
            await queue.put(self._message_event(channel, event.message, "message"))

        @self.client.on(events.MessageEdited(chats=resolved))
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
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
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
