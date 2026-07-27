# Panduan Colab / Colab user guide

## Cara tercepat (ID)
1. Buka `notebooks/SIAS_Agentic_Colab_Control_Center.ipynb` di Google Colab.
2. Jalankan sel Inisialisasi (Bagian 0) — gratis, tanpa API.
3. Bagian 2: setup dependensi (ringan; tanpa Node/GPU).
4. Bagian 3: sambungkan Google Drive (opsional tetapi disarankan — untuk resume).
5. Bagian 4: tambahkan kunci di Colab Secrets (🔑): BFL_API_KEY, OPENAI_API_KEY,
   OPENROUTER_API_KEY. (HF_* opsional.) Nilai tidak pernah ditampilkan.
6. Bagian 5: isi TOPIC, pilih RUN_MODE=plan, jalankan.
7. Bagian 6–7: perencanaan GRATIS + tinjau storyboard.
8. Bagian 8: canary (berbayar kecil) — set RUN_MODE=canary, ARM_PAID_CALLS=True,
   centang CONFIRM_CANARY.
9. Bagian 9–10: style lock lalu SETUJUI secara manual.
10. Bagian 11–12: pilot 4 adegan, tonton hasilnya.
11. Bagian 13–14: buka kunci produksi (hanya bila pilot PASS) dan produksi penuh.
12. Bagian 15–16: QC final dan ekspor ZIP.

## Resume setelah terputus
Buka ulang notebook, jalankan Bagian 1–5 dengan TOPIC yang sama; state.json di
Drive melanjutkan dari tahap tervalidasi terakhir. Aset yang sudah disetujui
tidak dibuat ulang.

## English quick path
Run sections 1–7 free (plan), section 8 canary (small paid), 9–10 style lock +
manual approval, 11–12 four-scene pilot, 13–14 production (locked until pilot
PASS), 15–16 QC + export. Toggle UI_LANG="en" in the init cell.

## Diagnosing provider failures
Every failure names the stage, provider, model, request id (when available),
whether it is recoverable, and the recommended next action. See
docs/troubleshooting.md.
