"""Implementasi klien Telegram asli di atas Telethon.

Telethon diimpor secara lazy (di dalam fungsi) supaya seluruh framework —
termasuk dashboard dan test — tetap bisa jalan di mesin yang belum menginstal
Telethon selama mode-nya "simulate".

Tanggung jawab utama modul ini: **menerjemahkan** exception Telethon menjadi
error stabil di ``base.py``. Tidak ada logika bisnis di sini.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from .base import (
    AdminRights,
    AuthError,
    ChannelPrivate,
    ChatAdminRequired,
    ChatInfo,
    ChatWriteForbidden,
    FloodWait,
    NetworkError,
    PeerFlood,
    RpcError,
    TelegramError,
    UserAlreadyParticipant,
    UserChannelsTooMuch,
    UserDeactivated,
    UserInfo,
    UserNotMutualContact,
    UserPrivacyRestricted,
)

#: Peta nama exception Telethon -> error framework. Memakai nama kelas sebagai
#: kunci agar tidak perlu mengimpor Telethon saat modul ini dimuat.
_ERROR_MAP: dict[str, type[TelegramError]] = {
    "PeerFloodError": PeerFlood,
    "UserPrivacyRestrictedError": UserPrivacyRestricted,
    "UserAlreadyParticipantError": UserAlreadyParticipant,
    "UserNotMutualContactError": UserNotMutualContact,
    "UserChannelsTooMuchError": UserChannelsTooMuch,
    "UserDeactivatedError": UserDeactivated,
    "UserDeactivatedBanError": UserDeactivated,
    "InputUserDeactivatedError": UserDeactivated,
    "ChatAdminRequiredError": ChatAdminRequired,
    "ChatWriteForbiddenError": ChatWriteForbidden,
    "ChannelPrivateError": ChannelPrivate,
    "AuthKeyUnregisteredError": AuthError,
    "SessionRevokedError": AuthError,
    "SessionExpiredError": AuthError,
    "UnauthorizedError": AuthError,
}


def translate_error(exc: BaseException) -> TelegramError:
    """Ubah exception apa pun dari Telethon menjadi ``TelegramError``."""
    if isinstance(exc, TelegramError):
        return exc

    name = type(exc).__name__

    # FloodWaitError membawa atribut `seconds`; ada beberapa varian namanya.
    if "FloodWait" in name or "SlowModeWait" in name:
        seconds = int(getattr(exc, "seconds", 60) or 60)
        return FloodWait(seconds, str(exc))

    mapped = _ERROR_MAP.get(name)
    if mapped is not None:
        return mapped(str(exc))

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return NetworkError(f"{name}: {exc}")

    return RpcError(f"{name}: {exc}")


class TelethonAgentClient:
    """Satu akun user Telegram (satu file sesi) sebagai agent."""

    def __init__(
        self,
        label: str,
        session_path: str,
        api_id: int,
        api_hash: str,
        *,
        device_model: str = "TSC Agent",
    ) -> None:
        self.label = label
        self.session_path = session_path
        self._api_id = api_id
        self._api_hash = api_hash
        self._device_model = device_model
        self._client: Any = None

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> UserInfo:
        try:
            from telethon import TelegramClient as _Telethon
        except ImportError as exc:  # pragma: no cover - bergantung lingkungan
            raise AuthError(
                "Telethon belum terpasang. Jalankan setup.bat atau "
                "`pip install -r requirements.txt`."
            ) from exc

        self._client = _Telethon(
            self.session_path,
            self._api_id,
            self._api_hash,
            device_model=self._device_model,
        )
        try:
            await self._client.connect()
            if not await self._client.is_user_authorized():
                raise AuthError(
                    f"Sesi '{self.session_path}' belum login. "
                    "Jalankan `python -m tsc login --agent " + self.label + "`."
                )
            me = await self._client.get_me()
        except TelegramError:
            raise
        except BaseException as exc:
            raise translate_error(exc) from exc

        return UserInfo(
            user_id=int(me.id),
            username=getattr(me, "username", None),
            access_hash=getattr(me, "access_hash", None),
            is_bot=bool(getattr(me, "bot", False)),
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    def _require(self) -> Any:
        if self._client is None:
            raise AuthError(f"Agent '{self.label}' belum connect()")
        return self._client

    # ----------------------------------------------------------------- chats

    async def resolve_chat(self, ref: str) -> ChatInfo:
        client = self._require()
        try:
            entity = await client.get_entity(ref)
            full = await self._participants_count(entity)
        except BaseException as exc:
            raise translate_error(exc) from exc
        return ChatInfo(
            id=int(entity.id),
            title=getattr(entity, "title", None) or getattr(entity, "username", "") or str(entity.id),
            username=getattr(entity, "username", None),
            participants_count=full,
            is_channel=bool(getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False)),
        )

    async def _participants_count(self, entity: Any) -> int:
        client = self._require()
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest

            full = await client(GetFullChannelRequest(entity))
            return int(getattr(full.full_chat, "participants_count", 0) or 0)
        except BaseException:
            # Grup biasa (bukan channel) atau tidak punya akses — bukan fatal.
            return int(getattr(entity, "participants_count", 0) or 0)

    async def get_admin_rights(self, chat: ChatInfo) -> AdminRights:
        client = self._require()
        try:
            from telethon.tl.types import (
                ChannelParticipantAdmin,
                ChannelParticipantCreator,
            )

            entity = await client.get_entity(chat.id)
            me = await client.get_me()
            participant = await client.get_permissions(entity, me)
        except BaseException as exc:
            raise translate_error(exc) from exc

        # `get_permissions` mengembalikan ParticipantPermissions dengan
        # properti .is_admin/.is_creator dan .add_admins/.invite_users.
        is_creator = bool(getattr(participant, "is_creator", False))
        is_admin = bool(getattr(participant, "is_admin", False)) or is_creator
        can_invite = bool(getattr(participant, "invite_users", False)) or is_creator
        raw = {
            "is_admin": is_admin,
            "is_creator": is_creator,
            "invite_users": can_invite,
            "participant_type": type(getattr(participant, "participant", participant)).__name__,
        }
        del ChannelParticipantAdmin, ChannelParticipantCreator  # hanya untuk validasi impor
        return AdminRights(
            is_admin=is_admin,
            is_creator=is_creator,
            can_invite_users=can_invite,
            raw=raw,
        )

    async def iter_participants(
        self, chat: ChatInfo, limit: int | None = None
    ) -> AsyncIterator[UserInfo]:
        client = self._require()
        try:
            entity = await client.get_entity(chat.id)
            async for user in client.iter_participants(entity, limit=limit):
                yield UserInfo(
                    user_id=int(user.id),
                    username=getattr(user, "username", None),
                    access_hash=getattr(user, "access_hash", None),
                    is_bot=bool(getattr(user, "bot", False)),
                    is_deleted=bool(getattr(user, "deleted", False)),
                    last_seen_days=_status_to_days(getattr(user, "status", None)),
                )
        except BaseException as exc:
            raise translate_error(exc) from exc

    # ---------------------------------------------------------------- invite

    async def invite(self, chat: ChatInfo, user: UserInfo) -> None:
        client = self._require()
        try:
            from telethon.tl.functions.channels import InviteToChannelRequest
            from telethon.tl.types import InputUser

            entity = await client.get_entity(chat.id)
            if user.access_hash is not None:
                target: Any = InputUser(user_id=user.user_id, access_hash=user.access_hash)
            else:
                target = await client.get_input_entity(user.user_id)
            await client(InviteToChannelRequest(entity, [target]))
        except BaseException as exc:
            raise translate_error(exc) from exc


def _status_to_days(status: Any) -> int | None:
    """Perkirakan 'terakhir online berapa hari lalu' dari objek status Telethon."""
    if status is None:
        return None
    name = type(status).__name__
    return {
        "UserStatusOnline": 0,
        "UserStatusRecently": 1,
        "UserStatusLastWeek": 7,
        "UserStatusLastMonth": 30,
    }.get(name)
