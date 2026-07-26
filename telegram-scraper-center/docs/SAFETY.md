# Batasan & Alasan Desain

Dokumen ini menjelaskan apa yang **tidak** dilakukan framework ini dan kenapa.
Bacalah sebelum meminta perubahan pada bagian guard — batasan di bawah bukan
kelalaian, tapi keputusan desain.

---

## 1. Kedua sisi harus grup yang Anda kelola

Sebelum satu invite pun dikirim, framework memverifikasi lewat API Telegram:

| Yang dicek | Syarat |
|---|---|
| Grup **target** | Setiap agent harus admin **dan** punya hak "Add Users" |
| Grup **source** | Minimal satu agent harus berstatus admin di grup tersebut |

Pengecekan source inilah yang membedakan alat ini dari *scraper* biasa. Tanpa
itu, alat semacam ini hanya berguna untuk mengambil member grup orang lain dan
membanjiri mereka dengan undangan — yang berarti:

- **Bagi penerima**: undangan dari komunitas yang tidak pernah mereka minta.
- **Bagi akun Anda**: PeerFlood, lalu pembatasan permanen. Akun yang dipakai
  mass-add ke member non-kontak biasanya kena limitasi dalam hitungan jam.
- **Bagi grup target**: dilaporkan spam, berisiko dibekukan.

Verifikasi dilakukan **secara teknis** (memanggil `get_permissions`), bukan lewat
kotak centang "saya berjanji ini grup saya" di config. Tidak ada opsi konfigurasi
untuk mematikannya. Kalau grup sumber memang milik Anda tapi ditolak, jadikan
salah satu akun agent sebagai admin di grup tersebut.

**Kasus penggunaan yang didukung**: memindahkan komunitas ke grup baru (grup lama
penuh/dibajak/ganti platform), menggabungkan beberapa grup cabang menjadi satu,
memulihkan grup setelah insiden.

---

## 2. Framework tidak mengirim pesan pribadi

Tidak ada jalur kode yang mengirim DM. Ketika invite gagal karena pengaturan
privasi pengguna (`privacy_restricted`), member ditandai `blocked_privacy` dan
muncul di dashboard — **tidak** ada pesan otomatis yang menyusul.

Ini disengaja. "Invite gagal → kirim DM" adalah definisi cold outreach: orang
tersebut sudah secara eksplisit mengatur akunnya agar tidak bisa ditambahkan ke
grup oleh orang asing, dan mengirim pesan justru mengakali pengaturan itu. Itu
juga cara tercepat membuat akun agent dilaporkan dan diblokir.

Kalau Anda perlu mengabari orang-orang tersebut, lakukan lewat kanal yang sudah
mereka setujui — pengumuman di grup asal, pesan siaran ke kontak, atau tautan
undangan yang mereka klik sendiri.

Himpunan aksi triage (`TriageAction`) sengaja tertutup dan tidak memuat aksi
kirim pesan, sehingga LLM pun tidak bisa memutuskan untuk mengirim DM.

---

## 3. Limit Telegram dihormati, bukan diakali

| Sinyal dari server | Tindakan framework |
|---|---|
| `FloodWaitError(n)` | Tunggu **n + margin** detik. Tidak pernah dipersingkat. |
| `PeerFloodError` | Parkir agent 24 jam (default). Tidak ada retry. |
| Hak admin dicabut | Agent dinonaktifkan, menunggu operator. |

Selain itu ada kuota yang framework tegakkan sendiri — default **40 invite per
akun per hari**, jeda 45–70 detik antar invite — agar limit server jarang
tersentuh sejak awal.

**Beberapa agent bukan untuk mengakali kuota.** Kuota Telegram berlaku per akun.
Enam agent yang masing-masing bekerja dalam batas wajarnya adalah cara normal
sebuah tim admin memindahkan komunitas besar. Yang tidak dilakukan framework ini
adalah memindahkan beban dari akun yang **sedang dihukum** ke akun lain agar laju
total tetap tinggi — agent yang kena PeerFlood diparkir, dan kalau semua agent
terparkir, campaign berhenti menunggu, bukan mencari jalan lain.

Menaikkan `invites_per_day_per_agent` di config secara teknis bisa dilakukan.
Konsekuensinya ditanggung akun Anda.

---

## 4. Daftar jangan-undang (opt-out)

Siapa pun yang meminta untuk tidak diundang bisa dimasukkan ke daftar permanen:

```bash
python -m tsc optout 123456789 --reason "diminta yang bersangkutan"
```

Atau lewat `POST /api/optout` dari dashboard. Member dalam daftar ini
dikeluarkan dari antrean saat itu juga dan tidak akan masuk rencana batch
berikutnya, termasuk kalau mereka muncul lagi dari source lain.

---

## 5. Data pribadi

- Yang disimpan: user_id, username, access_hash, penanda bot/terhapus. Cukup
  untuk mengirim invite dan mencegah duplikat — tidak lebih.
- Tidak ada nomor telepon, nama, foto, atau riwayat pesan.
- Konteks yang dikirim ke LLM **tidak memuat identitas member sama sekali** —
  hanya kode error, status agent, dan angka agregat. Lihat `TriageContext` di
  `tsc/llm.py`.
- `data/tsc.db` berisi daftar member komunitas Anda. Perlakukan seperti basis
  data pengguna: jangan di-commit, jangan dibagikan, hapus setelah selesai.
- File sesi di `sessions/` setara dengan akses penuh ke akun Telegram tersebut.
  Siapa pun yang menyalin file itu bisa memakai akun Anda. Sudah masuk
  `.gitignore`; jangan pernah membagikannya.

---

## 6. Yang tetap menjadi tanggung jawab Anda

Framework hanya menegakkan hal yang bisa diperiksa secara teknis. Sisanya di luar
kendalinya:

- Apakah anggota komunitas Anda benar-benar ingin dipindahkan.
- Apakah pemindahan sesuai aturan Telegram ToS di wilayah Anda.
- Apakah ada kewajiban perlindungan data yang berlaku (mis. GDPR) untuk daftar
  member yang Anda simpan.

Praktik yang disarankan: umumkan rencana pemindahan di grup lama sebelum mulai,
sediakan tautan undangan agar orang bisa bergabung sendiri, dan pakai invite
otomatis hanya untuk yang tidak merespons.
