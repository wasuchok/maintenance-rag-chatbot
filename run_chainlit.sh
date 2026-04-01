#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export DEBUG=false

choose_python_bin() {
  if [[ -x "venv/bin/python" ]]; then
    if venv/bin/python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info < (3, 14) else 1)
PY
    then
      echo "venv/bin/python"
      return 0
    fi
  fi

  if [[ -x ".venv312/bin/python" ]]; then
    echo ".venv312/bin/python"
    return 0
  fi

  cat <<'EOF' >&2
Chainlit ในโปรเจกต์นี้มีปัญหาบน Python 3.14

ให้สร้าง venv สำหรับ Chainlit ด้วย Python 3.12 ก่อน:
  /opt/homebrew/bin/python3.12 -m venv .venv312
  .venv312/bin/python -m pip install -r requirements.txt

จากนั้นรันใหม่:
  ./run_chainlit.sh
EOF
  exit 1
}

PYTHON_BIN="$(choose_python_bin)"

exec "$PYTHON_BIN" -m chainlit run chainlit_app.py -w --host "${HOST:-0.0.0.0}" --port "${PORT:-8100}" "$@"
