# Panduan Operasi

## Persiapan sekali jalan

1. `bat\setup.bat` — membuat venv, memasang dependency, menyalin config & .env.
2. Isi `.env`:
   - `TSC_API_ID` dan `TSC_API_HASH` dari <https://my.telegram.org>
   - `TSC_BOT_TOKEN` dari [@BotFather](https://t.me/BotFather) (opsional)
   - `ANTHROPIC_API_KEY` untuk triage LLM (opsional)
3. Isi `config.json`: grup target, daftar source, dan daftar agent.
4. Ubah `telegram.mode` menjadi `"live"`.
5. Login tiap agent: `bat\login-agent.bat admin-1` (ulangi untuk tiap label).
6. `bat\doctor.bat` — pastikan semuanya hijau.
7. `bat\validate.bat` — pastikan admin & source lolos. **Belum ada invite di tahap ini.**
8. `bat\start-dashboard.bat` — jalankan campaign dari dashboard.

## Latihan dulu tanpa risiko

Biarkan `telegram.mode` = `"simulate"` lalu jalankan `bat\start-dashboard.bat`.
Seluruh pipeline berjalan memakai grup dan member tiruan, lengkap dengan flood
wait dan PeerFlood palsu. Cara paling aman untuk membiasakan diri dengan
dashboard sebelum menyentuh akun asli.

## Membaca dashboard

| Panel | Yang perlu diperhatikan |
|---|---|
| **Progres** | Persentase member yang sudah diundang atau memang sudah bergabung. |
| **Agent** | Warna garis kiri: hijau = bekerja, biru = jeda, kuning = flood wait/kuota, merah = dilimitasi/nonaktif. |
| **Batch & sesi** | Batang progres per sesi. `56+4/60` berarti 56 berhasil, 4 gagal, dari 60. |
| **Error (24 jam)** | Kalau `peer_flood` naik, laju terlalu tinggi — turunkan kuota. |
| **Keputusan triage** | Alasan setiap agent diparkir atau member dilewati. |
| **Log kejadian** | Kronologi lengkap; saring per tingkat keparahan. |

## Angka kuota yang wajar

Default di `config.example.json` sengaja konservatif:

```json
"invites_per_day_per_agent": 40,
"invites_per_hour_per_agent": 15,
"min_gap_seconds": 45,
"gap_jitter_seconds": 25
```

Enam agent × 40 = 240 invite per hari. Untuk memindahkan 5.000 member perlu
sekitar tiga minggu. Itu bukan kelambatan yang perlu "dioptimalkan" — akun yang
dipaksa lebih cepat akan kena PeerFlood dan justru berhenti total.

Ukuran batch (`session_size`) tidak memengaruhi laju; itu hanya unit kerja agar
progres mudah dibaca. Kuota harianlah yang menentukan kecepatan sesungguhnya.

## Gejala dan tindakan

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| Semua agent `limited` bersamaan | Kuota terlalu tinggi, atau target dilaporkan spam | Hentikan campaign, tunggu 24–48 jam, turunkan kuota separuh |
| `privacy_restricted` > 30% | Wajar untuk grup publik lama | Tidak perlu tindakan; member tersebut tidak bisa ditambahkan siapa pun |
| `admin_required` | Hak admin agent dicabut di grup | Kembalikan hak "Add Users", lalu `bat\validate.bat` |
| `auth_failed` | Sesi kedaluwarsa atau dicabut dari perangkat lain | `bat\login-agent.bat <label>` |
| Progres berhenti tapi status `running` | Semua agent sedang menunggu kuota/flood wait | Normal — cek kolom "siap" di kartu agent |
| Dashboard tidak bisa dibuka | Port 8787 dipakai aplikasi lain | Ubah `dashboard.port` di config.json |

## Menghentikan dan melanjutkan

- Tombol **Stop** di dashboard atau Ctrl+C: aman kapan saja. Batch yang sedang
  berjalan dikembalikan ke antrean, member yang sudah diundang tidak diulang.
- Menjalankan ulang akan melanjutkan dari antrean yang sama — tidak perlu
  scrape ulang.
- **Susun ulang batch** dipakai setelah menambah/mengurangi agent atau setelah
  ada agent yang diparkir; batch yang sedang berjalan tidak diganggu.

## Menambah agent di tengah jalan

1. Hentikan campaign.
2. Tambahkan entri baru di `config.json` → `agents`.
3. `bat\login-agent.bat <label-baru>`.
4. `bat\validate.bat` — pastikan agent baru admin di target.
5. `bat\plan.bat` atau tombol **Susun ulang batch**.
6. Jalankan lagi.

## Menjalankan di VPS / terjadwal

`bat\run-headless.bat` menjalankan campaign tanpa dashboard dan menulis log ke
`logs\`. Cocok dipasang di Task Scheduler untuk berjalan tiap hari — karena
kuota harian ditegakkan per akun, menjalankannya sekali sehari sudah cukup untuk
menghabiskan jatah tanpa perlu proses yang hidup 24 jam.

Di Linux/macOS gunakan `./sh/tsc.sh run`.

## Sebelum melapor bug

Jalankan `bat\doctor.bat` dan sertakan keluarannya, ditambah:

```bash
python -m tsc status
sqlite3 data/tsc.db "select error_code, count(*) from attempts group by 1 order by 2 desc limit 10"
```
