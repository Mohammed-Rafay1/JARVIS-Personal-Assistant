#!/usr/bin/env bash
# Start JARVIS by hand (Ubuntu). Runs in this terminal: Ctrl+C, closing the terminal or ./stop_jarvis.sh
# stops everything. Nothing starts at login, and nothing restarts after a crash.
#   ./start_jarvis.sh             normal start (overlay, voice, WhatsApp remote if enabled)
#   ./start_jarvis.sh --selftest  check every component and exit
#   ./start_jarvis.sh --calibrate tune "Hey Jarvis" and the mic level to your voice
#   ./start_jarvis.sh --no-ui     console mode: type commands here
set -u
source "$(dirname "$(readlink -f "$0")")/scripts/linux_common.sh"
refuse_root
use_system_env

interactive=0
for a in "$@"; do
  case "$a" in --selftest|--calibrate|--no-ui) interactive=1 ;; esac
done

fail() { echo "  ✗ $*" >&2; problems=1; }
problems=0
[ -x "$PY" ] || fail "Python environment missing. Run: scripts/setup_linux.sh"
if [ "${1:-}" != --selftest ]; then  # the self-test reports these itself
  ldconfig -p 2>/dev/null | grep 'libportaudio\.so\.2' >/dev/null || fail "PortAudio missing (microphone). Run: sudo apt install libportaudio2"
  ls "$DATA_DIR/models/piper/"*.onnx >/dev/null 2>&1 || fail "Voice model missing. Run: scripts/setup_linux.sh"
fi
if [ $interactive = 0 ]; then
  [ -x "$ELECTRON_BIN" ] || fail "Overlay (Electron) missing. Run: scripts/setup_linux.sh"
  [ -n "${DISPLAY:-}" ] || fail "No desktop display found. Start JARVIS from a terminal in your desktop session."
  [ "${XDG_SESSION_TYPE:-x11}" = wayland ] && echo "  ! Wayland session: the hotkey, orb position and closing apps work best on 'Ubuntu on Xorg'."
fi
[ $problems = 0 ] || exit 1

if pid=$(backend_pid) && [ -n "$pid" ]; then
  echo "JARVIS is already running (backend PID $pid). Stop it with ./stop_jarvis.sh"
  exit 0
fi
leftovers=$(jarvis_pids)
if [ -n "$leftovers" ]; then
  echo "Cleaning up JARVIS processes left by an earlier run: $(describe_pids $leftovers)"
  "$JARVIS_DIR/stop_jarvis.sh" --quiet
fi
ensure_dirs
clean_stale_runtime

model=$(config_value llm.model)
start_ollama
case $? in
  0) if [ "$(cat "$RUN_DIR/ollama.owner" 2>/dev/null)" = external ]; then
       echo "• Ollama: already running on $OLLAMA_ADDR (not started by JARVIS; it will be left running)"
     else
       echo "• Ollama: started for JARVIS on $OLLAMA_ADDR (stopped again with JARVIS)"
     fi
     ollama_has_model "$model" || echo "  ! Model $model is not in Ollama. Run scripts/setup_linux.sh (commands still work; questions need it)."
     ;;
  1) echo "  ! Ollama not installed (run scripts/setup_linux.sh). Commands still work; questions and email writing need it." ;;
  *) echo "  ! Ollama didn't start in 20 s; see data/logs/ollama.log. Commands still work." ;;
esac

instance="$(date +%s)-$$"
echo "$instance" > "$RUN_DIR/instance"
jarvis_env
JARVIS_ENV+=("JARVIS_INSTANCE=$instance")
cd "$JARVIS_DIR/backend" || exit 1

cleanup() {  # bounded (about 25 s at most), and a second Ctrl+C or the closing terminal can't cut it short
  trap '' INT TERM HUP
  "$JARVIS_DIR/stop_jarvis.sh" --quiet
}

if [ $interactive = 1 ]; then
  trap 'true' INT
  env "${JARVIS_ENV[@]}" "$PY" -m jarvis "$@"
  code=$?
  cleanup
  exit $code
fi

env "${JARVIS_ENV[@]}" setsid "$PY" -m jarvis "$@" &
backend=$!
echo "$backend" > "$RUN_DIR/backend.pid"
trap 'echo; echo "Stopping JARVIS…"; cleanup; exit 130' INT TERM HUP
echo "• JARVIS is starting (backend PID $backend). It says \"Systems are online\" in about 20 s."
echo "  Stop: Ctrl+C here, ./stop_jarvis.sh, or Quit in the tray menu. Status: ./status_jarvis.sh"
echo "  Log: data/logs/jarvis.log"
echo
wait "$backend"
code=$?
echo
echo "JARVIS exited (code $code). Cleaning up…"
cleanup
exit $code
