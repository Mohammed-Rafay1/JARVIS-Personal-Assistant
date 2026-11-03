#!/usr/bin/env bash
# Stop JARVIS completely (Ubuntu): overlay, backend, WhatsApp bridge, microphone, and Ollama if JARVIS
# started it. Only processes JARVIS started from this folder are touched. Safe to run any number of times.
set -u
source "$(dirname "$(readlink -f "$0")")/scripts/linux_common.sh"
refuse_root
trap '' HUP  # finish even if the terminal closes

quiet=0
[ "${1:-}" = --quiet ] && quiet=1
say() { [ $quiet = 1 ] || echo "$@"; }

backend=$(backend_pid)
running=$(jarvis_pids)
if [ -n "$backend" ]; then
  say "Stopping the JARVIS backend (PID $backend): mic off, overlay closing…"
  kill -TERM "$backend" 2>/dev/null
  wait_gone 10 "$backend" || say "  The backend didn't stop in 10 s; stopping it now."
fi

pids=$(jarvis_pids)
if [ -n "$pids" ]; then
  say "Stopping remaining JARVIS processes: $(describe_pids $pids)"
  kill -TERM $pids 2>/dev/null
  wait_gone 3 $pids
  pids=$(jarvis_pids)
  [ -n "$pids" ] && kill -KILL $pids 2>/dev/null && wait_gone 2 $pids
fi

msg=$(stop_owned_ollama)
[ -n "$msg" ] && echo "$msg"

if [ -f "$RUN_DIR/ducked.json" ] && [ -x "$PY" ]; then  # volumes JARVIS lowered before a crash
  (cd "$JARVIS_DIR/backend" && "$PY" -I -c "import sys; sys.path.insert(0, '.'); from jarvis.linux import restore_ducked; restore_ducked()") \
    && say "Restored other apps' volume."
fi

left=$(jarvis_pids)
if [ -z "$left" ]; then
  clean_stale_runtime
  if [ -z "$running" ]; then
    say "JARVIS isn't running. No JARVIS processes remain."
  else
    say "JARVIS is stopped. No JARVIS processes remain, and the microphone is released."
  fi
  exit 0
fi
echo "Some JARVIS processes are still running: $(describe_pids $left)" >&2
echo "Run ./stop_jarvis.sh again, or check ./status_jarvis.sh." >&2
exit 1
