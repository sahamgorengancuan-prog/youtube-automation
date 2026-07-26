"""Test aturan triage deterministik (jalur yang selalu aktif, tanpa LLM)."""

from __future__ import annotations

import pytest

from tsc.llm import needs_llm, rule_triage
from tsc.models import TriageAction
from tsc.telegram.base import (
    AuthError,
    ChatAdminRequired,
    FloodWait,
    NetworkError,
    PeerFlood,
    RpcError,
    TelegramError,
    UserAlreadyParticipant,
    UserPrivacyRestricted,
)


def triage(error, attempts=1, max_attempts=2):
    return rule_triage(error, attempts=attempts, max_attempts=max_attempts)


def test_flood_wait_menunggu_persis_sesuai_permintaan_server():
    decision = triage(FloodWait(300))
    assert decision.action is TriageAction.BACKOFF
    assert decision.wait_seconds == 300  # tidak pernah dipersingkat


def test_peer_flood_memarkir_agent():
    decision = triage(PeerFlood())
    assert decision.action is TriageAction.PARK_AGENT
    assert decision.wait_seconds >= 3600


def test_masalah_akses_agent_butuh_operator():
    for error in (AuthError(), ChatAdminRequired()):
        decision = triage(error)
        assert decision.action is TriageAction.PARK_AGENT
        assert decision.wait_seconds < 0  # tidak pulih sendiri


def test_privasi_user_dilewati_tanpa_retry():
    decision = triage(UserPrivacyRestricted())
    assert decision.action is TriageAction.SKIP_MEMBER
    assert decision.wait_seconds == 0


def test_sudah_jadi_member_dilewati():
    assert triage(UserAlreadyParticipant()).action is TriageAction.SKIP_MEMBER


def test_gangguan_jaringan_diulang_dengan_backoff():
    decision = triage(NetworkError(), attempts=1, max_attempts=3)
    assert decision.action is TriageAction.RETRY
    assert decision.wait_seconds > 0


def test_gangguan_jaringan_menyerah_setelah_batas_percobaan():
    decision = triage(NetworkError(), attempts=3, max_attempts=3)
    assert decision.action is TriageAction.SKIP_MEMBER


def test_backoff_membesar_seiring_percobaan():
    pertama = triage(RpcError(), attempts=1, max_attempts=5).wait_seconds
    kedua = triage(RpcError(), attempts=2, max_attempts=5).wait_seconds
    assert kedua > pertama


class ErrorBelumDipetakan(TelegramError):
    """Error hipotetis yang belum punya aturan penanganan."""

    code = "belum_dipetakan"


def test_error_tak_dikenal_dieskalasi_dan_memanggil_llm():
    decision = triage(ErrorBelumDipetakan("error baru dari Telegram"))
    assert decision.action is TriageAction.ESCALATE
    assert decision.confidence == 0.0
    assert needs_llm(decision)  # inilah satu-satunya jalur yang memanggil LLM


def test_keputusan_pasti_tidak_memanggil_llm():
    for error in (FloodWait(10), PeerFlood(), UserPrivacyRestricted()):
        assert not needs_llm(triage(error))
