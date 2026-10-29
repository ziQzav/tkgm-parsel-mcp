#!/usr/bin/env bash
# parsel-imar v3 — opsiyonel bağımlılık kurulumu (idempotent).
# Kullanım:
#   ./setup.sh                  # PDF/PNG/birleştirme için gerekli paketler
#   ./setup.sh --with-browser   # + playwright ve chromium (kaynak_sayfa)
set -uo pipefail

WITH_BROWSER=0
for arg in "$@"; do
  case "$arg" in
    --with-browser) WITH_BROWSER=1 ;;
    -h|--help)
      sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Bilinmeyen argüman: $arg (kullanım: ./setup.sh [--with-browser])" >&2; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "HATA: python3 bulunamadı. Python 3.8+ kurun veya PYTHON=/yol/python3 ./setup.sh" >&2
  exit 1
fi

VER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)'; then
  echo "HATA: Python $VER bulundu, en az 3.8 gerekli." >&2
  exit 1
fi
echo "Python $VER: OK ($("$PY" -c 'import sys;print(sys.executable)'))"

HAS_PIP="$("$PY" -c 'import importlib.util,sys; print(1 if importlib.util.find_spec("pip") else 0)')"
if [ "$HAS_PIP" != "1" ]; then
  echo "HATA: pip bulunamadı ($PY -m pip). 'python3 -m ensurepip --user' veya dağıtımın pip paketini kurun." >&2
  exit 1
fi

IN_VENV="$("$PY" -c 'import sys; print(1 if sys.prefix != getattr(sys,"base_prefix",sys.prefix) else 0)')"
if [ "$IN_VENV" = "1" ]; then
  PIP_FLAGS=()
  echo "Sanal ortam algılandı: --user kullanılmayacak."
else
  PIP_FLAGS=(--user)
fi

PKGS=(cairocffi cairosvg pypdf)
EKSIK=()
for pkg in "${PKGS[@]}"; do
  if "$PY" -c "import $pkg" >/dev/null 2>&1; then
    echo "  $pkg: kurulu (atlandı)"
  else
    EKSIK+=("$pkg")
  fi
done

if [ ${#EKSIK[@]} -gt 0 ]; then
  echo "Kuruluyor: ${EKSIK[*]}"
  if ! "$PY" -m pip install "${PIP_FLAGS[@]}" "${EKSIK[@]}"; then
    echo "UYARI: bazı paketler kurulamadı. Ayrıntı için: $PY -m pip install ${PIP_FLAGS[*]} ${EKSIK[*]}" >&2
  fi
fi

if [ "$WITH_BROWSER" = "1" ]; then
  if "$PY" -c "import playwright" >/dev/null 2>&1; then
    echo "  playwright: kurulu (atlandı)"
  else
    echo "playwright kuruluyor..."
    "$PY" -m pip install "${PIP_FLAGS[@]}" playwright || \
      echo "UYARI: playwright kurulamadı (kaynak_sayfa 'bagimlilik_yok' döner)." >&2
  fi
  if command -v playwright >/dev/null 2>&1; then
    playwright install chromium || echo "UYARI: chromium indirilemedi; kaynak_sayfa çalışmayabilir." >&2
  else
    "$PY" -m playwright install chromium 2>/dev/null || \
      echo "UYARI: 'playwright install chromium' çalıştırılamadı." >&2
  fi
fi

echo "Doğrulama:"
eksik=0
for pkg in "${PKGS[@]}"; do
  if "$PY" -c "import $pkg" >/dev/null 2>&1; then
    echo "  OK   $pkg"
  else
    echo "  EKSİK $pkg  (kurulum: $PY -m pip install ${PIP_FLAGS[*]} $pkg)" >&2
    eksik=$((eksik+1))
  fi
done
if [ "$WITH_BROWSER" = "1" ]; then
  if "$PY" -c "import playwright" >/dev/null 2>&1; then
    echo "  OK   playwright"
  else
    echo "  EKSİK playwright  (kurulum: $PY -m pip install ${PIP_FLAGS[*]} playwright)" >&2
    eksik=$((eksik+1))
  fi
fi

if [ "$eksik" -gt 0 ]; then
  echo "SONUÇ: $eksik paket eksik. Çekirdek araçlar (parsel, imar_sorgula, kapsam, liste, cizim) çalışır; PDF/PNG/kaynak_sayfa atlanır." >&2
  exit 1
fi
echo "SONUÇ: tüm opsiyonel bağımlılıklar hazır."
