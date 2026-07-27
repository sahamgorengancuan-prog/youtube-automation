# Troubleshooting / Pemecahan masalah

| Gejala / Symptom | Penyebab & solusi / Cause & fix |
|---|---|
| `PaidCallBlockedError: ARM_PAID_CALLS is false` | Normal & disengaja. Centang ARM_PAID_CALLS + checkbox konfirmasi bagian itu, lalu jalankan ulang sel. |
| `required secret missing` | Tambahkan kunci di Colab Secrets (ikon 🔑) lalu jalankan ulang Bagian 4. Nilai tidak pernah ditampilkan. |
| `stage not allowed by current run mode` | Ubah RUN_MODE di Bagian 5 sesuai tahap (mis. `pilot`), jalankan ulang sel konfigurasi. |
| `BudgetExceededError` | Batas anggaran tercapai — sistem BERHENTI sebelum membayar lebih. Naikkan HARD_BUDGET_IMAGES secara sadar. |
| Runtime Colab terputus | Buka ulang notebook, jalankan Bagian 1–5; state.json di Drive membuat sistem lanjut dari tahap tervalidasi terakhir. |
| `ffmpeg MISSING` | Jalankan ulang Bagian 2; di Colab ffmpeg sudah tersedia. Lokal: `apt install ffmpeg`. |
| `ProviderRequestError: … transport not configured` | Adapter live belum disambungkan — lihat docs/provider_contracts.md. Tanpa transport, SIAS menolak (tidak pernah memalsukan hasil). |
| `ProviderSchemaError` dari reviewer | Model vision mengembalikan non-JSON. Ulangi sekali; bila terus, ganti slug model (resolver katalog). |
| `SilentAudioError` | TTS menghasilkan senyap — hard failure. Cek voice/model, ulangi TTS. Tidak ada placeholder senyap di produksi. |
| `BFL Request Moderated` | Kegagalan eksplisit per-adegan; perbaiki prompt adegan itu (repair), bukan mengulang seluruh episode. |
| MP4 terlalu kecil / stream hilang | Validator menolak; periksa render/work/*; file kecil TIDAK pernah dianggap sukses. |
| Durasi video ≠ narasi | Toleransi default 0.08 dtk. Cek alignment; audio adalah sumber kebenaran timeline. |
| Widget tidak muncul | Fallback berbasis teks aktif otomatis; semua fungsi tetap tersedia. |
| Higgsfield error | OPSIONAL — nonaktifkan (default) dan pipeline inti tetap lengkap. |
