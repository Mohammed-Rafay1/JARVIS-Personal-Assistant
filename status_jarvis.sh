#!/usr/bin/env bash
# Is JARVIS running, and is anything of it left behind? (Ubuntu). Read-only: changes nothing.
set -u
source "$(dirname "$(readlink -f "$0")")/scripts/linux_common.sh"

on() { printf '  %-18s \033[32m%s\033[0m\n' "$1" "$2"; }
off() { printf '  %-18s %s\n' "$1" "$2"; }
bad() { printf '  %-18s \033[33m%s\033[0m\n' "$1" "$2"; }

pids=$(jarvis_pids)
backend=$(backend_pid)
ui=() bridge=() others=()
for p in $pids; do
  [ "$p" = "$backend" ] && continue
  exe=$(readlink -f "/proc/$p/exe" 2>/dev/null)
  if [ "$exe" = "$(readlink -f "$ELECTRON_BIN")" ] || [ "$(cat "/proc/$p/comm" 2>/dev/null)" = chrome_crashpad ]; then
    ui+=("$p")
  elif tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q 'whatsapp-bridge/index.mjs'; then
    bridge+=("$p")
  else
    others+=("$p")
  fi
done

echo "JARVIS ($JARVIS_DIR)"
if [ -n "$backend" ]; then on "JARVIS" "running"; else off "JARVIS" "not running"; fi
if [ -n "$backend" ]; then on "Backend" "running (PID $backend)"; else off "Backend" "not running"; fi
if [ ${#ui[@]} -gt 0 ]; then on "Overlay (Electron)" "running (${#ui[@]} processes)"; else off "Overlay (Electron)" "not running"; fi
if [ ${#bridge[@]} -gt 0 ]; then on "WhatsApp bridge" "running (PID ${bridge[*]})"; else off "WhatsApp bridge" "not running"; fi

mic="not in use by JARVIS"
if [ -n "$pids" ] && command -v pw-dump >/dev/null; then
  if pw-dump 2>/dev/null | python3 -I -c '
import json, sys
mine = set(sys.argv[1].split())
objects = json.load(sys.stdin)
props = {o.get("id"): (o.get("info") or {}).get("props") or {} for o in objects}
for p in props.values():
    pid = p.get("application.process.id") or props.get(p.get("client.id"), {}).get("application.process.id")
    if p.get("media.class") == "Stream/Input/Audio" and str(pid) in mine:
        sys.exit(0)
sys.exit(1)' "$pids"; then mic="IN USE by JARVIS (listening)"; fi
fi
if [ "${mic#IN USE}" != "$mic" ]; then on "Microphone" "$mic"; else off "Microphone" "$mic"; fi

model=$(config_value llm.model 2>/dev/null)
owned=$(owned_ollama_pid)
if [ -n "$owned" ]; then
  on "Ollama" "running, started by JARVIS (PID $owned)"
elif ollama_up; then
  off "Ollama" "running, not started by JARVIS (left alone)"
else
  off "Ollama" "not running"
fi

echo
echo "Requirements"
missing=0
need() { if eval "$2"; then off "$1" "ok"; else bad "$1" "MISSING: $3"; missing=1; fi; }
need "Python env" '[ -x "$PY" ]' "run scripts/setup_linux.sh"
need "PortAudio" "ldconfig -p 2>/dev/null | grep 'libportaudio\.so\.2' >/dev/null" "sudo apt install libportaudio2"
need "Voice model" 'ls "$DATA_DIR/models/piper/"*.onnx >/dev/null 2>&1' "run scripts/setup_linux.sh"
need "Speech model" '[ -n "$(ls -A "$DATA_DIR/models/whisper" 2>/dev/null)" ]' "run scripts/setup_linux.sh"
need "Overlay" '[ -x "$ELECTRON_BIN" ]' "run scripts/setup_linux.sh"
need "Node.js" '[ -x "$NODE_DIR/bin/node" ]' "run scripts/setup_linux.sh (needed for WhatsApp)"
need "Ollama" '[ -x "$OLLAMA_BIN" ] || ollama_up' "run scripts/setup_linux.sh"
if ollama_up && [ -n "$model" ]; then
  need "LLM $model" 'ollama_has_model "$model"' "run scripts/setup_linux.sh"
elif [ -n "$model" ] && [ -d "$OLLAMA_MODELS_DIR/manifests" ]; then
  name=${model%%:*} tag=${model#*:}
  need "LLM $model" '[ -f "$OLLAMA_MODELS_DIR/manifests/registry.ollama.ai/library/$name/$tag" ]' "run scripts/setup_linux.sh"
fi

echo
echo "Left-over check"
if [ -z "$backend" ] && [ -n "$pids" ]; then
  bad "JARVIS processes" "still running without the backend: $(describe_pids $pids). Run ./stop_jarvis.sh"
elif [ -z "$pids" ]; then
  off "JARVIS processes" "none"
else
  off "JARVIS processes" "$(echo $pids | wc -w) (all part of the running JARVIS)"
fi
[ ${#others[@]} -gt 0 ] && off "Other JARVIS procs" "$(describe_pids "${others[@]}")"

auto=$( { grep -lis jarvis ~/.config/autostart/*.desktop /etc/xdg/autostart/*.desktop 2>/dev/null
          ls ~/.config/systemd/user 2>/dev/null | grep -i jarvis
          systemctl --user list-unit-files 2>/dev/null | grep -i jarvis
          crontab -l 2>/dev/null | grep -i jarvis; } | sort -u)
if [ -z "$auto" ]; then off "Autostart" "none (JARVIS never starts by itself)"; else bad "Autostart" "FOUND: $auto"; fi
exit $missing
