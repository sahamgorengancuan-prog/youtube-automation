"""Kontrak klien Telegram + taksonomi error.

Seluruh sisa framework hanya bicara dengan antarmuka di file ini. Ada dua
implementasi: ``telethon_client`` (asli) dan ``simulator`` (tiruan, untuk
latihan/test tanpa menyentuh Telegram). Karena orchestrator, governor, dan
batching tidak tahu implementasi mana yang dipakai, semuanya bisa diuji offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# --------------------------------------------------------------------- errors


class TelegramError(Exception):
    """Induk semua error Telegram yang sudah dinormalisasi.

    ``code`` adalah string stabil yang dipakai untuk taksonomi error di
    dashboard, aturan triage, dan test — jangan diubah sembarangan.
    """

    code = "unknown"
    retryable = False

    def __init__(self, message: str = "", **context: Any) -> None:
        super().__init__(message or self.code)
        self.context = context


class AuthError(TelegramError):
    """Sesi tidak valid / belum login. Agent harus dinonaktifkan."""

    code = "auth_failed"


class FloodWait(TelegramError):
    """Telegram meminta menunggu N detik sebelum request berikutnya."""

    code = "flood_wait"
    retryable = True

    def __init__(self, seconds: int, message: str = "") -> None:
        super().__init__(message or f"flood wait {seconds}s", seconds=seconds)
        self.seconds = int(seconds)


class PeerFlood(TelegramError):
    """Akun sedang dilimitasi karena terlalu banyak aksi ke user lain.

    Ini sinyal paling penting: agent harus langsung diparkir lama, bukan diulang.
    """

    code = "peer_flood"


class UserPrivacyRestricted(TelegramError):
    """Pengaturan privasi user menolak untuk ditambahkan ke grup."""

    code = "privacy_restricted"


class UserAlreadyParticipant(TelegramError):
    code = "already_participant"


class UserNotMutualContact(TelegramError):
    code = "not_mutual_contact"


class UserChannelsTooMuch(TelegramError):
    """User sudah bergabung di terlalu banyak channel/grup."""

    code = "user_channels_too_much"


class ChatAdminRequired(TelegramError):
    """Agent tidak punya hak admin yang dibutuhkan di chat ini."""

    code = "admin_required"


class ChatWriteForbidden(TelegramError):
    code = "write_forbidden"


class UserDeactivated(TelegramError):
    code = "user_deactivated"


class ChannelPrivate(TelegramError):
    """Chat tidak dapat diakses oleh akun ini (private / kena kick)."""

    code = "channel_private"


class RpcError(TelegramError):
    """Error RPC lain yang belum dipetakan."""

    code = "rpc_error"
    retryable = True


class NetworkError(TelegramError):
    code = "network_error"
    retryable = True


#: Error yang menandakan member ini memang tidak bisa diinvite — jangan retry.
PERMANENT_MEMBER_ERRORS = {
    UserPrivacyRestricted.code,
    UserAlreadyParticipant.code,
    UserNotMutualContact.code,
    UserChannelsTooMuch.code,
    UserDeactivated.code,
}

#: Error yang menandakan masalah pada agent, bukan pada member.
AGENT_LEVEL_ERRORS = {
    PeerFlood.code,
    AuthError.code,
    ChatAdminRequired.code,
    ChatWriteForbidden.code,
    ChannelPrivate.code,
}


# ----------------------------------------------------------------- data model


@dataclass(slots=True)
class ChatInfo:
    """Metadata chat yang dibutuhkan framework."""

    id: int
    title: str
    username: str | None = None
    participants_count: int = 0
    is_channel: bool = False


@dataclass(slots=True)
class AdminRights:
    """Hak admin satu akun pada satu chat."""

    is_admin: bool = False
    is_creator: bool = False
    can_invite_users: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def can_run_campaign(self) -> bool:
        """Cukup untuk dipakai sebagai agent: admin + boleh menambah member."""
        return self.is_admin and (self.is_creator or self.can_invite_users)


@dataclass(slots=True)
class UserInfo:
    user_id: int
    username: str | None = None
    access_hash: int | None = None
    is_bot: bool = False
    is_deleted: bool = False
    last_seen_days: int | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "access_hash": self.access_hash,
            "is_bot": self.is_bot,
            "is_deleted": self.is_deleted,
            "last_seen_days": self.last_seen_days,
        }


# ------------------------------------------------------------------ protocol


@runtime_checkable
class TelegramClient(Protocol):
    """Antarmuka minimal yang dipakai orchestrator.

    Implementasi wajib menerjemahkan error native menjadi subclass
    ``TelegramError`` di atas — tidak boleh membocorkan exception Telethon.
    """

    label: str

    async def connect(self) -> UserInfo:
        """Login memakai sesi tersimpan. Raise ``AuthError`` kalau gagal."""
        ...

    async def disconnect(self) -> None: ...

    async def resolve_chat(self, ref: str) -> ChatInfo:
        """Ubah @username / id / invite link menjadi ``ChatInfo``."""
        ...

    async def get_admin_rights(self, chat: ChatInfo) -> AdminRights:
        """Hak admin akun ini pada ``chat``."""
        ...

    async def iter_participants(self, chat: ChatInfo, limit: int | None = None):
        """Async iterator berisi ``UserInfo``."""
        ...

    async def invite(self, chat: ChatInfo, user: UserInfo) -> None:
        """Tambahkan ``user`` ke ``chat``. Raise ``TelegramError`` kalau gagal."""
        ...
