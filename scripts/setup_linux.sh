#!/usr/bin/env bash
# Install everything JARVIS needs on Ubuntu, inside this folder only: Python packages (.venv), Node.js and
# Ollama (.tools), npm packages (node_modules), models (data/models). No sudo, nothing global, no services.
# Re-run any time: finished steps are skipped.
#   scripts/setup_linux.sh               install what's missing
#   scripts/setup_linux.sh --update      also reinstall the pinned packages and refresh the language model
#   scripts/setup_linux.sh --no-selftest skip the self-test at the end (it records 1 s from the microphone)
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/linux_common.sh"
refuse_root
use_system_env

NODE_VERSION=24.22.0
OLLAMA_VERSION=0.40.7

update=0 selftest=1
for a in "$@"; do
  case "$a" in
    --update) update=1 ;;
    --no-selftest) selftest=0 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;36m[%s] %s\033[0m\n' "$1" "$2"; }
ok() { echo "  ✓ $*"; }
die() { echo "  ✗ $*" >&2; exit 1; }

cd "$JARVIS_DIR"
DL="$CACHE_DIR/downloads"
ensure_dirs
mkdir -p "$DL" "$TOOLS"
jarvis_env
SETUP_ENV=("npm_config_update_notifier=false")
for e in "${JARVIS_ENV[@]}"; do  # same caches as JARVIS, but without its ownership marker
  [[ $e == JARVIS_ROOT=* ]] || SETUP_ENV+=("$e")
done
run() { env "${SETUP_ENV[@]}" "$@"; }

step 1/7 "Checking what's already on this laptop"
command -v python3.12 >/dev/null || die "Python 3.12 is required (it ships with Ubuntu 24.04)."
for t in curl tar xz zstd sha256sum; do command -v "$t" >/dev/null || die "$t is missing."; done
ok "python3.12, curl, tar, xz, zstd"
if ldconfig -p | grep 'libportaudio\.so\.2' >/dev/null; then
  ok "PortAudio (microphone library)"
else
  echo "  ! PortAudio is missing. The microphone needs it. Run this once yourself, then start JARVIS:"
  echo "      sudo apt install libportaudio2"
fi
free_gb=$(df -Pk "$JARVIS_DIR" | awk 'NR==2 {print int($4 / 1048576)}')
[ "$free_gb" -ge 8 ] || die "Only ${free_gb} GB free; about 8 GB is needed while installing."
ok "${free_gb} GB free disk space"

step 2/7 "Python packages (.venv)"
if [ ! -x "$PY" ]; then
  python3.12 -m venv --without-pip "$VENV"  # Ubuntu's Python has no ensurepip without the python3.12-venv package
  ok "created .venv"
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$DL/get-pip.py"
  run "$PY" "$DL/get-pip.py" --quiet --no-warn-script-location
  rm -f "$DL/get-pip.py"
  ok "pip installed inside .venv"
fi
OWW=$(grep -E '^openwakeword==' backend/requirements.txt | cut -d';' -f1 | tr -d ' ')
if [ $update = 1 ] || ! "$PY" -c 'import faster_whisper, piper, kokoro_onnx, pulsectl, Xlib, openwakeword, sounddevice' 2>/dev/null; then
  run "$PY" -m pip install --quiet --upgrade pip
  run "$PY" -m pip install --quiet -r backend/requirements.txt -r backend/requirements-linux.txt
  # openWakeWord lists tflite-runtime for Linux, which has no Python 3.12 build; Jarvis uses its ONNX models
  run "$PY" -m pip install --quiet --no-deps "$OWW"
fi
ok "Python packages ($("$PY" -m pip list --format=freeze 2>/dev/null | wc -l) installed in .venv)"

step 3/7 "Node.js $NODE_VERSION (.tools/node; your nvm is not touched)"
if [ "$("$NODE_DIR/bin/node" --version 2>/dev/null || true)" != "v$NODE_VERSION" ]; then
  f="node-v$NODE_VERSION-linux-x64.tar.xz"
  curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/$f" -o "$DL/$f"
  curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt" -o "$DL/node-sums.txt"
  want=$(awk -v f="$f" '$2 == f {print $1}' "$DL/node-sums.txt")
  [ -n "$want" ] && [ "$(sha256sum "$DL/$f" | cut -d' ' -f1)" = "$want" ] || die "Node.js download failed its checksum"
  rm -rf "$NODE_DIR.new" && mkdir -p "$NODE_DIR.new"
  tar -xJf "$DL/$f" -C "$NODE_DIR.new" --strip-components=1
  rm -rf "$NODE_DIR" && mv "$NODE_DIR.new" "$NODE_DIR"
  rm -f "$DL/$f" "$DL/node-sums.txt"
fi
ok "node $("$NODE_DIR/bin/node" --version)"

step 4/7 "Overlay and WhatsApp bridge (npm packages inside ui/ and whatsapp-bridge/)"
for d in ui whatsapp-bridge; do
  if [ $update = 1 ] || [ ! -d "$d/node_modules" ]; then
    (cd "$d" && run npm ci --no-audit --no-fund --loglevel=error)
  fi
  ok "$d"
done
[ -x "$ELECTRON_BIN" ] || die "Electron didn't install; see the npm output above."
ok "electron $(cat ui/node_modules/electron/dist/version 2>/dev/null)"

step 5/7 "Speech and voice models (data/models)"
(cd backend && run "$PY" ../scripts/download_models.py)
acc=$(config_value stt.accurate_model)
if [ -n "$acc" ]; then
  (cd backend && run "$PY" -c "
import sys
from faster_whisper import WhisperModel
from jarvis.config import MODELS
WhisperModel(sys.argv[1], device='cpu', compute_type='int8', download_root=str(MODELS / 'whisper'))" "$acc")
  ok "Whisper $acc (re-checks unclear speech)"
fi

step 6/7 "Ollama $OLLAMA_VERSION and the language model"
model=$(config_value llm.model)
if ollama_up && [ -z "$(owned_ollama_pid)" ]; then
  ok "an Ollama is already running on $OLLAMA_ADDR (not this project's); JARVIS will use it"
  if ollama_has_model "$model"; then ok "it has $model"; else echo "  ! It doesn't have $model. Run: ollama pull $model"; fi
else
  if [ "$(cat "$TOOLS/ollama/.version" 2>/dev/null || true)" != "$OLLAMA_VERSION" ]; then
    f=ollama-linux-amd64.tar.zst
    base="https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION"
    echo "  downloading Ollama (about 1.5 GB; the GPU parts are left out after checking it)…"
    curl -fL --progress-bar "$base/$f" -o "$DL/$f"
    curl -fsSL "$base/sha256sum.txt" -o "$DL/ollama-sums.txt"
    want=$(awk -v f="$f" '{sub(/^\.\//, "", $2)} $2 == f {print $1}' "$DL/ollama-sums.txt")
    [ -n "$want" ] && [ "$(sha256sum "$DL/$f" | cut -d' ' -f1)" = "$want" ] || die "Ollama download failed its checksum"
    rm -rf "$TOOLS/ollama.new" && mkdir -p "$TOOLS/ollama.new"
    tar --zstd -xf "$DL/$f" -C "$TOOLS/ollama.new" \
      --exclude='lib/ollama/cuda_*' --exclude='lib/ollama/rocm*' --exclude='lib/ollama/vulkan*' --exclude='lib/ollama/mlx*'
    echo "$OLLAMA_VERSION" > "$TOOLS/ollama.new/.version"
    rm -rf "$TOOLS/ollama" && mv "$TOOLS/ollama.new" "$TOOLS/ollama"
    rm -f "$DL/$f" "$DL/ollama-sums.txt"
  fi
  ok "ollama $OLLAMA_VERSION in .tools/ollama (CPU only)"
  name=${model%%:*} tag=${model#*:}
  if [ $update = 1 ] || [ ! -f "$OLLAMA_MODELS_DIR/manifests/registry.ollama.ai/library/$name/$tag" ]; then
    start_ollama || die "Ollama didn't start; see data/logs/ollama.log"
    ollama_env
    env "${OLLAMA_ENV[@]}" "$OLLAMA_BIN" pull "$model"
    stop_owned_ollama >/dev/null
  fi
  ok "$model in ${OLLAMA_MODELS_DIR#$JARVIS_DIR/}"
fi

step 7/7 "Settings and permissions"
if [ ! -f config.local.yaml ]; then
  cp config.local.example.yaml config.local.yaml
  ok "created config.local.yaml (this laptop only, not committed): /var/www projects excluded"
else
  ok "config.local.yaml kept as it is"
fi
mkdir -p secrets
chmod 700 secrets "$DATA_DIR"
[ -d "$DATA_DIR/whatsapp-auth" ] && chmod 700 "$DATA_DIR/whatsapp-auth"
ok "secrets/ and data/ are private to you (chmod 700)"

if [ $selftest = 1 ]; then
  step "✓" "Self-test (records 1 s from the microphone)"
  "$JARVIS_DIR/start_jarvis.sh" --selftest || true
fi
printf '\nDone. Start JARVIS with ./start_jarvis.sh  (stop: ./stop_jarvis.sh, status: ./status_jarvis.sh)\n'
