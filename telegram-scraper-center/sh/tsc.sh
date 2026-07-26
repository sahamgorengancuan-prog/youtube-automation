#!/usr/bin/env bash
# Padanan skrip .bat untuk Linux/macOS.
#
#   ./sh/tsc.sh setup           siapkan venv + dependency + config
#   ./sh/tsc.sh dashboard       buka dashboard
#   ./sh/tsc.sh doctor          periksa kesiapan lingkungan
#   ./sh/tsc.sh validate        cek admin target & source
#   ./sh/tsc.sh plan            susun ulang batch
#   ./sh/tsc.sh status          ringkasan kondisi
#   ./sh/tsc.sh run             jalankan tanpa dashboard
#   ./sh/tsc.sh login <label>   login satu agent (mode live)
#   ./sh/tsc.sh test            jalankan test otomatis
set -euo pipefail

cd "$(dirname "$0")/.."
COMMAND="${1:-dashboard}"
shift || true

activate() {
    [[ -f .venv/bin/activate ]] && source .venv/bin/activate || true
}

case "$COMMAND" in
  setup)
    echo "=== Telegram Scraper Center - Setup ==="
    command -v python3 >/dev/null || { echo "[X] python3 tidak ditemukan"; exit 1; }
    [[ -d .venv ]] || python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt || \
        echo "[!] Sebagian dependency gagal; mode simulate tetap jalan."
    [[ -f config.json ]] || { cp config.example.json config.json; echo "config.json dibuat."; }
    [[ -f .env ]]        || { cp .env.example .env;               echo ".env dibuat.";       }
    mkdir -p data sessions logs
    echo "Setup selesai. Edit config.json dan .env, lalu: ./sh/tsc.sh doctor"
    ;;
  dashboard) activate; exec python -m tsc dashboard "$@" ;;
  doctor)    activate; exec python -m tsc doctor "$@" ;;
  validate)  activate; exec python -m tsc validate "$@" ;;
  plan)      activate; exec python -m tsc plan "$@" ;;
  status)    activate; exec python -m tsc status "$@" ;;
  run)       activate; exec python -m tsc run "$@" ;;
  login)
    activate
    [[ $# -ge 1 ]] || { echo "Pemakaian: ./sh/tsc.sh login <label-agent>"; exit 1; }
    exec python -m tsc login --agent "$1"
    ;;
  test)      activate; exec python -m pytest tests -q "$@" ;;
  *)
    echo "Perintah tidak dikenal: $COMMAND"
    sed -n '2,15p' "$0"
    exit 1
    ;;
esac
