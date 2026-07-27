"""Bilingual UI strings — Indonesian default, English toggle. Code, schemas,
logs, and identifiers stay English."""

from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "welcome_title": {
        "id": "🎬 SIAS — Studio Cerita Sains Bergambar",
        "en": "🎬 SIAS — Scientific Illustrated Audio Studio",
    },
    "welcome_body": {
        "id": ("Sistem ini membuat video sains pendek dari GAMBAR ILUSTRASI + NARASI ALAMI. "
               "Bukan video AI — gambar diam yang disusun mengikuti suara. "
               "Mode awal: plan (GRATIS, tanpa API berbayar)."),
        "en": ("This system builds short science videos from STILL ILLUSTRATIONS + NATURAL NARRATION. "
               "Not AI video — approved stills timed to the voice. "
               "Initial mode: plan (FREE, no paid API calls)."),
    },
    "run_mode": {"id": "Mode berjalan", "en": "Run mode"},
    "episode": {"id": "Episode", "en": "Episode"},
    "budget_used": {"id": "Anggaran terpakai", "en": "Budget used"},
    "secrets": {"id": "Status kunci API", "en": "API key status"},
    "next_action": {"id": "Langkah berikutnya", "en": "Next recommended action"},
    "arm_warning": {
        "id": "⚠️ Mengaktifkan ini MENGIZINKAN panggilan API BERBAYAR pada bagian yang Anda jalankan.",
        "en": "⚠️ Enabling this ALLOWS PAID API calls for the sections you run.",
    },
    "paid_blocked": {
        "id": "Ditolak: panggilan berbayar belum diizinkan (lihat alasan di bawah).",
        "en": "Refused: paid call not permitted (see reasons below).",
    },
    "approve_style": {"id": "✅ Setujui Style Lock", "en": "✅ Approve Style Lock"},
    "start": {"id": "▶️ Mulai", "en": "▶️ Start"},
    "export": {"id": "📦 Ekspor paket episode", "en": "📦 Export episode package"},
    "plan_done": {
        "id": "Perencanaan selesai (gratis). Periksa storyboard di bawah.",
        "en": "Planning finished (free). Review the storyboard below.",
    },
    "stage": {"id": "Tahap", "en": "Stage"},
    "provider": {"id": "Penyedia", "en": "Provider"},
    "model": {"id": "Model", "en": "Model"},
    "est_calls": {"id": "Perkiraan panggilan", "en": "Estimated calls"},
    "recoverable": {"id": "Bisa dipulihkan", "en": "Recoverable"},
    "recommended": {"id": "Saran", "en": "Recommendation"},
}


def t(key: str, lang: str = "id") -> str:
    entry = TEXTS.get(key, {})
    return entry.get(lang, entry.get("en", key))
