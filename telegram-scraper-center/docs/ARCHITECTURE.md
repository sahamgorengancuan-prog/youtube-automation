# Arsitektur

## Peta modul

```
config.py       Muat & validasi config.json + .env
models.py       Enum dan dataclass inti (Agent, Member, Batch, TriageDecision)
store.py        SQLite: satu-satunya sumber kebenaran, tahan restart
telegram/
  base.py       Kontrak TelegramClient + taksonomi error stabil
  telethon_client.py  Implementasi asli — hanya menerjemahkan error
  simulator.py  Implementasi tiruan untuk demo & test
guard.py        Gerbang validasi admin (target + source)
batching.py     Fungsi murni: pemilihan ukuran batch & pembagian ke agent
governor.py     Kuota, jeda, flood wait, limitasi — per agent
llm.py          Triage: aturan deterministik + lapis Claude
notifier.py     Bot Telegram untuk kabar ke operator
orchestrator.py Perekat: menjalankan pipeline, satu task asyncio per agent
dashboard/      Server HTTP stdlib + UI satu halaman
```

## Alur eksekusi

```
connect_all()      login tiap agent; yang gagal → DISABLED (run tetap lanjut)
      ↓
validate()         Guard: agent admin di target? source dikelola sendiri?
      ↓            gagal → campaign ABORTED, nol invite terkirim
scrape_sources()   ambil member per source, berurutan, dedup lintas source
      ↓
build_plan()       member layak → dibagi rata ke agent → dipotong per sesi
      ↓
_run_workers()     satu task per agent, berjalan paralel
```

Setiap worker mengulang:

```
ambil batch berikutnya  →  governor.check()  →  invite  →  catat hasil
                              ↑                    ↓
                          tunggu jeda        gagal → triage
```

## Keputusan desain

**SQLite sebagai state, bukan memori.** Setiap perubahan status member, batch,
dan agent langsung ditulis. Proses bisa dimatikan kapan saja; menjalankan ulang
akan melanjutkan dari antrean yang sama. Batch yang berstatus `running` saat
proses mati akan diambil lagi oleh agent yang sama.

**Adapter Telegram di balik protokol.** `orchestrator`, `governor`, dan
`batching` tidak tahu Telethon itu apa. Konsekuensinya: seluruh logika
penjadwalan bisa diuji offline lewat `SimulatedClient`, dan mengganti pustaka
MTProto cukup menyentuh satu file.

**Error dinormalisasi di perbatasan.** `telethon_client.translate_error`
memetakan exception Telethon ke kelas error milik framework dengan `code` yang
stabil. Kode inilah yang dipakai taksonomi dashboard, aturan triage, dan test —
sehingga pembaruan Telethon tidak merembet ke mana-mana.

**Fungsi murni untuk yang perlu diuji ketat.** `batching.py` dan `governor.py`
tidak melakukan I/O; `governor` menerima `clock` yang bisa disuntik. Test kuota
harian tidak perlu menunggu 24 jam.

**Waktu bisa disuntik sampai ke orchestrator.** `Engine` menerima `clock` dan
`sleep`. Di produksi keduanya asli; di test dipakai jam virtual, sehingga flood
wait 120 detik benar-benar diperlakukan 120 detik tanpa menunggu.

**Triage dua lapis.** Aturan deterministik menangani semua kasus yang sudah
dikenali dan selalu jalan tanpa jaringan. LLM hanya dipanggil ketika aturan
mengembalikan `ESCALATE`, dibatasi kuota panggilan per jam, dan hasilnya
divalidasi terhadap skema. Kalau LLM tidak tersedia, gagal, menolak, atau
menjawab di luar skema — framework memakai keputusan aturan. **Tidak ada jalur
yang membuat campaign bergantung pada ketersediaan LLM.**

**Dashboard tanpa dependency.** `http.server` + SSE. Alasannya praktis: target
pemakai menjalankan ini di laptop Windows lewat berkas `.bat`; `pip install`
yang gagal tidak boleh membuat dashboard mati.

## Model konkurensi

- Satu event loop asyncio, satu task per agent → paralelisme sebatas jumlah agent.
- Tidak ada kunci antar-worker: tiap worker hanya menyentuh batch miliknya
  sendiri, dan SQLite dijalankan mode WAL dengan kunci tulis per proses.
- Dashboard berjalan di thread terpisah dengan koneksi SQLite sendiri
  (`threading.local`), hanya membaca — kecuali perintah kontrol yang
  di-*marshal* ke event loop mesin lewat `call_soon_threadsafe`.

## Menambah kemampuan baru

| Kebutuhan | Sentuh berkas |
|---|---|
| Menangani error Telegram baru | `telegram/base.py` (kelas + `code`), `llm.py` (aturan) |
| Mengubah strategi pembagian batch | `batching.py` — fungsi murni, mudah diuji |
| Mengubah kebijakan laju | `governor.py` + `Limits` di `config.py` |
| Menambah panel dashboard | tambahkan data di `Engine.snapshot()`, render di `app.js` |
| Mengganti pustaka MTProto | implementasi baru sesuai protokol di `telegram/base.py` |
