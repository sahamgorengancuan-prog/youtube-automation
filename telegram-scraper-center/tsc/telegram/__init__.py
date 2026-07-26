"""Adapter Telegram: kontrak, implementasi asli (Telethon), dan simulator."""

from .base import (
    AGENT_LEVEL_ERRORS,
    PERMANENT_MEMBER_ERRORS,
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
    TelegramClient,
    TelegramError,
    UserAlreadyParticipant,
    UserChannelsTooMuch,
    UserDeactivated,
    UserInfo,
    UserNotMutualContact,
    UserPrivacyRestricted,
)
from .simulator import SimulatedClient, SimulatorWorld

__all__ = [
    "AGENT_LEVEL_ERRORS",
    "PERMANENT_MEMBER_ERRORS",
    "AdminRights",
    "AuthError",
    "ChannelPrivate",
    "ChatAdminRequired",
    "ChatInfo",
    "ChatWriteForbidden",
    "FloodWait",
    "NetworkError",
    "PeerFlood",
    "RpcError",
    "SimulatedClient",
    "SimulatorWorld",
    "TelegramClient",
    "TelegramError",
    "UserAlreadyParticipant",
    "UserChannelsTooMuch",
    "UserDeactivated",
    "UserInfo",
    "UserNotMutualContact",
    "UserPrivacyRestricted",
    "build_client",
]


def build_client(cfg, agent_cfg, world=None):
    """Buat klien sesuai ``cfg.telegram.mode``.

    Dipisah dari orchestrator agar penggantian mode simulate/live cukup satu
    baris di config dan tidak menyentuh logika penjadwalan.
    """
    if cfg.telegram.mode == "simulate":
        if world is None:
            raise ValueError("mode simulate membutuhkan SimulatorWorld")
        return SimulatedClient(agent_cfg.label, world)

    from .telethon_client import TelethonAgentClient

    api_id, api_hash = cfg.telegram.credentials()
    return TelethonAgentClient(agent_cfg.label, agent_cfg.session, api_id, api_hash)
