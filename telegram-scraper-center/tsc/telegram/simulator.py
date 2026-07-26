"""Klien Telegram tiruan.

Dipakai untuk: menjalankan demo dashboard tanpa akun asli, latihan operator,
dan test otomatis. Perilakunya sengaja dibuat "realistis-nakal" — ada
FloodWait, PeerFlood, privasi tertutup, dan latensi acak — supaya jalur
penanganan error benar-benar terlatih sebelum dipakai live.

Deterministik terhadap ``seed`` sehingga test bisa mengandalkan hasilnya.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from typing import AsyncIterator

from .base import (
    AdminRights,
    ChatInfo,
    FloodWait,
    NetworkError,
    PeerFlood,
    UserAlreadyParticipant,
    UserChannelsTooMuch,
    UserDeactivated,
    UserInfo,
    UserPrivacyRestricted,
)


def _stable_id(text: str, span: int = 9_000_000) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return 1_000_000 + int.from_bytes(digest[:6], "big") % span


class SimulatorWorld:
    """Dunia bersama untuk semua agent simulasi.

    Menyimpan daftar member per chat dan siapa yang sudah tergabung di target,
    supaya invite ganda menghasilkan ``UserAlreadyParticipant`` seperti aslinya.
    """

    def __init__(
        self,
        *,
        seed: int = 7,
        members_per_source: int = 1200,
        admin_labels: set[str] | None = None,
        latency_range: tuple[float, float] = (0.01, 0.05),
    ) -> None:
        self.seed = seed
        self.members_per_source = members_per_source
        self.admin_labels = admin_labels  # None = semua agent dianggap admin
        self.latency_range = latency_range
        self._chats: dict[str, ChatInfo] = {}
        self._members: dict[int, list[UserInfo]] = {}
        self._joined: set[tuple[int, int]] = set()
        self._lock = asyncio.Lock()
        # Statistik untuk assertion di test.
        self.invite_calls = 0
        self.flood_waits_raised = 0
        self.peer_floods_raised = 0

    def chat(self, ref: str) -> ChatInfo:
        if ref not in self._chats:
            chat_id = _stable_id(ref)
            info = ChatInfo(
                id=chat_id,
                title=ref.lstrip("@").replace("_", " ").title(),
                username=ref.lstrip("@") if ref.startswith("@") else None,
                participants_count=0,
                is_channel=True,
            )
            self._chats[ref] = info
            rng = random.Random(f"{self.seed}:{ref}")
            count = max(1, int(rng.gauss(self.members_per_source, self.members_per_source * 0.15)))
            people: list[UserInfo] = []
            for i in range(count):
                uid = _stable_id(f"{ref}:user:{i}")
                people.append(
                    UserInfo(
                        user_id=uid,
                        username=f"user{uid % 100000}",
                        access_hash=uid * 31,
                        is_bot=rng.random() < 0.02,
                        is_deleted=rng.random() < 0.03,
                        last_seen_days=rng.choice([0, 1, 3, 7, 30, 90, None]),
                    )
                )
            self._members[chat_id] = people
            info.participants_count = len(people)
        return self._chats[ref]

    def members_of(self, chat: ChatInfo) -> list[UserInfo]:
        return self._members.get(chat.id, [])

    def is_joined(self, chat: ChatInfo, user: UserInfo) -> bool:
        return (chat.id, user.user_id) in self._joined

    def join(self, chat: ChatInfo, user: UserInfo) -> None:
        self._joined.add((chat.id, user.user_id))


class SimulatedClient:
    """Implementasi ``TelegramClient`` yang tidak menyentuh jaringan."""

    def __init__(
        self,
        label: str,
        world: SimulatorWorld,
        *,
        latency_range: tuple[float, float] | None = None,
        privacy_rate: float = 0.12,
        flood_every: int = 37,
        peer_flood_after: int | None = 90,
    ) -> None:
        self.label = label
        self.world = world
        self.latency_range = latency_range or world.latency_range
        self.privacy_rate = privacy_rate
        self.flood_every = flood_every
        self.peer_flood_after = peer_flood_after
        self._rng = random.Random(f"{world.seed}:{label}")
        self._connected = False
        self._invites_done = 0
        self._me = UserInfo(
            user_id=_stable_id(f"agent:{label}"),
            username=label.lower().replace(" ", "_"),
        )

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> UserInfo:
        await self._sleep()
        self._connected = True
        return self._me

    async def disconnect(self) -> None:
        self._connected = False

    async def _sleep(self) -> None:
        lo, hi = self.latency_range
        if hi > 0:
            await asyncio.sleep(self._rng.uniform(lo, hi))

    # ----------------------------------------------------------------- chats

    async def resolve_chat(self, ref: str) -> ChatInfo:
        await self._sleep()
        return self.world.chat(ref)

    async def get_admin_rights(self, chat: ChatInfo) -> AdminRights:
        await self._sleep()
        allowed = self.world.admin_labels
        is_admin = allowed is None or self.label in allowed
        return AdminRights(
            is_admin=is_admin,
            is_creator=False,
            can_invite_users=is_admin,
            raw={"simulated": True},
        )

    async def iter_participants(
        self, chat: ChatInfo, limit: int | None = None
    ) -> AsyncIterator[UserInfo]:
        people = self.world.members_of(chat)
        if limit is not None:
            people = people[:limit]
        for index, person in enumerate(people):
            if index % 200 == 0:
                await asyncio.sleep(0)  # beri kesempatan task lain jalan
            yield person

    # ---------------------------------------------------------------- invite

    async def invite(self, chat: ChatInfo, user: UserInfo) -> None:
        await self._sleep()
        self.world.invite_calls += 1
        self._invites_done += 1

        if self.peer_flood_after and self._invites_done > self.peer_flood_after:
            self.world.peer_floods_raised += 1
            raise PeerFlood("simulasi: akun kena limitasi PeerFlood")

        if self.flood_every and self._invites_done % self.flood_every == 0:
            self.world.flood_waits_raised += 1
            raise FloodWait(self._rng.choice([20, 45, 120]))

        if self.world.is_joined(chat, user):
            raise UserAlreadyParticipant("simulasi: user sudah tergabung")

        roll = self._rng.random()
        if user.is_deleted:
            raise UserDeactivated("simulasi: akun sudah dihapus")
        if roll < self.privacy_rate:
            raise UserPrivacyRestricted("simulasi: privasi user menolak invite")
        if roll < self.privacy_rate + 0.02:
            raise UserChannelsTooMuch("simulasi: user di terlalu banyak grup")
        if roll > 0.985:
            raise NetworkError("simulasi: koneksi terputus sesaat")

        self.world.join(chat, user)
