# Shared by start_jarvis.sh, stop_jarvis.sh, status_jarvis.sh and scripts/setup_linux.sh (sourced, not run).
#
# Ownership: JARVIS's own processes are started with JARVIS_ROOT=<this folder> in their environment, which
# every child inherits (backend, overlay, WhatsApp bridge). Stopping JARVIS means stopping exactly the
# processes that carry it. Ollama gets JARVIS_OLLAMA_ROOT instead, so it is only stopped when JARVIS started it.

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV="$JARVIS_DIR/.venv"
PY="$VENV/bin/python"
TOOLS="$JARVIS_DIR/.tools"
NODE_DIR="$TOOLS/node"
OLLAMA_BIN="$TOOLS/ollama/bin/ollama"
DATA_DIR="$JARVIS_DIR/data"
RUN_DIR="$DATA_DIR/runtime"
LOG_DIR="$DATA_DIR/logs"
CACHE_DIR="$DATA_DIR/cache"
ELECTRON_BIN="$JARVIS_DIR/ui/node_modules/electron/dist/electron"
OLLAMA_ADDR="127.0.0.1:11434"
OLLAMA_URL="http://$OLLAMA_ADDR"
# Point this at an existing model folder to reuse models instead of downloading them again
OLLAMA_MODELS_DIR="${JARVIS_OLLAMA_MODELS:-$DATA_DIR/models/ollama}"

# JARVIS should behave the same whichever terminal starts it. A terminal inside VS Code carries
# ELECTRON_RUN_AS_NODE=1 (the overlay would start as plain Node) and VS Code's own variables, and a snap's
# terminal (VS Code from the Snap Store) carries the snap's library paths, which make GNOME apps opened from
# it crash (libpthread from /snap/core20). Use the system's environment instead. Only this script's
# process is affected, not your terminal.
use_system_env() {
  local v name
  unset ELECTRON_RUN_AS_NODE ELECTRON_NO_ATTACH_CONSOLE CHROME_DESKTOP
  for v in $(compgen -e | grep '^VSCODE_'); do unset "$v"; done
  [ -n "${SNAP_NAME:-}" ] || return 0
  for v in $(compgen -e | grep '_VSCODE_SNAP_ORIG$'); do
    name=${v%_VSCODE_SNAP_ORIG}
    if [ -n "${!v}" ]; then export "$name=${!v}"; else unset "$name"; fi
    unset "$v"
  done
  unset GTK_PATH GTK_EXE_PREFIX GTK_IM_MODULE_FILE GIO_MODULE_DIR GSETTINGS_SCHEMA_DIR LOCPATH \
        GDK_PIXBUF_MODULE_FILE GDK_PIXBUF_MODULEDIR GIO_LAUNCHED_DESKTOP_FILE GIO_LAUNCHED_DESKTOP_FILE_PID
  for v in $(compgen -e | grep '^SNAP'); do unset "$v"; done
}

refuse_root() {
  if [ "$(id -u)" -eq 0 ]; then
    echo "Don't run JARVIS as root or with sudo. Run it as yourself." >&2
    exit 1
  fi
}

ensure_dirs() {
  mkdir -p "$RUN_DIR/tmp" "$LOG_DIR" "$CACHE_DIR" "$DATA_DIR/models" "$DATA_DIR/ollama-home"
  chmod 700 "$DATA_DIR" 2>/dev/null || true
}

# Environment for JARVIS's own processes only (never exported into this shell). The user's original values
# are passed along as JARVIS_ORIG_<name> so apps that JARVIS opens for the user get them back.
jarvis_env() {
  local v
  JARVIS_ENV=()
  for v in PATH XDG_CACHE_HOME HF_HOME TMPDIR npm_config_cache electron_config_cache PIP_CACHE_DIR PIP_REQUIRE_VIRTUALENV; do
    if [ -n "${!v+x}" ]; then JARVIS_ENV+=("JARVIS_ORIG_$v=${!v}"); fi
  done
  JARVIS_ENV+=(
    "JARVIS_ROOT=$JARVIS_DIR"
    "PATH=$NODE_DIR/bin:$PATH"
    "XDG_CACHE_HOME=$CACHE_DIR"
    "HF_HOME=$CACHE_DIR/huggingface"
    "TMPDIR=$RUN_DIR/tmp"
    "npm_config_cache=$CACHE_DIR/npm"
    "electron_config_cache=$CACHE_DIR/electron"
    "PIP_CACHE_DIR=$CACHE_DIR/pip"
    "PIP_REQUIRE_VIRTUALENV=1"
  )
}

ollama_env() {
  OLLAMA_ENV=(
    "JARVIS_OLLAMA_ROOT=$JARVIS_DIR"
    "OLLAMA_HOST=$OLLAMA_ADDR"
    "OLLAMA_MODELS=$OLLAMA_MODELS_DIR"
    "HOME=$DATA_DIR/ollama-home"
  )
}

read_pid() { [ -f "$RUN_DIR/$1.pid" ] && tr -dc '0-9' < "$RUN_DIR/$1.pid"; }
pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

env_has() {  # pid NAME=value
  grep -qzxF -- "$2" "/proc/$1/environ" 2>/dev/null
}

pids_with_env() {  # NAME=value -> PIDs of this user's processes whose environment holds it
  grep -lzxF -- "$1" /proc/[0-9]*/environ 2>/dev/null | while IFS=/ read -r _ _ pid _; do
    [ "$pid" != "$$" ] && echo "$pid"
  done
}

# Every process JARVIS started from this folder (not Ollama, not apps it opened for you). The overlay is
# found by its binary instead: Chromium overwrites what /proc shows of its environment.
jarvis_pids() {
  {
    pids_with_env "JARVIS_ROOT=$JARVIS_DIR"
    find /proc/[0-9]*/exe -maxdepth 0 -user "$(id -u)" -lname "$JARVIS_DIR/ui/node_modules/electron/dist/*" 2>/dev/null \
      | while IFS=/ read -r _ _ pid _; do echo "$pid"; done
  } | sort -un
}
ollama_pids() { pids_with_env "JARVIS_OLLAMA_ROOT=$JARVIS_DIR"; }

backend_pid() {  # the running backend's PID, only if it really is this folder's JARVIS
  local pid
  pid=$(read_pid backend)
  if pid_alive "$pid" && env_has "$pid" "JARVIS_ROOT=$JARVIS_DIR"; then echo "$pid"; fi
}

owned_ollama_pid() {  # the Ollama server JARVIS started: its marker, this folder's binary, "serve"
  local pid bin
  bin=$(readlink -f "$OLLAMA_BIN")
  for pid in $(ollama_pids); do
    if [ "$(readlink -f "/proc/$pid/exe" 2>/dev/null)" = "$bin" ] \
       && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep ' serve' >/dev/null; then
      echo "$pid"
      return
    fi
  done
}

ollama_up() { curl -fsS -m 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; }

ollama_has_model() {  # model name, e.g. qwen2.5:3b
  curl -fsS -m 3 "$OLLAMA_URL/api/tags" 2>/dev/null | grep -F "\"name\":\"$1\"" >/dev/null
}

config_value() {  # dotted key from config.yaml + config.local.yaml, e.g. llm.model
  (cd "$JARVIS_DIR/backend" && "$PY" -I -c "
import sys; sys.path.insert(0, '.')
from jarvis.config import load_config
v = load_config()
for k in sys.argv[1].split('.'): v = v[k]
print(v)" "$1" 2>/dev/null)
}

wait_gone() {  # seconds pid...
  local seconds=$1 i p alive
  shift
  for ((i = 0; i < seconds * 10; i++)); do
    alive=0
    for p in "$@"; do pid_alive "$p" && alive=1; done
    [ $alive = 0 ] && return 0
    sleep 0.1
  done
  return 1
}

# Starts the project's own Ollama unless one is already answering on 127.0.0.1:11434.
start_ollama() {
  local pid
  if ollama_up; then
    echo external > "$RUN_DIR/ollama.owner"
    return 0
  fi
  [ -x "$OLLAMA_BIN" ] || return 1
  ollama_env
  (
    cd "$DATA_DIR" || exit 1
    env "${OLLAMA_ENV[@]}" setsid "$OLLAMA_BIN" serve >> "$LOG_DIR/ollama.log" 2>&1 < /dev/null &
    echo $! > "$RUN_DIR/ollama.pid"
  )
  echo jarvis > "$RUN_DIR/ollama.owner"
  for ((i = 0; i < 40; i++)); do
    ollama_up && return 0
    sleep 0.5
  done
  return 2
}

# Stops Ollama only if JARVIS started it and nothing else is using it. Prints what it did.
stop_owned_ollama() {
  local pid others
  pid=$(owned_ollama_pid)
  if [ -z "$pid" ]; then
    [ "$(cat "$RUN_DIR/ollama.owner" 2>/dev/null)" = external ] && echo "Ollama was already running before JARVIS; left running."
    rm -f "$RUN_DIR/ollama.pid" "$RUN_DIR/ollama.owner"
    return 0
  fi
  others=$(ss -Htn state established "( dport = :11434 )" 2>/dev/null | wc -l)
  if [ "$others" -gt 0 ]; then
    echo "Ollama (started by JARVIS) is still being used by another program ($others connection(s)); left running."
    echo "  Stop it later with ./stop_jarvis.sh once that program is done."
    return 0
  fi
  kill -TERM "$pid" 2>/dev/null
  wait_gone 8 "$pid" || kill -KILL "$pid" 2>/dev/null
  local rest
  rest=$(ollama_pids)
  if [ -n "$rest" ]; then
    kill -TERM $rest 2>/dev/null
    wait_gone 3 $rest || kill -KILL $(ollama_pids) 2>/dev/null
  fi
  rm -f "$RUN_DIR/ollama.pid" "$RUN_DIR/ollama.owner"
  echo "Stopped Ollama (it was started by JARVIS)."
}

# Leftovers from a crash. Only called once no JARVIS process is running: removing the lock file of a
# running JARVIS would let a second one start beside it.
clean_stale_runtime() {
  rm -f "$RUN_DIR/backend.pid" "$RUN_DIR/instance" "$RUN_DIR/backend.lock"
  rm -f "$DATA_DIR/electron/"Singleton* 2>/dev/null
  rm -rf "$RUN_DIR/tmp"
  rm -f "$DATA_DIR/whatsapp-media/"*.ogg "$DATA_DIR/whatsapp-qr.png" "$DATA_DIR/whatsapp-link.html" 2>/dev/null
  mkdir -p "$RUN_DIR/tmp"
}

describe_pids() {  # pid... -> "1234 electron, 1240 node"
  local p out=()
  for p in "$@"; do out+=("$p $(cat "/proc/$p/comm" 2>/dev/null || echo '?')"); done
  local IFS=,
  echo "${out[*]}"
}
